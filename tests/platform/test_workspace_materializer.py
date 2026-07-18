from __future__ import annotations

import io
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from peerassist.platform.adapters.memory import MemoryObjectStore
from peerassist.platform.adapters.workspace import WorkspaceMaterializer
from peerassist.platform.models import ReviewJob, StageInputManifest, TenantScope


def test_materializer_verifies_objects_and_creates_private_fresh_workspace(tmp_path) -> None:
    store = MemoryObjectStore()
    scope = TenantScope(uuid4(), uuid4())
    upload = store.create_temporary(scope, 5)
    temporary = store.write_temporary(scope, upload, io.BytesIO(b"hello"))
    descriptor = store.publish(scope, temporary, "paper/source")
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    job = ReviewJob(
        uuid4(), scope.organization_id, scope.project_id, uuid4(), "full", "queued",
        "queued", 1, 1, uuid4(), now, now,
    )
    materializer = WorkspaceMaterializer(store, tmp_path)
    destination = tmp_path / str(job.id) / "attempt-1" / "prepare"

    workspace = materializer.materialize(
        scope, job, "prepare", StageInputManifest(0, (descriptor,)), destination
    )

    assert workspace == destination.resolve()
    assert (workspace / "inputs" / "object-000.bin").read_bytes() == b"hello"
    assert os.stat(workspace).st_mode & 0o777 == 0o700
    assert os.stat(workspace / "inputs" / "object-000.bin").st_mode & 0o777 == 0o400
    (workspace / "stale.txt").write_text("stale", encoding="utf-8")
    materializer.materialize(
        scope, job, "prepare", StageInputManifest(0, (descriptor,)), destination
    )
    assert not (workspace / "stale.txt").exists()


def test_materializer_rejects_destinations_outside_scratch_and_symlinks(tmp_path) -> None:
    store = MemoryObjectStore()
    materializer = WorkspaceMaterializer(store, tmp_path / "scratch")
    scope = TenantScope(uuid4(), uuid4())
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    job = ReviewJob(
        uuid4(), scope.organization_id, scope.project_id, uuid4(), "full", "queued",
        "queued", 1, 1, uuid4(), now, now,
    )
    with pytest.raises(ValueError, match="scratch root"):
        materializer.materialize(scope, job, "prepare", StageInputManifest(0, ()), tmp_path / "outside")
