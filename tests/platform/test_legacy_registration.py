from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from peerassist.platform.adapters.legacy import LocalLegacyReader
from peerassist.platform.errors import DependencyUnavailable, NotFound
from peerassist.platform.models import LegacyRegistration, TenantScope


def _registration(scope: TenantScope, locator: str, digest: str) -> LegacyRegistration:
    return LegacyRegistration(
        uuid4(), scope.organization_id, scope.project_id, "m0_review_job",
        locator, digest, "read_only", 1, datetime(2026, 7, 18, 19, tzinfo=UTC),
    )


def test_legacy_reader_verifies_manifest_digest_and_returns_safe_job(tmp_path) -> None:
    scope = TenantScope(uuid4(), uuid4())
    locator = str(uuid4())
    job_dir = tmp_path / locator
    job_dir.mkdir()
    payload = {
        "id": locator,
        "paper_id": "a" * 64,
        "mode": "fast",
        "stage": "await_confirmation",
        "status": "awaiting_human_confirmation",
        "revision": 7,
        "created_at": "2026-07-18T18:00:00Z",
        "updated_at": "2026-07-18T19:00:00Z",
    }
    manifest = json.dumps(payload).encode()
    (job_dir / "job.json").write_bytes(manifest)
    registration = _registration(scope, locator, hashlib.sha256(manifest).hexdigest())

    job = LocalLegacyReader(tmp_path).read_job(scope, registration)

    assert job.id.hex == locator.replace("-", "")
    assert job.status == "awaiting_human_confirmation" and job.version == 7


def test_legacy_reader_rejects_digest_change_and_symlink_escape(tmp_path) -> None:
    scope = TenantScope(uuid4(), uuid4())
    locator = str(uuid4())
    outside = tmp_path.parent / f"outside-{uuid4()}"
    outside.mkdir()
    (outside / "job.json").write_text("{}", encoding="utf-8")
    (tmp_path / locator).symlink_to(outside, target_is_directory=True)
    registration = _registration(scope, locator, "a" * 64)

    with pytest.raises(NotFound):
        LocalLegacyReader(tmp_path).read_job(scope, registration)

    (tmp_path / locator).unlink()
    (tmp_path / locator).mkdir()
    (tmp_path / locator / "job.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DependencyUnavailable):
        LocalLegacyReader(tmp_path).read_job(scope, registration)
