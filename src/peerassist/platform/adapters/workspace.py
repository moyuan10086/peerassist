"""Private scratch materialization and immutable stage artifact publication."""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
from pathlib import Path

from ..models import (
    ObjectDescriptor,
    ReviewJob,
    StageInputManifest,
    StageOutputSpec,
    TenantScope,
)
from ..ports import ObjectStore


def _contained(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError("workspace destination must remain inside scratch root")
    return resolved


class WorkspaceMaterializer:
    def __init__(self, object_store: ObjectStore, scratch_root: Path) -> None:
        self._object_store = object_store
        self._scratch_root = scratch_root.resolve()

    def materialize(
        self,
        scope: TenantScope,
        job: ReviewJob,
        stage: str,
        input_manifest: StageInputManifest,
        destination: Path,
    ) -> Path:
        del job
        if not stage.strip():
            raise ValueError("stage must not be blank")
        workspace = _contained(self._scratch_root, destination)
        if destination.is_symlink():
            raise ValueError("workspace destination must not be a symlink")
        if workspace.exists():
            shutil.rmtree(workspace)
        inputs = workspace / "inputs"
        outputs = workspace / "outputs"
        inputs.mkdir(parents=True, mode=0o700)
        outputs.mkdir(mode=0o700)
        os.chmod(workspace, 0o700)
        os.chmod(inputs, 0o700)
        os.chmod(outputs, 0o700)
        rows: list[dict[str, object]] = []
        try:
            for index, descriptor in enumerate(input_manifest.objects):
                stored = self._object_store.metadata(scope, descriptor.object_id)
                if stored != descriptor:
                    raise ValueError("input object descriptor does not match immutable storage")
                target = inputs / f"object-{index:03d}.bin"
                stream = self._object_store.open_immutable(scope, descriptor.object_id)
                try:
                    with target.open("xb") as output:
                        shutil.copyfileobj(stream, output, length=64 * 1024)
                finally:
                    stream.close()
                if target.stat().st_size != descriptor.size_bytes:
                    raise ValueError("materialized input size does not match descriptor")
                os.chmod(target, 0o400)
                rows.append(
                    {
                        "filename": target.name,
                        "media_type": descriptor.media_type,
                        "object_id": descriptor.object_id,
                        "sha256": descriptor.sha256,
                        "size_bytes": descriptor.size_bytes,
                    }
                )
            manifest_path = workspace / "input-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {"objects": rows, "revision": input_manifest.revision},
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            os.chmod(manifest_path, 0o400)
            return workspace
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise

    def cleanup(self, workspace: Path) -> None:
        target = _contained(self._scratch_root, workspace)
        if target.exists() and not target.is_symlink():
            shutil.rmtree(target)
        parent = target.parent
        while parent != self._scratch_root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


class StageArtifactPublisher:
    def __init__(self, object_store: ObjectStore) -> None:
        self._object_store = object_store

    def publish(
        self,
        scope: TenantScope,
        job: ReviewJob,
        stage: str,
        output_spec: StageOutputSpec,
        workspace: Path,
    ) -> tuple[ObjectDescriptor, ...]:
        output_root = (workspace / "outputs").resolve()
        if not output_root.is_dir() or output_root.is_symlink():
            raise ValueError("workspace outputs directory is missing or unsafe")
        descriptors: list[ObjectDescriptor] = []
        total_size = 0
        for logical_name in output_spec.logical_names:
            source = _contained(output_root, output_root / logical_name)
            if source.is_symlink() or not source.is_file():
                raise ValueError("declared output must be a regular file")
            size = source.stat().st_size
            total_size += size
            if total_size > output_spec.maximum_size_bytes:
                raise ValueError("declared outputs exceed maximum size")
            upload_id = self._object_store.create_temporary(scope, size or 1)
            try:
                with source.open("rb") as stream:
                    temporary = self._object_store.write_temporary(scope, upload_id, stream)
                object_id = (
                    f"review-jobs/{job.id}/attempts/{job.attempt}/{stage}/{logical_name}"
                )
                descriptor = self._object_store.publish(scope, temporary, object_id)
                media_type = _media_type(source)
                if descriptor.media_type != media_type:
                    descriptor = ObjectDescriptor(
                        descriptor.object_id,
                        descriptor.size_bytes,
                        descriptor.sha256,
                        media_type,
                        descriptor.schema_version,
                    )
                descriptors.append(descriptor)
            finally:
                self._object_store.delete_temporary(scope, upload_id)
        return tuple(descriptors)


def _media_type(path: Path) -> str:
    if path.suffix.casefold() in {".md", ".markdown"}:
        return "text/markdown; charset=utf-8"
    if path.suffix.casefold() == ".json":
        return "application/json"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
