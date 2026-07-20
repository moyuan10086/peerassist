"""Paper, review-job, artifact, and legacy repositories."""

from __future__ import annotations

from uuid import UUID, uuid5

from sqlalchemy import and_, delete, insert, or_, select, update

from peerassist.platform.errors import NotFound, StaleVersion
from peerassist.platform.models import (
    Artifact,
    LegacyRegistration,
    Paper,
    PaperVersion,
    ReviewEvent,
    ReviewJob,
    TenantScope,
)

from . import postgres_schema as schema
from .postgres_core import (
    _artifact,
    _json,
    _legacy,
    _paper,
    _paper_version,
    _project_filter,
    _Repository,
    _require_initial,
    _require_next,
    _require_project,
    _review_event,
    _review_job,
)


class _Papers(_Repository):
    def get(self, scope: TenantScope, paper_id: UUID) -> Paper | None:
        row = self._one(
            select(schema.papers).where(_project_filter(schema.papers, scope), schema.papers.c.id == paper_id)
        )
        return None if row is None else _paper(row)

    def find_by_content_digest(self, scope: TenantScope, sha256: str) -> Paper | None:
        row = self._one(
            select(schema.papers).where(
                _project_filter(schema.papers, scope), schema.papers.c.content_sha256 == sha256
            )
        )
        return None if row is None else _paper(row)

    def get_version(self, scope: TenantScope, version_id: UUID) -> PaperVersion | None:
        row = self._one(
            select(schema.paper_versions).where(
                _project_filter(schema.paper_versions, scope), schema.paper_versions.c.id == version_id
            )
        )
        return None if row is None else _paper_version(row)

    def list(self, scope: TenantScope) -> tuple[Paper, ...]:
        rows = self.connection.execute(
            select(schema.papers).where(_project_filter(schema.papers, scope)).order_by(schema.papers.c.id)
        ).mappings()
        return tuple(_paper(row) for row in rows)

    def add(self, scope: TenantScope, paper: Paper, version: PaperVersion) -> None:
        _require_project(scope, paper)
        _require_project(scope, version)
        _require_initial(paper)
        if version.paper_id != paper.id or version.id != paper.current_version_id or version.revision != 1:
            raise StaleVersion(details={"expected_version": 1, "current_version": version.revision})
        current_paper = self._one(
            select(schema.papers).where(_project_filter(schema.papers, scope), schema.papers.c.id == paper.id)
        )
        current_version = self._one(
            select(schema.paper_versions).where(
                _project_filter(schema.paper_versions, scope), schema.paper_versions.c.id == version.id
            )
        )
        if current_paper is not None or current_version is not None:
            if current_paper is not None and current_version is not None:
                if _paper(current_paper) == paper and _paper_version(current_version) == version:
                    return
            raise ValueError("paper or version already exists")

        def operation() -> None:
            self.connection.execute(
                insert(schema.papers).values(
                    id=paper.id, organization_id=paper.organization_id, project_id=paper.project_id,
                    content_sha256=paper.content_sha256, current_version_id=None, status=paper.status,
                    version=paper.version, created_at=paper.created_at, updated_at=paper.updated_at,
                )
            )
            self.connection.execute(
                insert(schema.paper_versions).values(
                    id=version.id, organization_id=version.organization_id, project_id=version.project_id,
                    paper_id=version.paper_id, source_object_id=version.source_object_id,
                    filename=version.filename, media_type=version.media_type, size_bytes=version.size_bytes,
                    sha256=version.sha256, created_by=version.created_by, revision=version.revision,
                    created_at=version.created_at,
                )
            )
            self.connection.execute(
                update(schema.papers)
                .where(_project_filter(schema.papers, scope), schema.papers.c.id == paper.id)
                .values(current_version_id=version.id)
            )

        self._integrity(operation, "paper or version already exists or is invalid")

    def add_version(self, scope: TenantScope, version: PaperVersion, expected_paper_version: int) -> None:
        _require_project(scope, version)
        row = self._one(
            select(schema.papers)
            .where(_project_filter(schema.papers, scope), schema.papers.c.id == version.paper_id)
            .with_for_update()
        )
        if row is None:
            raise NotFound()
        paper = _paper(row)
        if paper.version != expected_paper_version or version.revision != expected_paper_version + 1:
            raise StaleVersion(
                details={"expected_version": expected_paper_version + 1, "current_version": version.revision}
            )
        current = self._one(
            select(schema.paper_versions).where(
                _project_filter(schema.paper_versions, scope), schema.paper_versions.c.id == version.id
            )
        )
        if current is not None:
            if _paper_version(current) == version:
                return
            raise ValueError("paper version already exists")

        def operation() -> None:
            self.connection.execute(
                insert(schema.paper_versions).values(
                    id=version.id, organization_id=version.organization_id, project_id=version.project_id,
                    paper_id=version.paper_id, source_object_id=version.source_object_id,
                    filename=version.filename, media_type=version.media_type, size_bytes=version.size_bytes,
                    sha256=version.sha256, created_by=version.created_by, revision=version.revision,
                    created_at=version.created_at,
                )
            )
            result = self.connection.execute(
                update(schema.papers)
                .where(
                    _project_filter(schema.papers, scope), schema.papers.c.id == version.paper_id,
                    schema.papers.c.version == expected_paper_version,
                )
                .values(
                    current_version_id=version.id, version=expected_paper_version + 1,
                    updated_at=version.created_at,
                )
            )
            if result.rowcount != 1:
                raise StaleVersion(
                    details={"expected_version": expected_paper_version, "current_version": None}
                )

        self._integrity(operation, "paper version already exists or is invalid")


