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
    ExternalServiceConsent,
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
    ReportVersion,
    ReviewDocument,
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


def _organization_scope_matches(scope: TenantScope, organization_id: UUID) -> bool:
    return scope.organization_id == organization_id


def _project_scope_matches(scope: TenantScope, organization_id: UUID, project_id: UUID | None) -> bool:
    return (
        scope.project_id is not None
        and project_id is not None
        and scope.organization_id == organization_id
        and scope.project_id == project_id
    )


def _record_scope_matches(scope: TenantScope, organization_id: UUID, project_id: UUID | None) -> bool:
    return scope.organization_id == organization_id and scope.project_id == project_id


def _insert_immutable(mapping: dict[object, object], key: object, value: object) -> None:
    existing = mapping.get(key)
    if existing is not None:
        if existing == value:
            return
        raise ValueError("record already exists with different immutable data")
    mapping[key] = value


def _require_project_scope(scope: TenantScope, value: _TenantOwned) -> None:
    project_id = getattr(value, "project_id", None)
    if isinstance(value, Project):
        project_id = value.id
    if not _project_scope_matches(
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
    consents: dict[UUID, ExternalServiceConsent] = field(default_factory=dict)
    current_consent_ids: dict[tuple[UUID, UUID, UUID, UUID, str], UUID] = field(
        default_factory=dict
    )
    review_documents: dict[tuple[UUID, UUID, UUID], ReviewDocument] = field(default_factory=dict)
    report_versions: dict[UUID, ReportVersion] = field(default_factory=dict)
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

    def get_identity_by_id(self, identity_id: UUID) -> ExternalIdentity | None:
        return next(
            (identity for identity in self._state.identities.values() if identity.id == identity_id),
            None,
        )

    def add(self, user: User) -> None:
        _insert_immutable(self._state.users, user.id, user)

    def add_identity(self, identity: ExternalIdentity) -> None:
        key = (identity.issuer, identity.subject)
        existing = self._state.identities.get(key)
        if existing is not None:
            if existing == identity:
                return
            raise ValueError("external identity already exists with different immutable data")
        if any(current.id == identity.id for current in self._state.identities.values()):
            raise ValueError("external identity ID already exists")
        self._state.identities[key] = identity

    def save_identity(self, identity: ExternalIdentity, expected_version: int) -> None:
        key = (identity.issuer, identity.subject)
        current = self._state.identities.get(key)
        _check_replacement_version(current, identity, expected_version)
        if current is not None and current.id != identity.id:
            raise ValueError("external identity identity cannot change")
        self._state.identities[key] = identity

    def unlink_identity(
        self,
        identity: ExternalIdentity,
        expected_version: int,
        unlinked_at: datetime,
    ) -> ExternalIdentity:
        key = (identity.issuer, identity.subject)
        current = self._state.identities.get(key)
        if current != identity or current.version != expected_version:
            raise StaleVersion(
                details={"expected_version": expected_version, "current_version": None}
            )
        unlinked = replace(
            current,
            issuer=f"urn:peerassist:unlinked:{current.id}",
            subject=current.id.hex,
            verified_claims={},
            disabled_at=current.disabled_at or unlinked_at,
            version=current.version + 1,
        )
        del self._state.identities[key]
        self._state.identities[(unlinked.issuer, unlinked.subject)] = unlinked
        return unlinked


class _Organizations:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope) -> Organization | None:
        return self._state.organizations.get(scope.organization_id)

    def list_for_user(self, user_id: UUID) -> tuple[OrganizationMembership, ...]:
        return tuple(
            sorted(
                (item for item in self._state.organization_memberships.values() if item.user_id == user_id),
                key=lambda item: item.id.int,
            )
        )

    def list_memberships(self, scope: TenantScope) -> tuple[OrganizationMembership, ...]:
        if scope.project_id is not None:
            return ()
        return tuple(
            sorted(
                (
                    item
                    for item in self._state.organization_memberships.values()
                    if item.organization_id == scope.organization_id
                ),
                key=lambda item: item.id.int,
            )
        )

    def add(self, scope: TenantScope, organization: Organization) -> None:
        if not _organization_scope_matches(scope, organization.id):
            raise NotFound()
        _require_initial_version(organization)
        if any(current.slug == organization.slug for current in self._state.organizations.values()):
            existing = self._state.organizations.get(organization.id)
            if existing == organization:
                return
            raise ValueError("organization slug already exists")
        _insert_immutable(self._state.organizations, organization.id, organization)

    def get_membership(self, scope: TenantScope, user_id: UUID) -> OrganizationMembership | None:
        return self._state.organization_memberships.get((scope.organization_id, user_id))

    def get_membership_by_id(
        self, scope: TenantScope, membership_id: UUID
    ) -> OrganizationMembership | None:
        return next(
            (item for item in self.list_memberships(scope) if item.id == membership_id),
            None,
        )

    def save_membership(
        self,
        scope: TenantScope,
        membership: OrganizationMembership,
        expected_version: int | None,
    ) -> None:
        if not _organization_scope_matches(scope, membership.organization_id):
            raise NotFound()
        key = (membership.organization_id, membership.user_id)
        current = self._state.organization_memberships.get(key)
        _check_replacement_version(current, membership, expected_version)
        self._state.organization_memberships[key] = membership


