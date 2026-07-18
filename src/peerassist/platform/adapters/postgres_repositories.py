"""Identity, organization, project, session, and OIDC repositories."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, insert, or_, select, update

from peerassist.platform.errors import NotFound, StaleVersion
from peerassist.platform.models import (
    BrowserSession,
    ExternalIdentity,
    OidcTransaction,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    TenantScope,
    User,
)

from . import postgres_schema as schema
from .postgres_core import (
    Clock,
    _identity,
    _json,
    _oidc_transaction,
    _organization,
    _organization_membership,
    _project,
    _project_filter,
    _project_membership,
    _Repository,
    _require_initial,
    _require_next,
    _require_project,
    _session,
    _user,
)


class _Users(_Repository):
    def get(self, user_id: UUID) -> User | None:
        row = self._one(select(schema.users).where(schema.users.c.id == user_id))
        return None if row is None else _user(row)

    def get_identity(self, issuer: str, subject: str) -> ExternalIdentity | None:
        row = self._one(
            select(schema.external_identities).where(
                schema.external_identities.c.issuer == issuer,
                schema.external_identities.c.subject == subject,
            )
        )
        return None if row is None else _identity(row)

    def get_identity_by_id(self, identity_id: UUID) -> ExternalIdentity | None:
        row = self._one(
            select(schema.external_identities).where(
                schema.external_identities.c.id == identity_id
            )
        )
        return None if row is None else _identity(row)

    def add(self, user: User) -> None:
        existing = self.get(user.id)
        if existing is not None:
            if existing == user:
                return
            raise ValueError("user already exists")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.users).values(
                    id=user.id, status=user.status, display_name=user.display_name, version=user.version,
                    created_at=user.created_at, updated_at=user.updated_at,
                )
            ),
            "user already exists or is invalid",
        )

    def add_identity(self, identity: ExternalIdentity) -> None:
        current = self.get_identity(identity.issuer, identity.subject)
        if current is not None:
            if current == identity:
                return
            raise ValueError("external identity already exists")
        row = self._one(select(schema.external_identities).where(schema.external_identities.c.id == identity.id))
        if row is not None:
            raise ValueError("external identity already exists")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.external_identities).values(
                    id=identity.id, user_id=identity.user_id, issuer=identity.issuer, subject=identity.subject,
                    verified_claims=_json(identity.verified_claims), last_seen_at=identity.last_seen_at,
                    created_at=identity.created_at, disabled_at=identity.disabled_at, version=identity.version,
                )
            ),
            "external identity already exists or is invalid",
        )

    def save_identity(self, identity: ExternalIdentity, expected_version: int) -> None:
        if identity.version != expected_version + 1:
            raise StaleVersion(
                details={"expected_version": expected_version + 1, "current_version": identity.version}
            )
        result = self.connection.execute(
            update(schema.external_identities)
            .where(
                schema.external_identities.c.id == identity.id,
                schema.external_identities.c.issuer == identity.issuer,
                schema.external_identities.c.subject == identity.subject,
                schema.external_identities.c.version == expected_version,
            )
            .values(
                verified_claims=_json(identity.verified_claims),
                last_seen_at=identity.last_seen_at,
                disabled_at=identity.disabled_at,
                version=identity.version,
            )
        )
        if result.rowcount != 1:
            raise StaleVersion(details={"expected_version": expected_version, "current_version": None})

    def unlink_identity(
        self,
        identity: ExternalIdentity,
        expected_version: int,
        unlinked_at: datetime,
    ) -> ExternalIdentity:
        row = self.connection.execute(
            update(schema.external_identities)
            .where(
                schema.external_identities.c.id == identity.id,
                schema.external_identities.c.issuer == identity.issuer,
                schema.external_identities.c.subject == identity.subject,
                schema.external_identities.c.version == expected_version,
            )
            .values(
                issuer=f"urn:peerassist:unlinked:{identity.id}",
                subject=identity.id.hex,
                verified_claims={},
                disabled_at=identity.disabled_at or unlinked_at,
                version=expected_version + 1,
            )
            .returning(schema.external_identities)
        ).mappings().one_or_none()
        if row is None:
            raise StaleVersion(
                details={"expected_version": expected_version, "current_version": None}
            )
        return _identity(row)


class _Organizations(_Repository):
    def get(self, scope: TenantScope) -> Organization | None:
        if scope.project_id is not None:
            return None
        row = self._one(select(schema.organizations).where(schema.organizations.c.id == scope.organization_id))
        return None if row is None else _organization(row)

    def list_for_user(self, user_id: UUID) -> tuple[OrganizationMembership, ...]:
        rows = self.connection.execute(
            select(schema.organization_memberships)
            .where(schema.organization_memberships.c.user_id == user_id)
            .order_by(schema.organization_memberships.c.id)
        ).mappings()
        return tuple(_organization_membership(row) for row in rows)

    def list_memberships(self, scope: TenantScope) -> tuple[OrganizationMembership, ...]:
        if scope.project_id is not None:
            return ()
        rows = self.connection.execute(
            select(schema.organization_memberships)
            .where(schema.organization_memberships.c.organization_id == scope.organization_id)
            .order_by(schema.organization_memberships.c.id)
        ).mappings()
        return tuple(_organization_membership(row) for row in rows)

    def add(self, scope: TenantScope, organization: Organization) -> None:
        if scope.project_id is not None or organization.id != scope.organization_id:
            raise NotFound()
        _require_initial(organization)
        current = self._one(
            select(schema.organizations).where(
                or_(schema.organizations.c.id == organization.id, schema.organizations.c.slug == organization.slug)
            )
        )
        if current is not None:
            if _organization(current) == organization:
                return
            raise ValueError("organization already exists")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.organizations).values(
                    id=organization.id, slug=organization.slug, name=organization.name, status=organization.status,
                    version=organization.version, created_at=organization.created_at, updated_at=organization.updated_at,
                )
            ),
            "organization already exists or is invalid",
        )

    def get_membership(self, scope: TenantScope, user_id: UUID) -> OrganizationMembership | None:
        if scope.project_id is not None:
            return None
        row = self._one(
            select(schema.organization_memberships).where(
                schema.organization_memberships.c.organization_id == scope.organization_id,
                schema.organization_memberships.c.user_id == user_id,
            )
        )
        return None if row is None else _organization_membership(row)

    def get_membership_by_id(
        self, scope: TenantScope, membership_id: UUID
    ) -> OrganizationMembership | None:
        if scope.project_id is not None:
            return None
        row = self._one(
            select(schema.organization_memberships).where(
                schema.organization_memberships.c.organization_id == scope.organization_id,
                schema.organization_memberships.c.id == membership_id,
            )
        )
        return None if row is None else _organization_membership(row)

    def save_membership(
        self, scope: TenantScope, membership: OrganizationMembership, expected_version: int | None
    ) -> None:
        if scope.project_id is not None or membership.organization_id != scope.organization_id:
            raise NotFound()
        _require_next(membership, expected_version)
        values = {
            "id": membership.id, "organization_id": membership.organization_id, "user_id": membership.user_id,
            "role": membership.role.value, "status": membership.status, "version": membership.version,
            "created_at": membership.created_at, "updated_at": membership.updated_at,
            "revoked_at": membership.revoked_at,
        }
        if expected_version is None:
            self._integrity(
                lambda: self.connection.execute(insert(schema.organization_memberships).values(**values)),
                "organization membership already exists or is invalid",
            )
            return
        result = self.connection.execute(
            update(schema.organization_memberships)
            .where(
                schema.organization_memberships.c.organization_id == scope.organization_id,
                schema.organization_memberships.c.user_id == membership.user_id,
                schema.organization_memberships.c.version == expected_version,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise StaleVersion(details={"expected_version": expected_version, "current_version": None})


class _Projects(_Repository):
    def get(self, scope: TenantScope) -> Project | None:
        row = self._one(select(schema.projects).where(_project_filter(schema.projects, scope)))
        return None if row is None else _project(row)

    def list(self, scope: TenantScope) -> tuple[Project, ...]:
        rows = self.connection.execute(
            select(schema.projects).where(_project_filter(schema.projects, scope)).order_by(schema.projects.c.id)
        ).mappings()
        return tuple(_project(row) for row in rows)

    def list_for_organization(self, scope: TenantScope) -> tuple[Project, ...]:
        if scope.project_id is not None:
            return ()
        rows = self.connection.execute(
            select(schema.projects)
            .where(schema.projects.c.organization_id == scope.organization_id)
            .order_by(schema.projects.c.id)
        ).mappings()
        return tuple(_project(row) for row in rows)

    def list_for_user(self, user_id: UUID) -> tuple[ProjectMembership, ...]:
        rows = self.connection.execute(
            select(schema.project_memberships)
            .where(schema.project_memberships.c.user_id == user_id)
            .order_by(schema.project_memberships.c.id)
        ).mappings()
        return tuple(_project_membership(row) for row in rows)

    def list_memberships(self, scope: TenantScope) -> tuple[ProjectMembership, ...]:
        rows = self.connection.execute(
            select(schema.project_memberships)
            .where(_project_filter(schema.project_memberships, scope))
            .order_by(schema.project_memberships.c.id)
        ).mappings()
        return tuple(_project_membership(row) for row in rows)

    def add(self, scope: TenantScope, project: Project) -> None:
        _require_project(scope, project)
        _require_initial(project)
        current = self._one(select(schema.projects).where(_project_filter(schema.projects, scope)))
        if current is not None:
            if _project(current) == project:
                return
            raise ValueError("project already exists")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.projects).values(
                    id=project.id, organization_id=project.organization_id, name=project.name,
                    status=project.status, version=project.version, created_at=project.created_at,
                    updated_at=project.updated_at,
                )
            ),
            "project already exists or is invalid",
        )

    def save(self, scope: TenantScope, project: Project, expected_version: int) -> None:
        _require_project(scope, project)
        _require_next(project, expected_version)
        result = self.connection.execute(
            update(schema.projects)
            .where(_project_filter(schema.projects, scope), schema.projects.c.version == expected_version)
            .values(name=project.name, status=project.status, version=project.version, updated_at=project.updated_at)
        )
        if result.rowcount != 1:
            raise StaleVersion(details={"expected_version": expected_version, "current_version": None})

    def get_membership(self, scope: TenantScope, user_id: UUID) -> ProjectMembership | None:
        row = self._one(
            select(schema.project_memberships).where(
                _project_filter(schema.project_memberships, scope),
                schema.project_memberships.c.user_id == user_id,
            )
        )
        return None if row is None else _project_membership(row)

    def get_membership_by_id(
        self, scope: TenantScope, membership_id: UUID
    ) -> ProjectMembership | None:
        row = self._one(
            select(schema.project_memberships).where(
                _project_filter(schema.project_memberships, scope),
                schema.project_memberships.c.id == membership_id,
            )
        )
        return None if row is None else _project_membership(row)

    def save_membership(
        self, scope: TenantScope, membership: ProjectMembership, expected_version: int | None
    ) -> None:
        _require_project(scope, membership)
        _require_next(membership, expected_version)
        values = {
            "id": membership.id, "organization_id": membership.organization_id,
            "project_id": membership.project_id, "user_id": membership.user_id,
            "role": membership.role.value, "status": membership.status, "version": membership.version,
            "created_at": membership.created_at, "updated_at": membership.updated_at,
            "revoked_at": membership.revoked_at,
        }
        if expected_version is None:
            self._integrity(
                lambda: self.connection.execute(insert(schema.project_memberships).values(**values)),
                "project membership already exists or is invalid",
            )
            return
        result = self.connection.execute(
            update(schema.project_memberships)
            .where(
                _project_filter(schema.project_memberships, scope),
                schema.project_memberships.c.user_id == membership.user_id,
                schema.project_memberships.c.version == expected_version,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise StaleVersion(details={"expected_version": expected_version, "current_version": None})


class _BrowserSessions(_Repository):
    def __init__(self, uow: Any, clock: Clock) -> None:
        super().__init__(uow)
        self._clock = clock

    def get_by_digest(self, session_digest: str) -> BrowserSession | None:
        row = self._one(
            select(schema.browser_sessions).where(schema.browser_sessions.c.session_digest == session_digest)
        )
        return None if row is None else _session(row)

    def save(self, session: BrowserSession) -> None:
        row = self._one(
            select(schema.browser_sessions).where(
                or_(
                    schema.browser_sessions.c.id == session.id,
                    schema.browser_sessions.c.session_digest == session.session_digest,
                )
            )
        )
        if row is not None:
            if _session(row) == session:
                return
            raise ValueError("browser session already exists")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.browser_sessions).values(
                    id=session.id, user_id=session.user_id, identity_id=session.identity_id,
                    session_digest=session.session_digest, csrf_digest=session.csrf_digest,
                    provider_credential_ref=session.provider_credential_ref,
                    token_expires_at=session.expires_at, expires_at=session.expires_at,
                    idle_expires_at=session.idle_expires_at, created_at=session.created_at,
                    updated_at=session.created_at, revoked_at=session.revoked_at,
                )
            ),
            "browser session already exists or is invalid",
        )

    def revoke_for_user(self, user_id: UUID) -> None:
        now = self._clock()
        self.connection.execute(
            update(schema.browser_sessions)
            .where(schema.browser_sessions.c.user_id == user_id, schema.browser_sessions.c.revoked_at.is_(None))
            .values(revoked_at=now, updated_at=now)
        )


class _OidcTransactions(_Repository):
    def get_for_update(self, transaction_id: UUID) -> OidcTransaction | None:
        row = self._one(
            select(schema.oidc_transactions)
            .where(schema.oidc_transactions.c.id == transaction_id)
            .with_for_update()
        )
        return None if row is None else _oidc_transaction(row)

    def add(self, transaction: OidcTransaction) -> None:
        row = self._one(
            select(schema.oidc_transactions).where(
                or_(
                    schema.oidc_transactions.c.id == transaction.id,
                    schema.oidc_transactions.c.state_digest == transaction.state_digest,
                    schema.oidc_transactions.c.nonce_digest == transaction.nonce_digest,
                )
            )
        )
        if row is not None:
            if _oidc_transaction(row) == transaction:
                return
            raise ValueError("OIDC transaction already exists")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.oidc_transactions).values(
                    id=transaction.id, state_digest=transaction.state_digest,
                    nonce_digest=transaction.nonce_digest,
                    encrypted_pkce_verifier=transaction.encrypted_pkce_verifier,
                    encryption_key_id=transaction.encryption_key_id, return_path=transaction.return_path,
                    expires_at=transaction.expires_at, created_at=transaction.created_at,
                    consumed_at=transaction.consumed_at,
                )
            ),
            "OIDC transaction already exists or is invalid",
        )

    def consume(self, transaction: OidcTransaction) -> None:
        current = self.get_for_update(transaction.id)
        if current is None or current.consumed_at is not None or transaction.consumed_at is None:
            raise ValueError("OIDC transaction is absent, consumed, or not marked consumed")
        if replace(transaction, consumed_at=current.consumed_at) != current:
            raise ValueError("OIDC transaction identity cannot change while consuming")
        self.connection.execute(
            update(schema.oidc_transactions)
            .where(schema.oidc_transactions.c.id == transaction.id, schema.oidc_transactions.c.consumed_at.is_(None))
            .values(consumed_at=transaction.consumed_at)
        )

    def delete(self, transaction_id: UUID) -> None:
        self.connection.execute(delete(schema.oidc_transactions).where(schema.oidc_transactions.c.id == transaction_id))
