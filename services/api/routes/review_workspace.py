"""Teacher-first aggregate facade over the project-scoped review services."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, File, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from peerassist.platform.models import ExternalServiceConsent, ReviewDocument, ReviewDocumentBlock
from peerassist.platform.services.artifacts import ArtifactService
from peerassist.platform.services.papers import PaperService, PaperUploadResult, UploadPaper
from peerassist.platform.services.review_workspace import (
    ChangeExternalServiceConsent,
    DecideFinding,
    ExportSubmission,
    FindingDecisionResult,
    ReviewWorkspaceService,
    SaveReviewDocument,
    SubmitReviewExport,
)
from peerassist.platform.services.reviews import (
    ChangeReviewJob,
    CreateReviewJob,
    DeleteReviewJob,
    ReviewService,
)
from peerassist.platform.services.workspace_bootstrap import WorkspaceBootstrapService

from .organizations import IdempotencyKey, ManagementActor, OrganizationView, ProjectView
from .papers import PaperUploadView, PaperView
from .projects import ProjectMembershipView
from .review_jobs import ReviewJobView

router = APIRouter(prefix="/api/v1/workspace", tags=["review-workspace"])


class WorkspaceView(BaseModel):
    status: str
    organization: OrganizationView | None
    project: ProjectView | None
    projects: list[ProjectView]
    membership: ProjectMembershipView | None
    papers: list[PaperView]
    reviews: list[ReviewJobView]


class CreateWorkspaceReviewBody(BaseModel):
    paper_version_id: UUID
    mode: Literal["fast", "full"] = "full"


class SelectWorkspaceProjectBody(BaseModel):
    project_id: UUID


class ChangeWorkspaceReviewBody(BaseModel):
    expected_version: int = Field(ge=1)


class ReviewDocumentBlockBody(BaseModel):
    id: UUID
    section: str
    text: str
    source_type: str
    finding_lineage_id: str | None = None
    finding_id: str | None = None
    finding_revision: int | None = None
    evidence_ids: tuple[str, ...] = ()
    evidence_locator: dict[str, object] | None = None

    def as_domain(self) -> ReviewDocumentBlock:
        return ReviewDocumentBlock(**self.model_dump())


class ReviewDocumentBlockView(ReviewDocumentBlockBody):
    model_config = ConfigDict(from_attributes=True)


class ReviewDocumentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    project_id: UUID
    review_job_id: UUID
    blocks: list[ReviewDocumentBlockView]
    document_version: int
    base_decision_event_id: UUID | None
    last_edited_by: UUID
    created_at: datetime
    updated_at: datetime


class SaveReviewDocumentBody(BaseModel):
    expected_document_version: int = Field(ge=1)
    blocks: tuple[ReviewDocumentBlockBody, ...]


class ConsentBody(BaseModel):
    action: Literal["grant", "deny", "revoke", "reapply", "expire"]
    expected_consent_version: int = Field(ge=0)
    provider_config_revision: int = Field(ge=1)
    policy_version: str = Field(min_length=1, max_length=128)
    data_scope: dict[str, object]
    expires_at: datetime | None = None


class ConsentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    review_job_id: UUID
    paper_version_id: UUID
    service: str
    provider_config_revision: int
    policy_version: str
    data_scope: dict[str, object]
    status: str
    generation: int
    version: int
    decided_by: UUID | None
    decided_at: datetime | None
    expires_at: datetime | None
    superseded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class FindingDecisionBody(BaseModel):
    finding_id: str = Field(min_length=1, max_length=256)
    finding_revision: int = Field(ge=1)
    action: Literal["accept", "rewrite", "downgrade", "delete"]
    expected_review_version: int = Field(ge=1)
    expected_document_version: int = Field(ge=1)
    last_decision_event_id: UUID | None = None
    rewrite_text: str | None = Field(default=None, max_length=100_000)


class FindingDecisionView(BaseModel):
    review_version: int
    document_id: UUID
    document_version: int
    base_decision_event_id: UUID


class SubmitExportBody(BaseModel):
    expected_review_version: int = Field(ge=1)
    expected_document_version: int = Field(ge=1)
    expected_decision_event_id: UUID | None = None
    format: Literal["markdown"] = "markdown"


class ExportSubmissionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    export_id: UUID
    job_id: UUID
    status: str
    document_version: int
    format: str


class ReportVersionView(BaseModel):
    id: UUID
    revision: int
    schema_version: int
    content_sha256: str
    status: str
    created_by: UUID
    created_at: datetime
    published_at: datetime | None
    artifact_id: UUID
    download_url: str


def _bootstrap(request: Request, actor: ManagementActor):
    return WorkspaceBootstrapService(request.app.state.dependencies.uow_factory).get(actor)


def _project_id(request: Request, actor: ManagementActor) -> UUID:
    state = _bootstrap(request, actor)
    if state.project is None:
        from peerassist.platform.errors import NotFound

        raise NotFound(details={"reason": state.status})
    return state.project.id


def _workspace_service(request: Request) -> ReviewWorkspaceService:
    dependencies = request.app.state.dependencies
    return ReviewWorkspaceService(dependencies.uow_factory, dependencies.object_store)


@router.get("", operation_id="v1_get_teacher_workspace", response_model=WorkspaceView)
def get_workspace(request: Request, actor: ManagementActor) -> WorkspaceView:
    state = _bootstrap(request, actor)
    papers = []
    reviews = []
    if state.project is not None:
        papers = list(
            PaperService(
                request.app.state.dependencies.uow_factory,
                request.app.state.dependencies.object_store,
            ).list_papers(actor, state.project.id)
        )
        reviews = list(
            ReviewService(request.app.state.dependencies.uow_factory).list(
                actor, state.project.id
            )
        )
    return WorkspaceView(
        status=state.status,
        organization=state.organization,
        project=state.project,
        projects=list(state.projects),
        membership=state.membership,
        papers=papers,
        reviews=reviews,
    )


@router.put(
    "/project",
    operation_id="v1_select_teacher_workspace_project",
    response_model=WorkspaceView,
)
def select_workspace_project(
    body: SelectWorkspaceProjectBody,
    request: Request,
    actor: ManagementActor,
) -> WorkspaceView:
    WorkspaceBootstrapService(request.app.state.dependencies.uow_factory).select(
        actor, body.project_id
    )
    return get_workspace(request, actor)


@router.post(
    "/papers",
    operation_id="v1_workspace_upload_paper",
    response_model=PaperUploadView,
    status_code=status.HTTP_201_CREATED,
)
def upload_paper(
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
    file: UploadFile = File(),
    maximum_size_bytes: int = Query(default=100 * 1024 * 1024, ge=1, le=100 * 1024 * 1024),
) -> PaperUploadResult:
    project_id = _project_id(request, actor)
    dependencies = request.app.state.dependencies
    return PaperService(dependencies.uow_factory, dependencies.object_store).upload(
        actor,
        UploadPaper(
            project_id,
            file.filename or "paper.pdf",
            file.content_type or "application/octet-stream",
            maximum_size_bytes,
            idempotency_key,
            request.state.request_id,
        ),
        file.file,
    )


@router.post(
    "/reviews",
    operation_id="v1_workspace_create_review",
    response_model=ReviewJobView,
    status_code=status.HTTP_201_CREATED,
)
def create_review(
    body: CreateWorkspaceReviewBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> object:
    project_id = _project_id(request, actor)
    return ReviewService(request.app.state.dependencies.uow_factory).create(
        actor,
        CreateReviewJob(
            project_id,
            body.paper_version_id,
            body.mode,
            idempotency_key,
            request.state.request_id,
        ),
    )


@router.get(
    "/reviews/{job_id}",
    operation_id="v1_workspace_get_review",
    response_model=ReviewJobView,
)
def get_review(job_id: UUID, request: Request, actor: ManagementActor) -> object:
    return ReviewService(request.app.state.dependencies.uow_factory).get(
        actor, _project_id(request, actor), job_id
    )


def _change_review(
    operation: str,
    job_id: UUID,
    body: ChangeWorkspaceReviewBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: str,
) -> object:
    service = ReviewService(request.app.state.dependencies.uow_factory)
    command = ChangeReviewJob(
        _project_id(request, actor),
        job_id,
        body.expected_version,
        idempotency_key,
        request.state.request_id,
    )
    return service.cancel(actor, command) if operation == "cancel" else service.retry(actor, command)


@router.post(
    "/reviews/{job_id}/cancel",
    operation_id="v1_workspace_cancel_review",
    response_model=ReviewJobView,
)
def cancel_review(
    job_id: UUID,
    body: ChangeWorkspaceReviewBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> object:
    return _change_review("cancel", job_id, body, request, actor, idempotency_key)


@router.post(
    "/reviews/{job_id}/retry",
    operation_id="v1_workspace_retry_review",
    response_model=ReviewJobView,
)
def retry_review(
    job_id: UUID,
    body: ChangeWorkspaceReviewBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> object:
    return _change_review("retry", job_id, body, request, actor, idempotency_key)


@router.delete(
    "/reviews/{job_id}",
    operation_id="v1_workspace_delete_review",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_review(
    job_id: UUID,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> Response:
    project_id = _project_id(request, actor)
    deleted, object_ids = ReviewService(
        request.app.state.dependencies.uow_factory
    ).delete(
        actor,
        DeleteReviewJob(
            project_id,
            job_id,
            idempotency_key,
            request.state.request_id,
        ),
    )
    from peerassist.platform.errors import DependencyUnavailable, NotFound
    from peerassist.platform.models import TenantScope

    scope = TenantScope(deleted.organization_id, deleted.project_id)
    for object_id in object_ids:
        try:
            request.app.state.dependencies.object_store.tombstone(scope, object_id)
        except (DependencyUnavailable, NotFound):
            continue
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/reviews/{job_id}/document",
    operation_id="v1_workspace_get_review_document",
    response_model=ReviewDocumentView,
)
def get_document(job_id: UUID, request: Request, actor: ManagementActor) -> ReviewDocument:
    return _workspace_service(request).get_document(
        actor, _project_id(request, actor), job_id
    )


@router.put(
    "/reviews/{job_id}/document",
    operation_id="v1_workspace_save_review_document",
    response_model=ReviewDocumentView,
)
def save_document(
    job_id: UUID,
    body: SaveReviewDocumentBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ReviewDocument:
    project_id = _project_id(request, actor)
    return _workspace_service(request).save_document(
        actor,
        SaveReviewDocument(
            project_id,
            job_id,
            tuple(block.as_domain() for block in body.blocks),
            body.expected_document_version,
            idempotency_key,
            request.state.request_id,
        ),
    )


@router.post(
    "/reviews/{job_id}/consents/{service}",
    operation_id="v1_workspace_change_review_consent",
    response_model=ConsentView,
)
def change_consent(
    job_id: UUID,
    service: str,
    body: ConsentBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ExternalServiceConsent:
    project_id = _project_id(request, actor)
    job = ReviewService(request.app.state.dependencies.uow_factory).get(
        actor, project_id, job_id
    )
    return _workspace_service(request).change_consent(
        actor,
        ChangeExternalServiceConsent(
            project_id=project_id,
            job_id=job_id,
            paper_version_id=job.paper_version_id,
            service=service,
            provider_config_revision=body.provider_config_revision,
            policy_version=body.policy_version,
            data_scope=body.data_scope,
            action=body.action,
            expected_consent_version=body.expected_consent_version,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            expires_at=body.expires_at,
        ),
    )


@router.get(
    "/reviews/{job_id}/evidence",
    operation_id="v1_workspace_get_review_evidence",
    response_model=dict[str, object],
)
def get_evidence(job_id: UUID, request: Request, actor: ManagementActor) -> object:
    return _workspace_service(request).get_evidence(
        actor, _project_id(request, actor), job_id
    )


@router.post(
    "/reviews/{job_id}/findings/{finding_lineage_id}/decision",
    operation_id="v1_workspace_decide_review_finding",
    response_model=FindingDecisionView,
)
def decide_finding(
    job_id: UUID,
    finding_lineage_id: str,
    body: FindingDecisionBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> FindingDecisionResult:
    return _workspace_service(request).decide_finding(
        actor,
        DecideFinding(
            project_id=_project_id(request, actor),
            job_id=job_id,
            finding_lineage_id=finding_lineage_id,
            finding_id=body.finding_id,
            finding_revision=body.finding_revision,
            action=body.action,
            expected_review_version=body.expected_review_version,
            expected_document_version=body.expected_document_version,
            last_decision_event_id=body.last_decision_event_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            rewrite_text=body.rewrite_text,
        ),
    )


@router.post(
    "/reviews/{job_id}/exports",
    operation_id="v1_workspace_submit_review_export",
    response_model=ExportSubmissionView,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_export(
    job_id: UUID,
    body: SubmitExportBody,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
) -> ExportSubmission:
    return _workspace_service(request).submit_export(
        actor,
        SubmitReviewExport(
            project_id=_project_id(request, actor),
            job_id=job_id,
            expected_review_version=body.expected_review_version,
            expected_document_version=body.expected_document_version,
            expected_decision_event_id=body.expected_decision_event_id,
            format=body.format,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        ),
    )


@router.get(
    "/reviews/{job_id}/exports",
    operation_id="v1_workspace_list_review_exports",
    response_model=list[ReportVersionView],
)
def list_exports(
    job_id: UUID,
    request: Request,
    actor: ManagementActor,
) -> list[ReportVersionView]:
    records = _workspace_service(request).list_exports(
        actor, _project_id(request, actor), job_id
    )
    return [
        ReportVersionView(
            id=record.report.id,
            revision=record.report.revision,
            schema_version=record.report.schema_version,
            content_sha256=record.report.content_sha256,
            status=record.report.status,
            created_by=record.report.created_by,
            created_at=record.report.created_at,
            published_at=record.report.published_at,
            artifact_id=record.artifact.id,
            download_url=f"/api/v1/workspace/reviews/{job_id}/exports/{record.report.id}",
        )
        for record in records
    ]


@router.get(
    "/reviews/{job_id}/exports/{report_version_id}",
    operation_id="v1_workspace_download_review_export",
)
def download_export(
    job_id: UUID,
    report_version_id: UUID,
    request: Request,
    actor: ManagementActor,
) -> StreamingResponse:
    project_id = _project_id(request, actor)
    record = _workspace_service(request).get_export(
        actor, project_id, job_id, report_version_id
    )
    source = ArtifactService(
        request.app.state.dependencies.uow_factory,
        request.app.state.dependencies.object_store,
    ).open(actor, project_id, job_id, record.artifact.id)
    return StreamingResponse(
        source.stream,
        media_type=record.artifact.object.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="review-{record.report.revision}.md"',
            "ETag": f'"{record.artifact.object.sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
