"""Project-scoped paper upload and immutable source routes."""

from __future__ import annotations

from email.utils import format_datetime
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Header, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from peerassist.platform.models import Paper, PaperVersion
from peerassist.platform.services.papers import PaperService, PaperUploadResult, UploadPaper

from .organizations import ManagementActor

router = APIRouter(prefix="/api/v1", tags=["papers"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


class PaperView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    project_id: UUID
    content_sha256: str
    current_version_id: UUID
    status: str
    version: int


class PaperVersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    project_id: UUID
    paper_id: UUID
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    revision: int


class PaperUploadView(BaseModel):
    paper: PaperView
    version: PaperVersionView


def _service(request: Request) -> PaperService:
    dependencies = request.app.state.dependencies
    return PaperService(dependencies.uow_factory, dependencies.object_store)


@router.post(
    "/projects/{project_id}/papers",
    operation_id="v1_upload_paper",
    response_model=PaperUploadView,
    status_code=status.HTTP_201_CREATED,
)
def upload_paper(
    project_id: UUID,
    request: Request,
    actor: ManagementActor,
    idempotency_key: IdempotencyKey,
    file: UploadFile = File(),
    maximum_size_bytes: int = Query(default=100 * 1024 * 1024, ge=1, le=100 * 1024 * 1024),
) -> PaperUploadResult:
    return _service(request).upload(
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


@router.get(
    "/projects/{project_id}/papers",
    operation_id="v1_list_papers",
    response_model=list[PaperView],
)
def list_papers(project_id: UUID, request: Request, actor: ManagementActor) -> tuple[Paper, ...]:
    return _service(request).list_papers(actor, project_id)


@router.get(
    "/projects/{project_id}/papers/{paper_id}/source",
    operation_id="v1_get_paper_source",
)
@router.head(
    "/projects/{project_id}/papers/{paper_id}/source",
    operation_id="v1_head_paper_source",
)
def paper_source(
    project_id: UUID,
    paper_id: UUID,
    request: Request,
    actor: ManagementActor,
) -> Response:
    service = _service(request)
    source = service.open_source(actor, project_id, paper_id)
    headers = _source_headers(source.version)
    range_header = request.headers.get("range", "")
    if request.method == "HEAD":
        source.stream.close()
        headers["Content-Length"] = str(source.version.size_bytes)
        return Response(status_code=200, headers=headers, media_type=source.version.media_type)
    if not range_header:
        headers["Content-Length"] = str(source.version.size_bytes)
        return StreamingResponse(
            source.stream,
            status_code=200,
            headers=headers,
            media_type=source.version.media_type,
        )
    bounds = _parse_range(range_header, source.version.size_bytes)
    source.stream.close()
    if bounds is None:
        return Response(
            status_code=416,
            headers={**headers, "Content-Range": f"bytes */{source.version.size_bytes}"},
        )
    start, end = bounds
    _, version, content = service.read_range(actor, project_id, paper_id, start, end)
    return Response(
        content=content,
        status_code=206,
        media_type=version.media_type,
        headers={
            **headers,
            "Content-Length": str(len(content)),
            "Content-Range": f"bytes {start}-{end}/{version.size_bytes}",
        },
    )


def _source_headers(version: PaperVersion) -> dict[str, str]:
    ascii_filename = version.filename.encode("ascii", "ignore").decode("ascii") or "paper.pdf"
    ascii_filename = ascii_filename.replace('"', "")
    return {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600, immutable",
        "ETag": f'"{version.sha256}"',
        "Last-Modified": format_datetime(version.created_at, usegmt=True),
        "Content-Disposition": (
            f'inline; filename="{ascii_filename}"; '
            f"filename*=UTF-8''{quote(version.filename, safe='')}"
        ),
    }


def _parse_range(value: str, size: int) -> tuple[int, int] | None:
    if not value.startswith("bytes=") or "," in value or size <= 0:
        return None
    token = value.removeprefix("bytes=").strip()
    start_text, separator, end_text = token.partition("-")
    if not separator:
        return None
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                return None
            return max(0, size - suffix), size - 1
        start = int(start_text)
        end = size - 1 if not end_text else int(end_text)
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)
