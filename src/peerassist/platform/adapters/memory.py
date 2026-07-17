"""Deterministic in-memory adapters used by the shared platform contracts."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import io
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import BinaryIO, Protocol, Self
from urllib.parse import urlencode
from uuid import UUID, uuid4

from ..errors import (
    AuthenticationRequired,
    IdempotencyConflict,
    ImmutableResource,
    NotFound,
    PayloadTooLarge,
    StaleVersion,
)
from ..models import (
    Actor,
    Artifact,
    AuditEvent,
    AuthenticatedIdentity,
    BrowserSession,
    CommandRecord,
    DownloadDescriptor,
    ExternalIdentity,
    LegacyRegistration,
    ObjectDescriptor,
    OidcTransaction,
    Organization,
    OrganizationMembership,
    OutboxEvent,
    Paper,
    PaperVersion,
    Project,
    ProjectMembership,
    ReviewEvent,
    ReviewJob,
    TemporaryObjectDescriptor,
    TenantScope,
    User,
    WorkItem,
)

Clock = Callable[[], datetime]


class _TenantOwned(Protocol):
    organization_id: UUID


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _scope_matches(scope: TenantScope, organization_id: UUID, project_id: UUID | None) -> bool:
    return scope.organization_id == organization_id and (
        scope.project_id is None or scope.project_id == project_id
    )


def _require_scope(scope: TenantScope, value: _TenantOwned) -> None:
    project_id = getattr(value, "project_id", None)
    if isinstance(value, Project):
        project_id = value.id
    if not _scope_matches(
        scope,
        value.organization_id,
        project_id,
    ):
        raise NotFound()


@dataclass
class _State:
    users: dict[UUID, User] = field(default_factory=dict)
    identities: dict[tuple[str, str], ExternalIdentity] = field(default_factory=dict)
    organizations: dict[UUID, Organization] = field(default_factory=dict)
    organization_memberships: dict[tuple[UUID, UUID], OrganizationMembership] = field(default_factory=dict)
    projects: dict[UUID, Project] = field(default_factory=dict)
    project_memberships: dict[tuple[UUID, UUID], ProjectMembership] = field(default_factory=dict)
    papers: dict[UUID, Paper] = field(default_factory=dict)
    paper_versions: dict[UUID, PaperVersion] = field(default_factory=dict)
    review_jobs: dict[UUID, ReviewJob] = field(default_factory=dict)
    review_events: dict[UUID, list[ReviewEvent]] = field(default_factory=dict)
    artifacts: dict[UUID, Artifact] = field(default_factory=dict)
    commands: dict[tuple[UUID, UUID, str, str], CommandRecord] = field(default_factory=dict)
    work_items: dict[UUID, WorkItem] = field(default_factory=dict)
    outbox: dict[UUID, OutboxEvent] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)
    legacy_registrations: dict[UUID, LegacyRegistration] = field(default_factory=dict)
    browser_sessions: dict[str, BrowserSession] = field(default_factory=dict)
    oidc_transactions: dict[UUID, OidcTransaction] = field(default_factory=dict)

    def clone(self) -> _State:
        """Copy mutable containers; stored domain values are immutable snapshots."""
        result = copy.copy(self)
        for name, value in vars(self).items():
            setattr(result, name, list(value) if isinstance(value, list) else value.copy())
        result.review_events = {key: list(events) for key, events in self.review_events.items()}
        return result


class _Users:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, user_id: UUID) -> User | None:
        return self._state.users.get(user_id)

    def get_identity(self, issuer: str, subject: str) -> ExternalIdentity | None:
        return self._state.identities.get((issuer, subject))

    def add(self, user: User) -> None:
        self._state.users[user.id] = user

    def add_identity(self, identity: ExternalIdentity) -> None:
        key = (identity.issuer, identity.subject)
        existing = self._state.identities.get(key)
        if existing is not None and existing.id != identity.id:
            raise ValueError("issuer and subject already identify another identity")
        self._state.identities[key] = identity


class _Organizations:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope) -> Organization | None:
        return self._state.organizations.get(scope.organization_id)

    def add(self, scope: TenantScope, organization: Organization) -> None:
        _require_scope(scope, organization)
        self._state.organizations[organization.id] = organization

    def get_membership(self, scope: TenantScope, user_id: UUID) -> OrganizationMembership | None:
        return self._state.organization_memberships.get((scope.organization_id, user_id))

    def save_membership(
        self,
        scope: TenantScope,
        membership: OrganizationMembership,
        expected_version: int | None,
    ) -> None:
        _require_scope(scope, membership)
        key = (membership.organization_id, membership.user_id)
        current = self._state.organization_memberships.get(key)
        _check_version(current, expected_version)
        self._state.organization_memberships[key] = membership


def _check_version(current: object | None, expected_version: int | None) -> None:
    current_version = getattr(current, "version", None)
    if current_version != expected_version:
        raise StaleVersion(
            details={"expected_version": expected_version, "current_version": current_version},
        )


class _Projects:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope) -> Project | None:
        if scope.project_id is None:
            return None
        project = self._state.projects.get(scope.project_id)
        return (
            project
            if project is not None and _scope_matches(scope, project.organization_id, project.id)
            else None
        )

    def list(self, scope: TenantScope) -> tuple[Project, ...]:
        return tuple(
            item
            for item in self._state.projects.values()
            if _scope_matches(scope, item.organization_id, item.id)
        )

    def add(self, scope: TenantScope, project: Project) -> None:
        _require_scope(scope, project)
        self._state.projects[project.id] = project

    def save(self, scope: TenantScope, project: Project, expected_version: int) -> None:
        _require_scope(scope, project)
        current = self.get(scope)
        _check_version(current, expected_version)
        self._state.projects[project.id] = project

    def get_membership(self, scope: TenantScope, user_id: UUID) -> ProjectMembership | None:
        if scope.project_id is None:
            return None
        item = self._state.project_memberships.get((scope.project_id, user_id))
        return (
            item
            if item is not None and _scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def save_membership(
        self,
        scope: TenantScope,
        membership: ProjectMembership,
        expected_version: int | None,
    ) -> None:
        _require_scope(scope, membership)
        key = (membership.project_id, membership.user_id)
        _check_version(self._state.project_memberships.get(key), expected_version)
        self._state.project_memberships[key] = membership


class _Papers:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope, paper_id: UUID) -> Paper | None:
        item = self._state.papers.get(paper_id)
        return (
            item
            if item is not None and _scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def find_by_content_digest(self, scope: TenantScope, sha256: str) -> Paper | None:
        return next((item for item in self.list(scope) if item.content_sha256 == sha256), None)

    def list(self, scope: TenantScope) -> tuple[Paper, ...]:
        return tuple(
            item
            for item in self._state.papers.values()
            if _scope_matches(scope, item.organization_id, item.project_id)
        )

    def add(self, scope: TenantScope, paper: Paper, version: PaperVersion) -> None:
        _require_scope(scope, paper)
        _require_scope(scope, version)
        if version.paper_id != paper.id or version.id != paper.current_version_id:
            raise ValueError("paper and initial version do not match")
        self._state.papers[paper.id] = paper
        self._state.paper_versions[version.id] = version

    def add_version(self, scope: TenantScope, version: PaperVersion, expected_paper_version: int) -> None:
        _require_scope(scope, version)
        paper = self.get(scope, version.paper_id)
        _check_version(paper, expected_paper_version)
        self._state.paper_versions[version.id] = version


class _ReviewJobs:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope, job_id: UUID) -> ReviewJob | None:
        item = self._state.review_jobs.get(job_id)
        return (
            item
            if item is not None and _scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def list(self, scope: TenantScope) -> tuple[ReviewJob, ...]:
        return tuple(
            item
            for item in self._state.review_jobs.values()
            if _scope_matches(scope, item.organization_id, item.project_id)
        )

    def add(self, scope: TenantScope, job: ReviewJob) -> None:
        _require_scope(scope, job)
        self._state.review_jobs[job.id] = job

    def save(self, scope: TenantScope, job: ReviewJob, expected_version: int) -> None:
        _require_scope(scope, job)
        _check_version(self.get(scope, job.id), expected_version)
        self._state.review_jobs[job.id] = job

    def append_event(self, scope: TenantScope, event: ReviewEvent) -> None:
        _require_scope(scope, event)
        events = self._state.review_events.setdefault(event.job_id, [])
        expected = len(events) + 1
        if event.aggregate_sequence != expected:
            raise ValueError("aggregate event sequence must be contiguous and unique")
        events.append(event)

    def list_events(self, scope: TenantScope, job_id: UUID) -> tuple[ReviewEvent, ...]:
        return tuple(
            event
            for event in self._state.review_events.get(job_id, [])
            if _scope_matches(scope, event.organization_id, event.project_id)
        )


class _Artifacts:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope, artifact_id: UUID) -> Artifact | None:
        item = self._state.artifacts.get(artifact_id)
        return (
            item
            if item is not None and _scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def list_for_job(self, scope: TenantScope, job_id: UUID) -> tuple[Artifact, ...]:
        return tuple(
            item
            for item in self._state.artifacts.values()
            if item.job_id == job_id and _scope_matches(scope, item.organization_id, item.project_id)
        )

    def add(self, scope: TenantScope, artifact: Artifact) -> None:
        _require_scope(scope, artifact)
        self._state.artifacts[artifact.id] = artifact


class _Commands:
    def __init__(self, state: _State) -> None:
        self._state = state

    @staticmethod
    def _key(scope: TenantScope, actor_id: UUID, operation: str, key: str) -> tuple[UUID, UUID, str, str]:
        return scope.organization_id, actor_id, operation, key

    def get(
        self, scope: TenantScope, actor_id: UUID, operation: str, idempotency_key: str
    ) -> CommandRecord | None:
        item = self._state.commands.get(self._key(scope, actor_id, operation, idempotency_key))
        return (
            item
            if item is not None and _scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def reserve(self, scope: TenantScope, command: CommandRecord) -> None:
        _require_scope(scope, command)
        key = self._key(scope, command.actor_id, command.operation, command.idempotency_key)
        existing = self._state.commands.get(key)
        if existing is not None and existing.payload_digest != command.payload_digest:
            raise IdempotencyConflict()
        if existing is None:
            self._state.commands[key] = command

    def reserve_or_replay(self, scope: TenantScope, command: CommandRecord) -> CommandRecord:
        existing = self.get(scope, command.actor_id, command.operation, command.idempotency_key)
        if existing is None:
            self.reserve(scope, command)
            return command
        if existing.payload_digest != command.payload_digest:
            raise IdempotencyConflict()
        return existing

    def complete(self, scope: TenantScope, command: CommandRecord) -> None:
        key = self._key(scope, command.actor_id, command.operation, command.idempotency_key)
        existing = self._state.commands.get(key)
        if existing is None or existing.id != command.id or existing.payload_digest != command.payload_digest:
            raise IdempotencyConflict()
        self._state.commands[key] = command


class _WorkItems:
    def __init__(self, state: _State, clock: Clock) -> None:
        self._state = state
        self._clock = clock

    def enqueue(self, scope: TenantScope, item: WorkItem) -> None:
        _require_scope(scope, item)
        dedupe = (item.job_id, item.attempt_id, item.stage, item.input_revision)
        if any(
            (current.job_id, current.attempt_id, current.stage, current.input_revision) == dedupe
            for current in self._state.work_items.values()
        ):
            return
        self._state.work_items[item.id] = item

    def get(self, scope: TenantScope, item_id: UUID) -> WorkItem | None:
        item = self._state.work_items.get(item_id)
        return (
            item
            if item is not None and _scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def claim(self, scope: TenantScope, worker_id: str, lease_seconds: int) -> WorkItem | None:
        now = self._clock()
        for item in sorted(
            self._state.work_items.values(),
            key=lambda value: (value.available_at, value.created_at, value.id.int),
        ):
            if (
                not _scope_matches(scope, item.organization_id, item.project_id)
                or item.dead_lettered_at is not None
            ):
                continue
            if item.lease_expires_at is not None and item.lease_expires_at <= now:
                if item.attempt_count >= item.max_attempts:
                    self._state.work_items[item.id] = replace(
                        item,
                        lease_owner=None,
                        lease_expires_at=None,
                        dead_lettered_at=now,
                        safe_error_code="lease_expired",
                    )
                    continue
                item = replace(item, lease_owner=None, lease_expires_at=None)
                self._state.work_items[item.id] = item
            if item.lease_owner is not None or item.available_at > now:
                continue
            claimed = replace(
                item,
                attempt_count=item.attempt_count + 1,
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            self._state.work_items[item.id] = claimed
            return claimed
        return None

    def renew(self, scope: TenantScope, item_id: UUID, lease_owner: str, lease_seconds: int) -> WorkItem:
        item = self.get(scope, item_id)
        self._require_active_lease(item, lease_owner)
        renewed = replace(item, lease_expires_at=self._clock() + timedelta(seconds=lease_seconds))
        self._state.work_items[item_id] = renewed
        return renewed

    def complete(self, scope: TenantScope, item_id: UUID, lease_owner: str) -> None:
        item = self.get(scope, item_id)
        self._require_active_lease(item, lease_owner)
        del self._state.work_items[item_id]

    def fail(self, scope: TenantScope, item: WorkItem, lease_owner: str) -> None:
        current = self.get(scope, item.id)
        self._require_active_lease(current, lease_owner)
        self._state.work_items[item.id] = replace(
            current,
            lease_owner=None,
            lease_expires_at=None,
            available_at=self._clock(),
        )

    def _require_active_lease(self, item: WorkItem | None, lease_owner: str) -> None:
        if (
            item is None
            or item.lease_owner != lease_owner
            or item.lease_expires_at is None
            or item.lease_expires_at <= self._clock()
        ):
            raise ValueError("work item lease is absent, expired, or owned by another worker")


class _Outbox:
    def __init__(self, state: _State, clock: Clock) -> None:
        self._state = state
        self._clock = clock

    def append(self, scope: TenantScope, event: OutboxEvent) -> None:
        _require_scope(scope, event)
        if event.id in self._state.outbox:
            raise ValueError("outbox event already exists")
        self._state.outbox[event.id] = event

    def claim_batch(self, scope: TenantScope, limit: int) -> tuple[OutboxEvent, ...]:
        candidates = sorted(
            (
                item
                for item in self._state.outbox.values()
                if item.published_at is None and _scope_matches(scope, item.organization_id, item.project_id)
            ),
            key=lambda item: (item.created_at, item.aggregate_id.int, item.aggregate_sequence, item.id.int),
        )[:limit]
        claimed = tuple(
            replace(item, publication_attempts=item.publication_attempts + 1) for item in candidates
        )
        self._state.outbox.update((item.id, item) for item in claimed)
        return claimed

    def mark_published(self, scope: TenantScope, event_id: UUID) -> None:
        item = self._state.outbox.get(event_id)
        if item is None or not _scope_matches(scope, item.organization_id, item.project_id):
            raise NotFound()
        if item.published_at is None:
            self._state.outbox[event_id] = replace(item, published_at=self._clock())


class _Audit:
    def __init__(self, state: _State) -> None:
        self._state = state

    def append(self, scope: TenantScope, event: AuditEvent) -> None:
        _require_scope(scope, event)
        if any(existing.id == event.id for existing in self._state.audits):
            raise ValueError("audit records are append-only and event IDs are unique")
        self._state.audits.append(event)

    def list(self, scope: TenantScope) -> tuple[AuditEvent, ...]:
        return tuple(
            item
            for item in self._state.audits
            if _scope_matches(scope, item.organization_id, item.project_id)
        )


class _LegacyRegistrations:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope, registration_id: UUID) -> LegacyRegistration | None:
        item = self._state.legacy_registrations.get(registration_id)
        return (
            item
            if item is not None and _scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def add(self, scope: TenantScope, registration: LegacyRegistration) -> None:
        _require_scope(scope, registration)
        self._state.legacy_registrations[registration.id] = registration


class _BrowserSessions:
    def __init__(self, state: _State, clock: Clock) -> None:
        self._state = state
        self._clock = clock

    def get_by_digest(self, session_digest: str) -> BrowserSession | None:
        return self._state.browser_sessions.get(session_digest)

    def save(self, session: BrowserSession) -> None:
        self._state.browser_sessions[session.session_digest] = session

    def revoke_for_user(self, user_id: UUID) -> None:
        for digest, session in tuple(self._state.browser_sessions.items()):
            if session.user_id == user_id and session.revoked_at is None:
                self._state.browser_sessions[digest] = replace(session, revoked_at=self._clock())


class _OidcTransactions:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get_for_update(self, transaction_id: UUID) -> OidcTransaction | None:
        return self._state.oidc_transactions.get(transaction_id)

    def add(self, transaction: OidcTransaction) -> None:
        if transaction.id in self._state.oidc_transactions:
            raise ValueError("OIDC transaction already exists")
        self._state.oidc_transactions[transaction.id] = transaction

    def consume(self, transaction: OidcTransaction) -> None:
        current = self._state.oidc_transactions.get(transaction.id)
        if current is None or current.consumed_at is not None or transaction.consumed_at is None:
            raise ValueError("OIDC transaction is absent, consumed, or not marked consumed")
        self._state.oidc_transactions[transaction.id] = transaction

    def delete(self, transaction_id: UUID) -> None:
        self._state.oidc_transactions.pop(transaction_id, None)


class MemoryUnitOfWork:
    def __init__(self, factory: MemoryUnitOfWorkFactory, actor: Actor) -> None:
        self._factory = factory
        self.actor = actor
        self._state: _State | None = None
        self._committed = False
        self._entered = False

    def __enter__(self) -> Self:
        self._factory._lock.acquire()
        self._entered = True
        self._state = self._factory._state.clone()
        self._bind_repositories()
        return self

    def _bind_repositories(self) -> None:
        assert self._state is not None
        self.users = _Users(self._state)
        self.organizations = _Organizations(self._state)
        self.projects = _Projects(self._state)
        self.papers = _Papers(self._state)
        self.review_jobs = _ReviewJobs(self._state)
        self.artifacts = _Artifacts(self._state)
        self.commands = _Commands(self._state)
        self.work_items = _WorkItems(self._state, self._factory._clock)
        self.outbox = _Outbox(self._state, self._factory._clock)
        self.audit = _Audit(self._state)
        self.legacy_registrations = _LegacyRegistrations(self._state)
        self.browser_sessions = _BrowserSessions(self._state, self._factory._clock)
        self.oidc_transactions = _OidcTransactions(self._state)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
        finally:
            self._entered = False
            self._factory._lock.release()

    def commit(self) -> None:
        if not self._entered or self._state is None:
            raise RuntimeError("unit of work is not active")
        self._factory._state = self._state.clone()
        self._committed = True

    def rollback(self) -> None:
        if self._entered:
            self._state = self._factory._state.clone()
            self._bind_repositories()
        self._committed = False


class MemoryUnitOfWorkFactory:
    """Create serializable copy-on-write memory transactions."""

    def __init__(self, *, clock: Clock = _utc_now) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._state = _State()

    def __call__(self, actor: Actor) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(self, actor)


@dataclass
class _TemporaryObject:
    scope: TenantScope
    maximum_size_bytes: int
    data: bytes | None = None
    descriptor: TemporaryObjectDescriptor | None = None


@dataclass(frozen=True)
class _PublishedObject:
    scope: TenantScope
    data: bytes
    descriptor: ObjectDescriptor


class MemoryObjectStore:
    def __init__(self, *, clock: Clock = _utc_now) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._temporary: dict[UUID, _TemporaryObject] = {}
        self._published: dict[str, _PublishedObject] = {}

    def create_temporary(self, scope: TenantScope, maximum_size_bytes: int) -> UUID:
        if maximum_size_bytes <= 0:
            raise ValueError("maximum_size_bytes must be positive")
        with self._lock:
            upload_id = uuid4()
            self._temporary[upload_id] = _TemporaryObject(scope, maximum_size_bytes)
            return upload_id

    def write_temporary(
        self,
        scope: TenantScope,
        upload_id: UUID,
        source: BinaryIO,
    ) -> TemporaryObjectDescriptor:
        with self._lock:
            temporary = self._temporary.get(upload_id)
            if temporary is None or temporary.scope != scope:
                raise NotFound()
            chunks: list[bytes] = []
            size = 0
            digest = hashlib.sha256()
            while chunk := source.read(min(64 * 1024, temporary.maximum_size_bytes + 1)):
                if not isinstance(chunk, bytes):
                    raise TypeError("temporary object source must yield bytes")
                size += len(chunk)
                if size > temporary.maximum_size_bytes:
                    raise PayloadTooLarge(details={"limit": temporary.maximum_size_bytes})
                digest.update(chunk)
                chunks.append(chunk)
            descriptor = TemporaryObjectDescriptor(upload_id, size, digest.hexdigest())
            temporary.data = b"".join(chunks)
            temporary.descriptor = descriptor
            return descriptor

    def publish(
        self,
        scope: TenantScope,
        temporary: TemporaryObjectDescriptor,
        object_id: str,
    ) -> ObjectDescriptor:
        with self._lock:
            stored = self._temporary.get(temporary.upload_id)
            if stored is None or stored.scope != scope or stored.data is None or stored.descriptor is None:
                raise NotFound()
            if stored.descriptor != temporary:
                raise ValueError("temporary object size or digest does not match stored bytes")
            descriptor = ObjectDescriptor(
                object_id,
                temporary.size_bytes,
                temporary.sha256,
                "application/octet-stream",
            )
            existing = self._published.get(object_id)
            if existing is not None:
                if existing.scope == scope and existing.descriptor.sha256 == descriptor.sha256:
                    return existing.descriptor
                raise ImmutableResource()
            self._published[object_id] = _PublishedObject(scope, bytes(stored.data), descriptor)
            return descriptor

    def metadata(self, scope: TenantScope, object_id: str) -> ObjectDescriptor | None:
        with self._lock:
            item = self._published.get(object_id)
            return item.descriptor if item is not None and item.scope == scope else None

    def open_immutable(self, scope: TenantScope, object_id: str) -> BinaryIO:
        with self._lock:
            item = self._published.get(object_id)
            if item is None or item.scope != scope:
                raise NotFound()
            if hashlib.sha256(item.data).hexdigest() != item.descriptor.sha256:
                raise ValueError("published object digest validation failed")
            return io.BytesIO(bytes(item.data))

    def read_range(self, scope: TenantScope, object_id: str, start: int, end: int) -> bytes:
        if start < 0 or end < start:
            raise ValueError("range must have nonnegative inclusive bounds")
        data = self.open_immutable(scope, object_id).read()
        if start >= len(data) or end >= len(data):
            raise ValueError("range exceeds object size")
        return data[start : end + 1]

    def download_descriptor(self, scope: TenantScope, object_id: str, filename: str) -> DownloadDescriptor:
        descriptor = self.metadata(scope, object_id)
        if descriptor is None:
            raise NotFound()
        return DownloadDescriptor(descriptor, filename)

    def tombstone(self, scope: TenantScope, object_id: str) -> None:
        with self._lock:
            item = self._published.get(object_id)
            if item is None or item.scope != scope:
                raise NotFound()
            del self._published[object_id]

    def delete_temporary(self, scope: TenantScope, upload_id: UUID) -> None:
        with self._lock:
            item = self._temporary.get(upload_id)
            if item is None:
                return
            if item.scope != scope:
                raise NotFound()
            del self._temporary[upload_id]


class FakeIdentityProvider:
    """Signed deterministic identity fake; bearer values are never retained."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        accepted_algorithms: frozenset[str],
        clock: Clock = _utc_now,
        disabled_identities: set[tuple[str, str]] | None = None,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.accepted_algorithms = frozenset(accepted_algorithms)
        self._clock = clock
        self._disabled_identities = frozenset(disabled_identities or ())
        self._signing_key = b"peerassist-memory-identity-contract-key"
        self._callback_claims: dict[tuple[UUID, str], dict[str, object]] = {}
        self._consumed_transactions: set[UUID] = set()

    def issue(self, claims: dict[str, object]) -> str:
        payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self._signing_key, encoded, hashlib.sha256).hexdigest().encode()
        return (encoded + b"." + signature).decode()

    def issue_and_validate(self, claims: dict[str, object]) -> AuthenticatedIdentity:
        return self.validate_bearer(self.issue(claims))

    def validate_bearer(self, bearer: str) -> AuthenticatedIdentity:
        try:
            encoded, supplied_signature = bearer.encode().split(b".", 1)
            expected_signature = hmac.new(self._signing_key, encoded, hashlib.sha256).hexdigest().encode()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ValueError
            payload = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
            claims = json.loads(payload)
            if not isinstance(claims, dict):
                raise ValueError
            return self._validate_claims(claims)
        except AuthenticationRequired:
            raise
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise AuthenticationRequired(cause=error) from error

    def _validate_claims(self, claims: dict[str, object]) -> AuthenticatedIdentity:
        now = self._clock().timestamp()
        issuer = claims.get("iss")
        subject = claims.get("sub")
        audience = claims.get("aud")
        algorithm = claims.get("alg")
        expires = claims.get("exp")
        not_before = claims.get("nbf", 0)
        if issuer != self.issuer or not isinstance(subject, str) or not subject:
            raise AuthenticationRequired()
        if audience != self.audience or algorithm not in self.accepted_algorithms or algorithm == "none":
            raise AuthenticationRequired()
        if not isinstance(expires, (int, float)) or expires <= now:
            raise AuthenticationRequired()
        if not isinstance(not_before, (int, float)) or not_before > now:
            raise AuthenticationRequired()
        if (issuer, subject) in self._disabled_identities:
            raise AuthenticationRequired()
        normalized: dict[str, object] = {
            "iss": issuer,
            "sub": subject,
            "aud": audience,
        }
        if claims.get("email_verified") is True and isinstance(claims.get("email"), str):
            normalized["email"] = claims["email"].strip().casefold()
            normalized["email_verified"] = True
        if isinstance(claims.get("name"), str):
            normalized["name"] = " ".join(claims["name"].split())
        return AuthenticatedIdentity(
            issuer,
            subject,
            normalized,
            datetime.fromtimestamp(float(expires), UTC),
        )

    def build_authorization_url(self, transaction_id: UUID) -> str:
        return f"{self.issuer}/authorize?{urlencode({'transaction_id': str(transaction_id)})}"

    def issue_callback(self, transaction_id: UUID, claims: dict[str, object]) -> str:
        code = uuid4().hex
        self._callback_claims[(transaction_id, code)] = copy.deepcopy(claims)
        return code

    def exchange_callback(self, transaction_id: UUID, authorization_code: str) -> AuthenticatedIdentity:
        key = (transaction_id, authorization_code)
        claims = self._callback_claims.pop(key, None)
        if claims is None or transaction_id in self._consumed_transactions:
            raise AuthenticationRequired()
        self._consumed_transactions.add(transaction_id)
        return self.issue_and_validate(claims)
