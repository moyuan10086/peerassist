"""Private operator use cases for first-tenant bootstrap and global identities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from ..errors import Forbidden, IdempotencyConflict, NotFound, StaleVersion
from ..idempotency import canonical_json_digest
from ..models import (
    Action,
    Actor,
    ActorKind,
    AuditEvent,
    CommandRecord,
    ExternalIdentity,
    Organization,
    OrganizationMembership,
    OutboxEvent,
    Role,
    TenantScope,
    mutable_json,
)
from ..ports import UnitOfWorkFactory

Clock = Callable[[], datetime]
UuidFactory = Callable[[], UUID]


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise IdempotencyConflict()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise IdempotencyConflict() from None
    return parsed


def _identity_response(identity: ExternalIdentity) -> dict[str, object]:
    return {
        "id": str(identity.id),
        "user_id": str(identity.user_id),
        "issuer": identity.issuer,
        "subject": identity.subject,
        "verified_claims": mutable_json(identity.verified_claims),
        "last_seen_at": identity.last_seen_at.isoformat(),
        "created_at": identity.created_at.isoformat(),
        "disabled_at": _timestamp(identity.disabled_at),
        "version": identity.version,
    }


def _identity_from_response(response: object) -> ExternalIdentity:
    if not isinstance(response, Mapping):
        raise IdempotencyConflict()
    try:
        claims = response["verified_claims"]
        if not isinstance(claims, Mapping):
            raise ValueError
        return ExternalIdentity(
            UUID(str(response["id"])),
            UUID(str(response["user_id"])),
            str(response["issuer"]),
            str(response["subject"]),
            dict(claims),
            _parse_timestamp(response["last_seen_at"]),
            _parse_timestamp(response["created_at"]),
            _parse_timestamp(response.get("disabled_at")),
            int(response["version"]),
        )
    except (KeyError, TypeError, ValueError):
        raise IdempotencyConflict() from None


def _bootstrap_response(
    organization: Organization,
    membership: OrganizationMembership,
) -> dict[str, object]:
    return {
        "organization": {
            "id": str(organization.id),
            "slug": organization.slug,
            "name": organization.name,
            "status": organization.status,
            "version": organization.version,
            "created_at": organization.created_at.isoformat(),
            "updated_at": organization.updated_at.isoformat(),
        },
        "membership": {
            "id": str(membership.id),
            "organization_id": str(membership.organization_id),
            "user_id": str(membership.user_id),
            "role": membership.role.value,
            "status": membership.status,
            "version": membership.version,
            "created_at": membership.created_at.isoformat(),
            "updated_at": membership.updated_at.isoformat(),
            "revoked_at": _timestamp(membership.revoked_at),
        },
    }


def _bootstrap_from_response(response: object) -> BootstrapOrganizationResult:
    if not isinstance(response, Mapping):
        raise IdempotencyConflict()
    organization = response.get("organization")
    membership = response.get("membership")
    if not isinstance(organization, Mapping) or not isinstance(membership, Mapping):
        raise IdempotencyConflict()
    try:
        return BootstrapOrganizationResult(
            Organization(
                UUID(str(organization["id"])),
                str(organization["slug"]),
                str(organization["name"]),
                str(organization["status"]),
                int(organization["version"]),
                _parse_timestamp(organization["created_at"]),
                _parse_timestamp(organization["updated_at"]),
            ),
            OrganizationMembership(
                UUID(str(membership["id"])),
                UUID(str(membership["organization_id"])),
                UUID(str(membership["user_id"])),
                Role(str(membership["role"])),
                str(membership["status"]),
                int(membership["version"]),
                _parse_timestamp(membership["created_at"]),
                _parse_timestamp(membership["updated_at"]),
                _parse_timestamp(membership.get("revoked_at")),
            ),
        )
    except (KeyError, TypeError, ValueError):
        raise IdempotencyConflict() from None


def _now() -> datetime:
    return datetime.now(UTC)


def _operator(actor: Actor) -> None:
    if actor.kind is not ActorKind.OPERATOR:
        raise Forbidden()


def _command(
    *,
    command_id: UUID,
    scope: TenantScope,
    actor: Actor,
    operation: str,
    idempotency_key: str,
    payload: dict[str, object],
    created_at: datetime,
) -> CommandRecord:
    return CommandRecord(
        command_id,
        scope.organization_id,
        actor.actor_id,
        operation,
        idempotency_key,
        canonical_json_digest(payload),
        created_at,
        project_id=scope.project_id,
    )


@dataclass(frozen=True, slots=True)
class BootstrapOrganization:
    issuer: str
    subject: str
    slug: str
    name: str
    idempotency_key: str
    request_id: str


@dataclass(frozen=True, slots=True)
class BootstrapOrganizationResult:
    organization: Organization
    membership: OrganizationMembership


class BootstrapOrganizationService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock = _now,
        uuid_factory: UuidFactory = uuid4,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._uuid = uuid_factory

    def execute(self, actor: Actor, request: BootstrapOrganization) -> BootstrapOrganizationResult:
        _operator(actor)
        try:
            return self._execute_once(actor, request)
        except ValueError:
            # A concurrent PostgreSQL bootstrap may win a unique organization/slug
            # insert. Re-enter a fresh transaction so the command row can replay.
            try:
                return self._execute_once(actor, request)
            except ValueError:
                raise IdempotencyConflict() from None

    def _execute_once(self, actor: Actor, request: BootstrapOrganization) -> BootstrapOrganizationResult:
        organization_id = uuid5(
            NAMESPACE_URL,
            f"peerassist:bootstrap:{actor.actor_id}:{request.idempotency_key}",
        )
        command_id = uuid5(organization_id, "command")
        membership_id = uuid5(organization_id, f"organization-admin:{request.issuer}:{request.subject}")
        scope = TenantScope(organization_id)
        payload = {
            "issuer": request.issuer,
            "subject": request.subject,
            "slug": request.slug,
            "name": request.name,
        }
        now = self._clock()
        candidate = _command(
            command_id=command_id,
            scope=scope,
            actor=actor,
            operation="organization.bootstrap",
            idempotency_key=request.idempotency_key,
            payload=payload,
            created_at=now,
        )
        with self._uow_factory(actor) as uow:
            existing = uow.commands.get(
                scope,
                actor.actor_id,
                candidate.operation,
                candidate.idempotency_key,
            )
            if existing is not None:
                if existing.payload_digest != candidate.payload_digest:
                    raise IdempotencyConflict()
                if existing.completed_at is not None:
                    return _bootstrap_from_response(existing.response_body)
            identity = uow.users.get_identity(request.issuer, request.subject)
            if identity is None:
                raise NotFound()
            if identity.disabled_at is not None:
                raise Forbidden()
            organization = uow.organizations.get(scope)
            if organization is None:
                organization = Organization(
                    organization_id,
                    request.slug,
                    request.name,
                    "active",
                    1,
                    now,
                    now,
                )
                membership = OrganizationMembership(
                    membership_id,
                    organization_id,
                    identity.user_id,
                    Role.ORGANIZATION_ADMIN,
                    "active",
                    1,
                    now,
                    now,
                )
                uow.organizations.add(scope, organization)
                uow.organizations.save_membership(scope, membership, None)
            reserved = uow.commands.reserve_or_replay(scope, candidate)
            if reserved.payload_digest != candidate.payload_digest:
                raise IdempotencyConflict()
            if reserved.completed_at is not None:
                return _bootstrap_from_response(reserved.response_body)

            membership = uow.organizations.get_membership(scope, identity.user_id)
            if membership is None:
                raise IdempotencyConflict()
            uow.audit.append(
                scope,
                AuditEvent(
                    uuid5(command_id, "audit"),
                    actor.actor_id,
                    organization_id,
                    Action.ORGANIZATION_BOOTSTRAP,
                    "organization",
                    organization_id,
                    "succeeded",
                    request.request_id,
                    now,
                    identity_id=identity.id,
                    command_id=command_id,
                    safe_metadata={},
                ),
            )
            uow.outbox.append(
                scope,
                OutboxEvent(
                    uuid5(command_id, "outbox"),
                    organization_id,
                    "organization",
                    organization_id,
                    1,
                    "organization.bootstrapped",
                    1,
                    {"organization_id": str(organization_id)},
                    now,
                ),
            )
            uow.commands.complete(
                scope,
                replace(
                    reserved,
                    response_status=201,
                    response_body=_bootstrap_response(organization, membership),
                    completed_at=now,
                ),
            )
            uow.commit()
            return BootstrapOrganizationResult(organization, membership)


@dataclass(frozen=True, slots=True)
class ChangeIdentity:
    issuer: str
    subject: str
    expected_version: int
    idempotency_key: str
    request_id: str
    audit_organization_id: UUID


class IdentityOperatorService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock = _now,
        uuid_factory: UuidFactory = uuid4,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._uuid = uuid_factory

    def disable(self, actor: Actor, request: ChangeIdentity) -> ExternalIdentity:
        return self._change(actor, request, unlink=False)

    def unlink(self, actor: Actor, request: ChangeIdentity) -> ExternalIdentity:
        return self._change(actor, request, unlink=True)

    def _change(self, actor: Actor, request: ChangeIdentity, *, unlink: bool) -> ExternalIdentity:
        _operator(actor)
        scope = TenantScope(request.audit_organization_id)
        operation = "identity.unlink" if unlink else "identity.disable"
        payload = {
            "issuer": request.issuer,
            "subject": request.subject,
            "expected_version": request.expected_version,
        }
        command_id = uuid5(
            request.audit_organization_id,
            f"{actor.actor_id}:{operation}:{request.idempotency_key}",
        )
        now = self._clock()
        candidate = _command(
            command_id=command_id,
            scope=scope,
            actor=actor,
            operation=operation,
            idempotency_key=request.idempotency_key,
            payload=payload,
            created_at=now,
        )
        with self._uow_factory(actor) as uow:
            if uow.organizations.get(scope) is None:
                raise NotFound()
            reserved = uow.commands.reserve_or_replay(scope, candidate)
            if reserved.payload_digest != candidate.payload_digest:
                raise IdempotencyConflict()
            if reserved.completed_at is not None:
                return _identity_from_response(reserved.response_body)
            identity = uow.users.get_identity(request.issuer, request.subject)
            if identity is None:
                raise NotFound()
            if identity.version != request.expected_version:
                raise StaleVersion(
                    details={
                        "expected_version": request.expected_version,
                        "current_version": identity.version,
                    }
                )
            if unlink:
                updated = uow.users.unlink_identity(
                    identity,
                    request.expected_version,
                    now,
                )
            else:
                updated = replace(
                    identity,
                    disabled_at=identity.disabled_at or now,
                    version=identity.version + 1,
                )
                uow.users.save_identity(updated, request.expected_version)
            uow.browser_sessions.revoke_for_user(identity.user_id)
            action = Action.IDENTITY_UNLINK if unlink else Action.IDENTITY_DISABLE
            uow.audit.append(
                scope,
                AuditEvent(
                    uuid5(command_id, "audit"),
                    actor.actor_id,
                    scope.organization_id,
                    action,
                    "external_identity",
                    identity.id,
                    "succeeded",
                    request.request_id,
                    now,
                    identity_id=identity.id,
                    command_id=command_id,
                    safe_metadata={"version": updated.version},
                ),
            )
            uow.commands.complete(
                scope,
                replace(
                    reserved,
                    response_status=200,
                    response_body=_identity_response(updated),
                    completed_at=now,
                ),
            )
            uow.commit()
            return updated