def _check_current_version(current: object | None, expected_version: int | None) -> None:
    current_version = getattr(current, "version", None)
    if current_version != expected_version:
        raise StaleVersion(
            details={"expected_version": expected_version, "current_version": current_version},
        )


def _require_initial_version(value: object) -> None:
    if getattr(value, "version", None) != 1:
        raise StaleVersion(details={"expected_version": 1, "current_version": None})


def _check_replacement_version(
    current: object | None, replacement: object, expected_version: int | None
) -> None:
    _check_current_version(current, expected_version)
    replacement_version = getattr(replacement, "version", None)
    required_version = 1 if expected_version is None else expected_version + 1
    if replacement_version != required_version:
        raise StaleVersion(
            details={
                "expected_version": required_version,
                "current_version": replacement_version,
            }
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
            if project is not None and _project_scope_matches(scope, project.organization_id, project.id)
            else None
        )

    def list(self, scope: TenantScope) -> tuple[Project, ...]:
        return tuple(
            item
            for item in self._state.projects.values()
            if _project_scope_matches(scope, item.organization_id, item.id)
        )

    def list_for_organization(self, scope: TenantScope) -> tuple[Project, ...]:
        if scope.project_id is not None:
            return ()
        return tuple(
            sorted(
                (
                    item
                    for item in self._state.projects.values()
                    if item.organization_id == scope.organization_id
                ),
                key=lambda item: item.id.int,
            )
        )

    def list_for_user(self, user_id: UUID) -> tuple[ProjectMembership, ...]:
        return tuple(
            sorted(
                (item for item in self._state.project_memberships.values() if item.user_id == user_id),
                key=lambda item: item.id.int,
            )
        )

    def list_memberships(self, scope: TenantScope) -> tuple[ProjectMembership, ...]:
        if scope.project_id is None:
            return ()
        return tuple(
            sorted(
                (
                    item
                    for item in self._state.project_memberships.values()
                    if _project_scope_matches(scope, item.organization_id, item.project_id)
                ),
                key=lambda item: item.id.int,
            )
        )

    def add(self, scope: TenantScope, project: Project) -> None:
        _require_project_scope(scope, project)
        _require_initial_version(project)
        _insert_immutable(self._state.projects, project.id, project)

    def save(self, scope: TenantScope, project: Project, expected_version: int) -> None:
        _require_project_scope(scope, project)
        current = self.get(scope)
        _check_replacement_version(current, project, expected_version)
        self._state.projects[project.id] = project

    def get_membership(self, scope: TenantScope, user_id: UUID) -> ProjectMembership | None:
        if scope.project_id is None:
            return None
        item = self._state.project_memberships.get((scope.project_id, user_id))
        return (
            item
            if item is not None and _project_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def get_membership_by_id(
        self, scope: TenantScope, membership_id: UUID
    ) -> ProjectMembership | None:
        return next((item for item in self.list_memberships(scope) if item.id == membership_id), None)

    def save_membership(
        self,
        scope: TenantScope,
        membership: ProjectMembership,
        expected_version: int | None,
    ) -> None:
        _require_project_scope(scope, membership)
        key = (membership.project_id, membership.user_id)
        _check_replacement_version(self._state.project_memberships.get(key), membership, expected_version)
        if membership.is_default:
            for other_key, other in tuple(self._state.project_memberships.items()):
                if (
                    other_key != key
                    and other.organization_id == membership.organization_id
                    and other.user_id == membership.user_id
                    and other.is_default
                ):
                    self._state.project_memberships[other_key] = replace(
                        other,
                        is_default=False,
                        version=other.version + 1,
                        updated_at=membership.updated_at,
                    )
        self._state.project_memberships[key] = membership


class _Papers:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope, paper_id: UUID) -> Paper | None:
        item = self._state.papers.get(paper_id)
        return (
            item
            if item is not None and _project_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def find_by_content_digest(self, scope: TenantScope, sha256: str) -> Paper | None:
        return next((item for item in self.list(scope) if item.content_sha256 == sha256), None)

    def get_version(self, scope: TenantScope, version_id: UUID) -> PaperVersion | None:
        item = self._state.paper_versions.get(version_id)
        return (
            item
            if item is not None and _project_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def list(self, scope: TenantScope) -> tuple[Paper, ...]:
        return tuple(
            item
            for item in self._state.papers.values()
            if _project_scope_matches(scope, item.organization_id, item.project_id)
        )

    def add(self, scope: TenantScope, paper: Paper, version: PaperVersion) -> None:
        _require_project_scope(scope, paper)
        _require_project_scope(scope, version)
        _require_initial_version(paper)
        if version.revision != 1:
            raise StaleVersion(details={"expected_version": 1, "current_version": version.revision})
        if version.paper_id != paper.id or version.id != paper.current_version_id:
            raise ValueError("paper and initial version do not match")
        if any(
            current.project_id == paper.project_id
            and current.organization_id == paper.organization_id
            and current.content_sha256 == paper.content_sha256
            and current.id != paper.id
            for current in self._state.papers.values()
        ):
            raise ValueError("paper content digest already exists in project")
        existing_paper = self._state.papers.get(paper.id)
        existing_version = self._state.paper_versions.get(version.id)
        if existing_paper is not None or existing_version is not None:
            if existing_paper == paper and existing_version == version:
                return
            raise ValueError("paper or initial version already exists with different immutable data")
        self._state.papers[paper.id] = paper
        self._state.paper_versions[version.id] = version

    def add_version(self, scope: TenantScope, version: PaperVersion, expected_paper_version: int) -> None:
        _require_project_scope(scope, version)
        paper = self.get(scope, version.paper_id)
        _check_current_version(paper, expected_paper_version)
        if version.revision != expected_paper_version + 1:
            raise StaleVersion(
                details={
                    "expected_version": expected_paper_version + 1,
                    "current_version": version.revision,
                }
            )
        existing_version = self._state.paper_versions.get(version.id)
        if existing_version is not None and existing_version != version:
            raise ValueError("paper version already exists with different immutable data")
        self._state.paper_versions[version.id] = version
        self._state.papers[version.paper_id] = replace(
            paper,
            current_version_id=version.id,
            version=expected_paper_version + 1,
            updated_at=version.created_at,
        )


class _ReviewJobs:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope, job_id: UUID) -> ReviewJob | None:
        item = self._state.review_jobs.get(job_id)
        return (
            item
            if item is not None and _project_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def list(self, scope: TenantScope) -> tuple[ReviewJob, ...]:
        return tuple(
            item
            for item in self._state.review_jobs.values()
            if _project_scope_matches(scope, item.organization_id, item.project_id)
        )

    def add(self, scope: TenantScope, job: ReviewJob) -> None:
        _require_project_scope(scope, job)
        _require_initial_version(job)
        _insert_immutable(self._state.review_jobs, job.id, job)

    def save(self, scope: TenantScope, job: ReviewJob, expected_version: int) -> None:
        _require_project_scope(scope, job)
        _check_replacement_version(self.get(scope, job.id), job, expected_version)
        self._state.review_jobs[job.id] = job

    def delete(self, scope: TenantScope, job_id: UUID) -> None:
        job = self.get(scope, job_id)
        if job is None:
            raise NotFound()
        self._state.review_jobs.pop(job_id, None)
        self._state.review_events.pop(job_id, None)
        self._state.review_documents.pop((job.organization_id, job.project_id, job_id), None)
        for consent_id, consent in tuple(self._state.consents.items()):
            if (
                consent.organization_id == job.organization_id
                and consent.project_id == job.project_id
                and consent.review_job_id == job_id
            ):
                del self._state.consents[consent_id]
                self._state.current_consent_ids.pop(
                    (
                        consent.organization_id,
                        consent.project_id,
                        consent.review_job_id,
                        consent.paper_version_id,
                        consent.service,
                    ),
                    None,
                )
        for artifact_id, artifact in tuple(self._state.artifacts.items()):
            if artifact.job_id == job_id:
                del self._state.artifacts[artifact_id]
        for report_version_id, report_version in tuple(self._state.report_versions.items()):
            if (
                report_version.organization_id == job.organization_id
                and report_version.project_id == job.project_id
                and report_version.job_id == job_id
            ):
                del self._state.report_versions[report_version_id]
        for item_id, item in tuple(self._state.work_items.items()):
            if item.job_id == job_id:
                del self._state.work_items[item_id]
        for event_id, event in tuple(self._state.outbox.items()):
            if event.aggregate_id == job_id:
                del self._state.outbox[event_id]

    def append_event(self, scope: TenantScope, event: ReviewEvent) -> None:
        _require_project_scope(scope, event)
        events = self._state.review_events.setdefault(event.job_id, [])
        expected = len(events) + 1
        if event.aggregate_sequence != expected:
            raise ValueError("aggregate event sequence must be contiguous and unique")
        events.append(event)

    def list_events(self, scope: TenantScope, job_id: UUID) -> tuple[ReviewEvent, ...]:
        return tuple(
            event
            for event in self._state.review_events.get(job_id, [])
            if _project_scope_matches(scope, event.organization_id, event.project_id)
        )


class _Consents:
    def __init__(self, state: _State) -> None:
        self._state = state

    @staticmethod
    def _key(consent: ExternalServiceConsent) -> tuple[UUID, UUID, UUID, UUID, str]:
        return (
            consent.organization_id,
            consent.project_id,
            consent.review_job_id,
            consent.paper_version_id,
            consent.service,
        )

    def get_current(
        self,
        scope: TenantScope,
        review_job_id: UUID,
        paper_version_id: UUID,
        service: str,
    ) -> ExternalServiceConsent | None:
        if scope.project_id is None:
            return None
        consent_id = self._state.current_consent_ids.get(
            (
                scope.organization_id,
                scope.project_id,
                review_job_id,
                paper_version_id,
                service,
            )
        )
        item = self._state.consents.get(consent_id) if consent_id is not None else None
        return (
            item
            if item is not None
            and item.superseded_at is None
            and _project_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def add(self, scope: TenantScope, consent: ExternalServiceConsent) -> None:
        _require_project_scope(scope, consent)
        _require_initial_version(consent)
        if consent.generation != 1:
            raise ValueError("initial consent generation must be 1")
        if consent.superseded_at is not None:
            raise ValueError("a new consent must be current")
        existing = self._state.consents.get(consent.id)
        key = self._key(consent)
        if existing == consent and self._state.current_consent_ids.get(key) == consent.id:
            return
        if existing is not None:
            raise ValueError("consent ID already exists")
        if key in self._state.current_consent_ids:
            raise ValueError("current consent already exists")
        self._state.consents[consent.id] = consent
        self._state.current_consent_ids[key] = consent.id

    def save(
        self,
        scope: TenantScope,
        consent: ExternalServiceConsent,
        expected_version: int,
    ) -> None:
        _require_project_scope(scope, consent)
        current = self.get_current(
            scope,
            consent.review_job_id,
            consent.paper_version_id,
            consent.service,
        )
        if current is None:
            raise NotFound()
        _check_replacement_version(current, consent, expected_version)
        self._require_same_record(current, consent)
        if consent.superseded_at is not None:
            raise ValueError("save cannot supersede a consent")
        self._state.consents[consent.id] = consent

    def supersede_and_add(
        self,
        scope: TenantScope,
        superseded: ExternalServiceConsent,
        replacement: ExternalServiceConsent,
        expected_version: int,
    ) -> None:
        _require_project_scope(scope, superseded)
        _require_project_scope(scope, replacement)
        current = self.get_current(
            scope,
            superseded.review_job_id,
            superseded.paper_version_id,
            superseded.service,
        )
        if current is None:
            raise NotFound()
        _check_replacement_version(current, superseded, expected_version)
        self._require_same_record(current, superseded)
        if superseded.superseded_at is None:
            raise ValueError("superseded consent must record superseded_at")
        if self._key(replacement) != self._key(current):
            raise ValueError("replacement consent must retain the current consent key")
        if replacement.id in self._state.consents:
            raise ValueError("replacement consent ID already exists")
        if replacement.generation != current.generation + 1:
            raise ValueError("replacement consent generation must advance exactly once")
        if replacement.version != 1:
            raise StaleVersion(
                details={"expected_version": 1, "current_version": replacement.version}
            )
        if replacement.status != "pending" or replacement.superseded_at is not None:
            raise ValueError("replacement consent must be a current pending decision")
        key = self._key(current)
        self._state.consents[superseded.id] = superseded
        self._state.consents[replacement.id] = replacement
        self._state.current_consent_ids[key] = replacement.id

    @staticmethod
    def _require_same_record(
        current: ExternalServiceConsent,
        replacement: ExternalServiceConsent,
    ) -> None:
        immutable_names = (
            "id",
            "organization_id",
            "project_id",
            "review_job_id",
            "paper_version_id",
            "service",
            "provider_config_revision",
            "policy_version",
            "data_scope",
            "generation",
            "created_at",
        )
        if any(getattr(current, name) != getattr(replacement, name) for name in immutable_names):
            raise ValueError("consent identity and generation are immutable")


class _ReviewDocuments:
    def __init__(self, state: _State) -> None:
        self._state = state

    @staticmethod
    def _key(document: ReviewDocument) -> tuple[UUID, UUID, UUID]:
        return document.organization_id, document.project_id, document.review_job_id

    def get(self, scope: TenantScope, review_job_id: UUID) -> ReviewDocument | None:
        if scope.project_id is None:
            return None
        item = self._state.review_documents.get(
            (scope.organization_id, scope.project_id, review_job_id)
        )
        return (
            item
            if item is not None
            and _project_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def add(self, scope: TenantScope, document: ReviewDocument) -> None:
        _require_project_scope(scope, document)
        if document.document_version != 1:
            raise StaleVersion(
                details={"expected_version": 1, "current_version": document.document_version}
            )
        key = self._key(document)
        existing = self._state.review_documents.get(key)
        if existing == document:
            return
        if existing is not None:
            raise ValueError("review document already exists for job")
        if any(item.id == document.id for item in self._state.review_documents.values()):
            raise ValueError("review document ID already exists")
        self._state.review_documents[key] = document

    def save(
        self,
        scope: TenantScope,
        document: ReviewDocument,
        expected_document_version: int,
    ) -> None:
        _require_project_scope(scope, document)
        current = self.get(scope, document.review_job_id)
        if current is None:
            raise NotFound()
        if current.document_version != expected_document_version:
            raise StaleVersion(
                details={
                    "expected_version": expected_document_version,
                    "current_version": current.document_version,
                }
            )
        required_version = expected_document_version + 1
        if document.document_version != required_version:
            raise StaleVersion(
                details={
                    "expected_version": required_version,
                    "current_version": document.document_version,
                }
            )
        immutable_names = (
            "id",
            "organization_id",
            "project_id",
            "review_job_id",
            "created_at",
        )
        if any(getattr(current, name) != getattr(document, name) for name in immutable_names):
            raise ValueError("review document identity is immutable")
        self._state.review_documents[self._key(document)] = document


class _ReportVersions:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope, report_version_id: UUID) -> ReportVersion | None:
        item = self._state.report_versions.get(report_version_id)
        return (
            item
            if item is not None
            and _project_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def list_for_job(
        self,
        scope: TenantScope,
        review_job_id: UUID,
    ) -> tuple[ReportVersion, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._state.report_versions.values()
                    if item.job_id == review_job_id
                    and _project_scope_matches(scope, item.organization_id, item.project_id)
                ),
                key=lambda item: item.revision,
            )
        )

    def add(self, scope: TenantScope, report_version: ReportVersion) -> None:
        _require_project_scope(scope, report_version)
        current = self._state.report_versions.get(report_version.id)
        if current == report_version:
            return
        if current is not None:
            raise ValueError("report version ID already exists")
        revisions = self.list_for_job(scope, report_version.job_id)
        expected_revision = (revisions[-1].revision if revisions else 0) + 1
        if report_version.revision != expected_revision:
            raise ValueError("report version revision must advance exactly once")
        self._state.report_versions[report_version.id] = report_version


class _Artifacts:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope, artifact_id: UUID) -> Artifact | None:
        item = self._state.artifacts.get(artifact_id)
        return (
            item
            if item is not None and _project_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def list_for_job(self, scope: TenantScope, job_id: UUID) -> tuple[Artifact, ...]:
        return tuple(
            item
            for item in self._state.artifacts.values()
            if item.job_id == job_id and _project_scope_matches(scope, item.organization_id, item.project_id)
        )

    def add(self, scope: TenantScope, artifact: Artifact) -> None:
        _require_project_scope(scope, artifact)
        if any(
            current.job_id == artifact.job_id
            and current.logical_name == artifact.logical_name
            and current.id != artifact.id
            for current in self._state.artifacts.values()
        ):
            raise ValueError("artifact logical name already exists for job")
        _insert_immutable(self._state.artifacts, artifact.id, artifact)


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
            if item is not None and _record_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def reserve(self, scope: TenantScope, command: CommandRecord) -> None:
        if not _record_scope_matches(scope, command.organization_id, command.project_id):
            raise NotFound()
        key = self._key(scope, command.actor_id, command.operation, command.idempotency_key)
        existing = self._state.commands.get(key)
        identity = self._identity(command)
        if existing is not None:
            if not _record_scope_matches(scope, existing.organization_id, existing.project_id):
                raise IdempotencyConflict()
            if self._identity(existing) != identity:
                raise IdempotencyConflict()
            return
        self._state.commands[key] = command

    def reserve_or_replay(self, scope: TenantScope, command: CommandRecord) -> CommandRecord:
        existing = self.get(scope, command.actor_id, command.operation, command.idempotency_key)
        if existing is None:
            self.reserve(scope, command)
            return command
        if self._identity(existing) != self._identity(command):
            raise IdempotencyConflict()
        return existing

    def complete(self, scope: TenantScope, command: CommandRecord) -> None:
        if not _record_scope_matches(scope, command.organization_id, command.project_id):
            raise NotFound()
        key = self._key(scope, command.actor_id, command.operation, command.idempotency_key)
        existing = self._state.commands.get(key)
        if existing is None or self._identity(existing) != self._identity(command):
            raise IdempotencyConflict()
        if existing.completed_at is not None:
            if self._completion(existing) == self._completion(command):
                return
            raise IdempotencyConflict()
        self._state.commands[key] = replace(
            existing,
            response_status=command.response_status,
            response_body=command.response_body,
            completed_at=command.completed_at,
        )

    @staticmethod
    def _identity(command: CommandRecord) -> tuple[object, ...]:
        return (
            command.organization_id,
            command.actor_id,
            command.operation,
            command.idempotency_key,
            command.payload_digest,
        )

    @staticmethod
    def _completion(command: CommandRecord) -> tuple[object, ...]:
        return command.response_status, command.response_body, command.completed_at


class _WorkItems:
    def __init__(self, state: _State, clock: Clock) -> None:
        self._state = state
        self._clock = clock

    def enqueue(self, scope: TenantScope, item: WorkItem) -> None:
        _require_project_scope(scope, item)
        existing = self._state.work_items.get(item.id)
        if existing is not None:
            if existing == item:
                return
            raise ValueError("work item already exists with different immutable data")
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
            if item is not None and _project_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def claim(self, scope: TenantScope, worker_id: str, lease_seconds: int) -> WorkItem | None:
        return self._claim(scope, worker_id, lease_seconds)

    def claim_next(self, worker_id: str, lease_seconds: int) -> WorkItem | None:
        return self._claim(None, worker_id, lease_seconds)

    def _claim(
        self,
        scope: TenantScope | None,
        worker_id: str,
        lease_seconds: int,
    ) -> WorkItem | None:
        if lease_seconds <= 0 or not worker_id.strip():
            return None
        now = self._clock()
        for item in sorted(
            self._state.work_items.values(),
            key=lambda value: (value.available_at, value.created_at, value.id.int),
        ):
            if (
                (scope is not None and not _project_scope_matches(scope, item.organization_id, item.project_id))
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
        now = self._clock()
        exhausted = current.attempt_count >= current.max_attempts
        self._state.work_items[item.id] = replace(
            current,
            lease_owner=None,
            lease_expires_at=None,
            available_at=now,
            dead_lettered_at=now if exhausted else None,
            safe_error_code="attempts_exhausted" if exhausted else current.safe_error_code,
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
        if not _record_scope_matches(scope, event.organization_id, event.project_id):
            raise NotFound()
        if event.id in self._state.outbox:
            raise ValueError("outbox event already exists")
        self._state.outbox[event.id] = event

    def claim_batch(self, scope: TenantScope, limit: int) -> tuple[OutboxEvent, ...]:
        candidates = sorted(
            (
                item
                for item in self._state.outbox.values()
                if item.published_at is None
                and _record_scope_matches(scope, item.organization_id, item.project_id)
                and not any(
                    lower.published_at is None
                    and lower.organization_id == item.organization_id
                    and lower.project_id == item.project_id
                    and lower.aggregate_type == item.aggregate_type
                    and lower.aggregate_id == item.aggregate_id
                    and lower.aggregate_sequence < item.aggregate_sequence
                    for lower in self._state.outbox.values()
                )
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
        if item is None or not _record_scope_matches(scope, item.organization_id, item.project_id):
            raise NotFound()
        if item.published_at is None:
            self._state.outbox[event_id] = replace(item, published_at=self._clock())


class _Audit:
    def __init__(self, state: _State) -> None:
        self._state = state

    def append(self, scope: TenantScope, event: AuditEvent) -> None:
        if not _record_scope_matches(scope, event.organization_id, event.project_id):
            raise NotFound()
        if any(existing.id == event.id for existing in self._state.audits):
            raise ValueError("audit records are append-only and event IDs are unique")
        self._state.audits.append(event)

    def list(self, scope: TenantScope) -> tuple[AuditEvent, ...]:
        return tuple(
            item
            for item in self._state.audits
            if _record_scope_matches(scope, item.organization_id, item.project_id)
        )

    def list_for_organization(self, scope: TenantScope) -> tuple[AuditEvent, ...]:
        if scope.project_id is not None:
            return ()
        return tuple(
            item for item in self._state.audits if item.organization_id == scope.organization_id
        )


class _LegacyRegistrations:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get(self, scope: TenantScope, registration_id: UUID) -> LegacyRegistration | None:
        item = self._state.legacy_registrations.get(registration_id)
        return (
            item
            if item is not None and _project_scope_matches(scope, item.organization_id, item.project_id)
            else None
        )

    def add(self, scope: TenantScope, registration: LegacyRegistration) -> None:
        _require_project_scope(scope, registration)
        _insert_immutable(self._state.legacy_registrations, registration.id, registration)


class _BrowserSessions:
    def __init__(self, state: _State, clock: Clock) -> None:
        self._state = state
        self._clock = clock

    def get_by_digest(self, session_digest: str) -> BrowserSession | None:
        return self._state.browser_sessions.get(session_digest)

    def save(self, session: BrowserSession) -> None:
        if any(
            existing.id == session.id and digest != session.session_digest
            for digest, existing in self._state.browser_sessions.items()
        ):
            raise ValueError("browser session ID already exists")
        _insert_immutable(self._state.browser_sessions, session.session_digest, session)

    def revoke(self, session_id: UUID, revoked_at: datetime) -> None:
        for digest, session in tuple(self._state.browser_sessions.items()):
            if session.id == session_id and session.revoked_at is None:
                self._state.browser_sessions[digest] = replace(session, revoked_at=revoked_at)
                return
        raise ValueError("browser session is absent or revoked")

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
        if any(
            existing.state_digest == transaction.state_digest
            or existing.nonce_digest == transaction.nonce_digest
            for existing in self._state.oidc_transactions.values()
        ):
            existing = self._state.oidc_transactions.get(transaction.id)
            if existing == transaction:
                return
            raise ValueError("OIDC transaction state or nonce already exists")
        if transaction.id in self._state.oidc_transactions:
            if self._state.oidc_transactions[transaction.id] == transaction:
                return
            raise ValueError("OIDC transaction ID already exists")
        self._state.oidc_transactions[transaction.id] = transaction

    def consume(self, transaction: OidcTransaction) -> None:
        current = self._state.oidc_transactions.get(transaction.id)
        if current is None or current.consumed_at is not None or transaction.consumed_at is None:
            raise ValueError("OIDC transaction is absent, consumed, or not marked consumed")
        if replace(transaction, consumed_at=current.consumed_at) != current:
            raise ValueError("OIDC transaction identity cannot change while consuming")
        self._state.oidc_transactions[transaction.id] = replace(current, consumed_at=transaction.consumed_at)

    def delete(self, transaction_id: UUID) -> None:
        self._state.oidc_transactions.pop(transaction_id, None)


class MemoryUnitOfWork:
    consents: _Consents
    review_documents: _ReviewDocuments
    report_versions: _ReportVersions

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
        self.consents = _Consents(self._state)
        self.review_documents = _ReviewDocuments(self._state)
        self.report_versions = _ReportVersions(self._state)
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
        self._tombstones: dict[str, TenantScope] = {}

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
            if object_id in self._tombstones:
                raise ImmutableResource()
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
            self._tombstones[object_id] = scope

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

    def build_authorization_url(self, transaction_id: UUID, **kwargs: str) -> str:
        return f"{self.issuer}/authorize?{urlencode({'transaction_id': str(transaction_id), **kwargs})}"

    def issue_callback(self, transaction_id: UUID, claims: dict[str, object]) -> str:
        code = uuid4().hex
        self._callback_claims[(transaction_id, code)] = copy.deepcopy(claims)
        return code

    def exchange_callback(
        self, transaction_id: UUID, authorization_code: str, **kwargs: str
    ) -> AuthenticatedIdentity:
        del kwargs
        key = (transaction_id, authorization_code)
        claims = self._callback_claims.pop(key, None)
        if claims is None or transaction_id in self._consumed_transactions:
            raise AuthenticationRequired()
        self._consumed_transactions.add(transaction_id)
        return self.issue_and_validate(claims)