class _ReviewJobs(_Repository):
    def get(self, scope: TenantScope, job_id: UUID) -> ReviewJob | None:
        row = self._one(
            select(schema.review_jobs).where(
                _project_filter(schema.review_jobs, scope), schema.review_jobs.c.id == job_id
            )
        )
        return None if row is None else _review_job(row)

    def list(self, scope: TenantScope) -> tuple[ReviewJob, ...]:
        rows = self.connection.execute(
            select(schema.review_jobs).where(_project_filter(schema.review_jobs, scope)).order_by(schema.review_jobs.c.id)
        ).mappings()
        return tuple(_review_job(row) for row in rows)

    def add(self, scope: TenantScope, job: ReviewJob) -> None:
        _require_project(scope, job)
        _require_initial(job)
        current = self._one(
            select(schema.review_jobs).where(
                _project_filter(schema.review_jobs, scope), schema.review_jobs.c.id == job.id
            )
        )
        if current is not None:
            if _review_job(current) == job:
                return
            raise ValueError("review job already exists")
        def operation() -> None:
            self.connection.execute(
                insert(schema.review_jobs).values(
                    id=job.id, organization_id=job.organization_id, project_id=job.project_id,
                    paper_version_id=job.paper_version_id, mode=job.mode, stage=job.stage, status=job.status,
                    version=job.version, attempt=job.attempt, created_by=job.created_by,
                    created_at=job.created_at, updated_at=job.updated_at, cancelled_at=job.cancelled_at,
                    safe_error_code=job.safe_error_code,
                )
            )
            self._add_attempt(job)

        self._integrity(operation, "review job already exists or is invalid")

    def save(self, scope: TenantScope, job: ReviewJob, expected_version: int) -> None:
        _require_project(scope, job)
        _require_next(job, expected_version)
        current_row = self._one(
            select(schema.review_jobs).where(
                _project_filter(schema.review_jobs, scope),
                schema.review_jobs.c.id == job.id,
            )
        )
        if current_row is None:
            raise NotFound()
        current = _review_job(current_row)
        result = self.connection.execute(
            update(schema.review_jobs)
            .where(
                _project_filter(schema.review_jobs, scope), schema.review_jobs.c.id == job.id,
                schema.review_jobs.c.version == expected_version,
            )
            .values(
                mode=job.mode, stage=job.stage, status=job.status, version=job.version,
                attempt=job.attempt, updated_at=job.updated_at, cancelled_at=job.cancelled_at,
                safe_error_code=job.safe_error_code,
            )
        )
        if result.rowcount != 1:
            raise StaleVersion(details={"expected_version": expected_version, "current_version": None})
        if job.attempt > current.attempt:
            self._integrity(
                lambda: self._add_attempt(job),
                "review attempt already exists or is invalid",
            )

    def delete(self, scope: TenantScope, job_id: UUID) -> None:
        if scope.project_id is None:
            raise NotFound()
        job_filter = _project_filter(schema.review_jobs, scope) & (schema.review_jobs.c.id == job_id)
        if self._one(select(schema.review_jobs).where(job_filter)) is None:
            raise NotFound()

        def child_filter(table):
            return _project_filter(table, scope) & (table.c.job_id == job_id)
        # Delete children first because the M1 schema deliberately keeps audit
        # history but does not cascade ReviewJob-owned operational records.
        for table in (
            schema.artifacts,
            schema.work_items,
            schema.stage_manifests,
            schema.report_versions,
            schema.review_events,
            schema.review_attempts,
        ):
            self.connection.execute(delete(table).where(child_filter(table)))
        self.connection.execute(
            delete(schema.outbox_events).where(
                schema.outbox_events.c.organization_id == scope.organization_id,
                schema.outbox_events.c.project_id == scope.project_id,
                schema.outbox_events.c.aggregate_id == job_id,
            )
        )
        self.connection.execute(delete(schema.review_jobs).where(job_filter))

    def _add_attempt(self, job: ReviewJob) -> None:
        if job.attempt <= 0:
            return
        self.connection.execute(
            insert(schema.review_attempts).values(
                id=uuid5(job.id, f"attempt:{job.attempt}"),
                organization_id=job.organization_id,
                project_id=job.project_id,
                job_id=job.id,
                attempt_number=job.attempt,
                status="queued",
                started_at=None,
                finished_at=None,
                created_at=job.updated_at,
            )
        )

    def append_event(self, scope: TenantScope, event: ReviewEvent) -> None:
        _require_project(scope, event)
        last = self.connection.scalar(
            select(schema.review_events.c.aggregate_sequence)
            .where(_project_filter(schema.review_events, scope), schema.review_events.c.job_id == event.job_id)
            .order_by(schema.review_events.c.aggregate_sequence.desc())
            .limit(1)
            .with_for_update()
        )
        if event.aggregate_sequence != (0 if last is None else last) + 1:
            raise ValueError("aggregate event sequence must be contiguous and unique")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.review_events).values(
                    id=event.id, organization_id=event.organization_id, project_id=event.project_id,
                    job_id=event.job_id, aggregate_sequence=event.aggregate_sequence,
                    event_type=event.event_type, schema_version=event.schema_version,
                    payload=_json(event.payload), created_at=event.created_at,
                )
            ),
            "aggregate event sequence already exists or is invalid",
        )

    def list_events(self, scope: TenantScope, job_id: UUID) -> tuple[ReviewEvent, ...]:
        rows = self.connection.execute(
            select(schema.review_events)
            .where(_project_filter(schema.review_events, scope), schema.review_events.c.job_id == job_id)
            .order_by(schema.review_events.c.aggregate_sequence)
        ).mappings()
        return tuple(_review_event(row) for row in rows)


