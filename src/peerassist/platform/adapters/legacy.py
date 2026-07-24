"""Digest-verified read-only access to registered M0 review jobs."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import NAMESPACE_URL, UUID, uuid5

from ..errors import DependencyUnavailable, NotFound
from ..models import LegacyRegistration, ReviewJob, TenantScope

_MAX_MANIFEST_BYTES = 1024 * 1024


class LocalLegacyReader:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def read_job(self, scope: TenantScope, registration: LegacyRegistration) -> ReviewJob:
        payload = self._read_manifest(scope, registration)
        try:
            job_id = UUID(str(payload["id"]))
            created_at = _datetime(payload["created_at"])
            updated_at = _datetime(payload["updated_at"])
            revision = max(1, int(payload.get("revision") or 1))
        except (KeyError, TypeError, ValueError):
            raise DependencyUnavailable() from None
        if str(job_id) != registration.opaque_locator:
            raise DependencyUnavailable()
        assert scope.project_id is not None
        return ReviewJob(
            job_id,
            scope.organization_id,
            scope.project_id,
            uuid5(NAMESPACE_URL, f"legacy-paper:{payload.get('paper_id', '')}"),
            str(payload.get("mode") or "fast"),
            str(payload.get("stage") or "completed"),
            str(payload.get("status") or "completed"),
            revision,
            1,
            uuid5(registration.id, "legacy-created-by"),
            created_at,
            updated_at,
        )

    def read_workspace_facts(
        self,
        scope: TenantScope,
        registration: LegacyRegistration,
    ) -> dict[str, object]:
        payload = self._read_manifest(scope, registration)
        metadata = payload.get("metadata")
        migration = metadata.get("workspace_migration") if isinstance(metadata, dict) else None
        explicit = migration if isinstance(migration, dict) else {}
        result: dict[str, object] = {
            "paper_sha256": payload.get("paper_id"),
            "mode": payload.get("mode"),
        }
        consent = explicit.get("consent")
        sections = explicit.get("document_sections")
        if isinstance(consent, dict):
            result["consent"] = json.loads(json.dumps(consent))
        if isinstance(sections, dict):
            result["document_sections"] = json.loads(json.dumps(sections))
        return result

    def read_artifact(
        self,
        scope: TenantScope,
        registration: LegacyRegistration,
        logical_name: str,
    ) -> io.BytesIO:
        if (
            registration.organization_id != scope.organization_id
            or registration.project_id != scope.project_id
        ):
            raise NotFound()
        relative = PurePosixPath(logical_name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise NotFound()
        target = (self._job_dir(registration.opaque_locator) / Path(*relative.parts)).resolve()
        if self._root not in target.parents or target.is_symlink() or not target.is_file():
            raise NotFound()
        try:
            return io.BytesIO(target.read_bytes())
        except OSError:
            raise NotFound() from None

    def _job_dir(self, locator: str) -> Path:
        try:
            normalized = str(UUID(locator))
        except ValueError:
            raise NotFound() from None
        if normalized != locator:
            raise NotFound()
        candidate = self._root / locator
        if candidate.is_symlink():
            raise NotFound()
        resolved = candidate.resolve()
        if self._root not in resolved.parents:
            raise NotFound()
        return resolved

    def _read_manifest(
        self,
        scope: TenantScope,
        registration: LegacyRegistration,
    ) -> dict[str, object]:
        if (
            registration.organization_id != scope.organization_id
            or registration.project_id != scope.project_id
            or registration.status != "read_only"
        ):
            raise NotFound()
        manifest_path = self._job_dir(registration.opaque_locator) / "job.json"
        try:
            raw = manifest_path.read_bytes()
        except OSError:
            raise NotFound() from None
        if len(raw) > _MAX_MANIFEST_BYTES:
            raise DependencyUnavailable()
        if hashlib.sha256(raw).hexdigest() != registration.manifest_sha256:
            raise DependencyUnavailable()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise DependencyUnavailable() from None
        if not isinstance(payload, dict):
            raise DependencyUnavailable()
        return payload


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed
