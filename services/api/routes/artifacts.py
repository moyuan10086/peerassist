"""Review artifact metadata and immutable downloads."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from peerassist.platform.models import Artifact
from peerassist.platform.services.artifacts import ArtifactService

from .organizations import ManagementActor

router = APIRouter(prefix="/api/v1", tags=["artifacts"])


class ObjectDescriptorView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    object_id: str
    size_bytes: int
    sha256: str
    media_type: str
    schema_version: int


class ArtifactView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    project_id: UUID
    job_id: UUID
    logical_name: str
    status: str
    created_at: datetime
    object: ObjectDescriptorView


def _service(request: Request) -> ArtifactService:
    dependencies = request.app.state.dependencies
    return ArtifactService(dependencies.uow_factory, dependencies.object_store)


@router.get(
    "/projects/{project_id}/review-jobs/{job_id}/artifacts",
    operation_id="v1_list_review_artifacts",
    response_model=list[ArtifactView],
)
def list_artifacts(
    project_id: UUID, job_id: UUID, request: Request, actor: ManagementActor
) -> tuple[Artifact, ...]:
    return _service(request).list(actor, project_id, job_id)


@router.get(
    "/projects/{project_id}/review-jobs/{job_id}/artifacts/{artifact_id}",
    operation_id="v1_get_review_artifact",
)
@router.head(
    "/projects/{project_id}/review-jobs/{job_id}/artifacts/{artifact_id}",
    operation_id="v1_head_review_artifact",
)
def get_artifact(
    project_id: UUID,
    job_id: UUID,
    artifact_id: UUID,
    request: Request,
    actor: ManagementActor,
) -> Response:
    source = _service(request).open(actor, project_id, job_id, artifact_id)
    descriptor = source.artifact.object
    headers = {
        "Cache-Control": "private, max-age=3600, immutable",
        "Content-Length": str(descriptor.size_bytes),
        "ETag": f'"{descriptor.sha256}"',
        "X-Content-Type-Options": "nosniff",
    }
    if request.method == "HEAD":
        source.stream.close()
        return Response(headers=headers, media_type=descriptor.media_type)
    return StreamingResponse(source.stream, headers=headers, media_type=descriptor.media_type)
