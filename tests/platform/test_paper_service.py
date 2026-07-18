from __future__ import annotations

import io
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from peerassist.platform.adapters.memory import MemoryObjectStore, MemoryUnitOfWorkFactory
from peerassist.platform.errors import Forbidden, IdempotencyConflict, InvalidUpload, NotFound
from peerassist.platform.models import (
    Actor,
    ActorKind,
    Organization,
    Project,
    ProjectMembership,
    Role,
    TenantScope,
    User,
)
from peerassist.platform.services.papers import PaperService, UploadPaper


def _seed():
    now = datetime(2026, 7, 18, 15, 0, tzinfo=UTC)
    factory = MemoryUnitOfWorkFactory(clock=lambda: now)
    store = MemoryObjectStore(clock=lambda: now)
    actors = {
        role: Actor(uuid4(), ActorKind.USER)
        for role in ("owner", "reviewer", "viewer", "outsider")
    }
    organization = Organization(uuid4(), "paper-org", "Paper Org", "active", 1, now, now)
    project = Project(uuid4(), organization.id, "Paper Project", "active", 1, now, now)
    with factory(actors["owner"]) as uow:
        for role, actor in actors.items():
            uow.users.add(User(actor.actor_id, "active", role, now, now))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.projects.add(project.scope, project)
        for role in ("owner", "reviewer", "viewer"):
            uow.projects.save_membership(
                project.scope,
                ProjectMembership(
                    uuid4(), organization.id, project.id, actors[role].actor_id,
                    Role.PROJECT_OWNER if role == "owner" else Role(role),
                    "active", 1, now, now,
                ),
                None,
            )
        uow.commit()
    return PaperService(factory, store, clock=lambda: now), store, actors, project


def _upload(project_id, *, key: str, filename: str = "paper.pdf") -> UploadPaper:
    return UploadPaper(
        project_id=project_id,
        filename=filename,
        media_type="application/pdf",
        maximum_size_bytes=1024,
        idempotency_key=key,
        request_id=f"request-{key}",
    )


def test_upload_replays_project_digest_and_persists_immutable_source() -> None:
    service, store, actors, project = _seed()
    content = b"%PDF-1.7\nsynthetic public fixture\n%%EOF\n"

    created = service.upload(
        actors["reviewer"], _upload(project.id, key="first"), io.BytesIO(content)
    )
    replay = service.upload(
        actors["reviewer"],
        _upload(project.id, key="same-digest", filename="renamed.pdf"),
        io.BytesIO(content),
    )

    assert replay == created
    assert replay.version.filename == "paper.pdf"
    assert service.list_papers(actors["viewer"], project.id) == (created.paper,)
    assert store.metadata(project.scope, created.version.source_object_id) is not None
    assert service.open_source(actors["viewer"], project.id, created.paper.id).stream.read() == content


def test_upload_permissions_validation_and_idempotency_fail_closed() -> None:
    service, _, actors, project = _seed()
    valid = b"%PDF-1.7\nvalid\n%%EOF\n"
    service.upload(actors["reviewer"], _upload(project.id, key="fixed"), io.BytesIO(valid))

    with pytest.raises(IdempotencyConflict):
        service.upload(
            actors["reviewer"],
            _upload(project.id, key="fixed"),
            io.BytesIO(b"%PDF-1.7\nchanged\n%%EOF\n"),
        )
    with pytest.raises(Forbidden):
        service.upload(actors["viewer"], _upload(project.id, key="viewer"), io.BytesIO(valid))
    with pytest.raises(NotFound):
        service.upload(actors["outsider"], _upload(project.id, key="outsider"), io.BytesIO(valid))
    with pytest.raises(InvalidUpload):
        service.upload(
            actors["reviewer"], _upload(project.id, key="not-pdf"), io.BytesIO(b"not a pdf")
        )
    with pytest.raises(InvalidUpload):
        service.upload(
            actors["reviewer"],
            _upload(project.id, key="unsafe-name", filename='paper.pdf\r\nX-Injected: yes'),
            io.BytesIO(valid),
        )