class _Artifacts(_Repository):
    def get(self, scope: TenantScope, artifact_id: UUID) -> Artifact | None:
        row = self._one(
            select(schema.artifacts).where(
                _project_filter(schema.artifacts, scope), schema.artifacts.c.id == artifact_id
            )
        )
        return None if row is None else _artifact(row)

    def list_for_job(self, scope: TenantScope, job_id: UUID) -> tuple[Artifact, ...]:
        rows = self.connection.execute(
            select(schema.artifacts)
            .where(_project_filter(schema.artifacts, scope), schema.artifacts.c.job_id == job_id)
            .order_by(schema.artifacts.c.created_at, schema.artifacts.c.id)
        ).mappings()
        return tuple(_artifact(row) for row in rows)

    def add(self, scope: TenantScope, artifact: Artifact) -> None:
        _require_project(scope, artifact)
        current = self._one(
            select(schema.artifacts).where(
                _project_filter(schema.artifacts, scope),
                or_(
                    schema.artifacts.c.id == artifact.id,
                    and_(
                        schema.artifacts.c.job_id == artifact.job_id,
                        schema.artifacts.c.logical_name == artifact.logical_name,
                    ),
                    schema.artifacts.c.object_id == artifact.object.object_id,
                )
            )
        )
        if current is not None:
            if _artifact(current) == artifact:
                return
            raise ValueError("artifact already exists")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.artifacts).values(
                    id=artifact.id, organization_id=artifact.organization_id, project_id=artifact.project_id,
                    job_id=artifact.job_id, logical_name=artifact.logical_name,
                    object_id=artifact.object.object_id, media_type=artifact.object.media_type,
                    size_bytes=artifact.object.size_bytes, sha256=artifact.object.sha256,
                    schema_version=artifact.object.schema_version, status=artifact.status,
                    created_at=artifact.created_at,
                )
            ),
            "artifact already exists or is invalid",
        )


class _LegacyRegistrations(_Repository):
    def get(self, scope: TenantScope, registration_id: UUID) -> LegacyRegistration | None:
        row = self._one(
            select(schema.legacy_registrations).where(
                _project_filter(schema.legacy_registrations, scope),
                schema.legacy_registrations.c.id == registration_id,
            )
        )
        return None if row is None else _legacy(row)

    def add(self, scope: TenantScope, registration: LegacyRegistration) -> None:
        _require_project(scope, registration)
        current = self._one(
            select(schema.legacy_registrations).where(
                _project_filter(schema.legacy_registrations, scope),
                schema.legacy_registrations.c.id == registration.id,
            )
        )
        if current is not None:
            if _legacy(current) == registration:
                return
            raise ValueError("legacy registration already exists")
        self._integrity(
            lambda: self.connection.execute(
                insert(schema.legacy_registrations).values(
                    id=registration.id, organization_id=registration.organization_id,
                    project_id=registration.project_id, legacy_type=registration.legacy_type,
                    opaque_locator=registration.opaque_locator,
                    manifest_digest=registration.manifest_sha256, schema_version=registration.version,
                    status=registration.status, registered_by=self._uow.actor.actor_id,
                    created_at=registration.created_at, updated_at=registration.created_at,
                )
            ),
            "legacy registration already exists or is invalid",
        )
