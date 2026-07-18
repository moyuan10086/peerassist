"""S3-compatible immutable tenant object storage."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, BinaryIO
from uuid import UUID, uuid4

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from ..errors import DependencyUnavailable, ImmutableResource, NotFound, PayloadTooLarge
from ..models import (
    DownloadDescriptor,
    ObjectDescriptor,
    TemporaryObjectDescriptor,
    TenantScope,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _TemporaryReservation:
    scope: TenantScope
    maximum_size_bytes: int
    descriptor: TemporaryObjectDescriptor | None = None


class S3ObjectStore:
    """Store immutable objects under non-enumerable tenant-prefixed keys."""

    name = "object_store"

    def __init__(
        self,
        client: Any,
        bucket: str,
        *,
        clock=_utc_now,
    ) -> None:
        if not bucket.strip():
            raise ValueError("bucket must not be empty")
        self._client = client
        self._bucket = bucket
        self._clock = clock
        self._lock = threading.RLock()
        self._temporary: dict[UUID, _TemporaryReservation] = {}

    @classmethod
    def from_endpoint(
        cls,
        *,
        endpoint: str,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        path_style: bool,
        clock=_utc_now,
    ) -> S3ObjectStore:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if path_style else "virtual"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        return cls(client, bucket, clock=clock)

    async def start(self) -> None:
        if not await self.check():
            raise DependencyUnavailable()

    async def stop(self) -> None:
        await asyncio.to_thread(self._client.close)

    async def check(self) -> bool:
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
            return True
        except (BotoCoreError, ClientError, OSError):
            return False

    def create_temporary(self, scope: TenantScope, maximum_size_bytes: int) -> UUID:
        if maximum_size_bytes <= 0:
            raise ValueError("maximum_size_bytes must be positive")
        with self._lock:
            upload_id = uuid4()
            self._temporary[upload_id] = _TemporaryReservation(scope, maximum_size_bytes)
            return upload_id

    def write_temporary(
        self,
        scope: TenantScope,
        upload_id: UUID,
        source: BinaryIO,
    ) -> TemporaryObjectDescriptor:
        with self._lock:
            reservation = self._temporary.get(upload_id)
            if reservation is None or reservation.scope != scope:
                raise NotFound()
            digest = hashlib.sha256()
            size = 0
            try:
                with tempfile.SpooledTemporaryFile(
                    max_size=8 * 1024 * 1024, mode="w+b"
                ) as spool:
                    while chunk := source.read(
                        min(64 * 1024, reservation.maximum_size_bytes + 1)
                    ):
                        if not isinstance(chunk, bytes):
                            raise TypeError("temporary object source must yield bytes")
                        size += len(chunk)
                        if size > reservation.maximum_size_bytes:
                            raise PayloadTooLarge(
                                details={"limit": reservation.maximum_size_bytes}
                            )
                        digest.update(chunk)
                        spool.write(chunk)
                    descriptor = TemporaryObjectDescriptor(upload_id, size, digest.hexdigest())
                    spool.seek(0)
                    self._client.put_object(
                        Bucket=self._bucket,
                        Key=self._temporary_key(scope, upload_id),
                        Body=spool,
                        ContentLength=size,
                        ContentType="application/octet-stream",
                        Metadata={
                            "sha256": descriptor.sha256,
                            "size-bytes": str(size),
                            "state": "temporary",
                        },
                    )
                    reservation.descriptor = descriptor
                    return descriptor
            except (BotoCoreError, ClientError, OSError) as error:
                raise DependencyUnavailable(cause=error) from None

    def publish(
        self,
        scope: TenantScope,
        temporary: TemporaryObjectDescriptor,
        object_id: str,
    ) -> ObjectDescriptor:
        with self._lock:
            reservation = self._temporary.get(temporary.upload_id)
            if (
                reservation is None
                or reservation.scope != scope
                or reservation.descriptor is None
            ):
                raise NotFound()
            if reservation.descriptor != temporary:
                raise ValueError("temporary object size or digest does not match stored bytes")
            if self._head(self._tombstone_key(scope, object_id)) is not None:
                raise ImmutableResource()
            existing = self.metadata(scope, object_id)
            if existing is not None:
                if existing.sha256 == temporary.sha256:
                    return existing
                raise ImmutableResource()
            temporary_key = self._temporary_key(scope, temporary.upload_id)
            body = None
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=temporary_key)
                body = response["Body"]
                with tempfile.SpooledTemporaryFile(
                    max_size=8 * 1024 * 1024, mode="w+b"
                ) as verified:
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := body.read(64 * 1024):
                        size += len(chunk)
                        if size > temporary.size_bytes:
                            raise DependencyUnavailable()
                        digest.update(chunk)
                        verified.write(chunk)
                    if size != temporary.size_bytes or digest.hexdigest() != temporary.sha256:
                        raise DependencyUnavailable()
                    verified.seek(0)
                    self._client.put_object(
                        Bucket=self._bucket,
                        Key=self._object_key(scope, object_id),
                        Body=verified,
                        ContentLength=temporary.size_bytes,
                        ContentType="application/octet-stream",
                        Metadata={
                            "sha256": temporary.sha256,
                            "size-bytes": str(temporary.size_bytes),
                            "object-id-sha256": self._object_id_digest(object_id),
                            "state": "published",
                        },
                        IfNoneMatch="*",
                    )
            except ClientError as error:
                code = str(error.response.get("Error", {}).get("Code", ""))
                if code not in {"PreconditionFailed", "ConditionalRequestConflict", "412"}:
                    raise DependencyUnavailable(cause=error) from None
                existing = self.metadata(scope, object_id)
                if existing is None or existing.sha256 != temporary.sha256:
                    raise ImmutableResource() from None
                return existing
            except (BotoCoreError, OSError) as error:
                raise DependencyUnavailable(cause=error) from None
            finally:
                if body is not None:
                    body.close()
            descriptor = self.metadata(scope, object_id)
            if descriptor is None or descriptor.sha256 != temporary.sha256:
                raise DependencyUnavailable()
            return descriptor

    def metadata(self, scope: TenantScope, object_id: str) -> ObjectDescriptor | None:
        if self._head(self._tombstone_key(scope, object_id)) is not None:
            return None
        response = self._head(self._object_key(scope, object_id))
        if response is None:
            return None
        metadata = response.get("Metadata", {})
        if metadata.get("object-id-sha256") != self._object_id_digest(object_id):
            return None
        try:
            size = int(metadata["size-bytes"])
            sha256 = metadata["sha256"]
            media_type = str(response.get("ContentType") or "application/octet-stream")
            if size != int(response["ContentLength"]):
                raise ValueError
            return ObjectDescriptor(object_id, size, sha256, media_type)
        except (KeyError, TypeError, ValueError):
            raise DependencyUnavailable() from None

    def open_immutable(self, scope: TenantScope, object_id: str) -> BinaryIO:
        descriptor = self.metadata(scope, object_id)
        if descriptor is None:
            raise NotFound()
        body = None
        spool = tempfile.SpooledTemporaryFile(  # noqa: SIM115 - caller owns returned stream
            max_size=8 * 1024 * 1024, mode="w+b"
        )
        try:
            response = self._client.get_object(
                Bucket=self._bucket, Key=self._object_key(scope, object_id)
            )
            body = response["Body"]
            digest = hashlib.sha256()
            size = 0
            while chunk := body.read(64 * 1024):
                size += len(chunk)
                digest.update(chunk)
                spool.write(chunk)
            if size != descriptor.size_bytes or digest.hexdigest() != descriptor.sha256:
                raise DependencyUnavailable()
            spool.seek(0)
            return spool
        except (BotoCoreError, ClientError, OSError) as error:
            spool.close()
            raise DependencyUnavailable(cause=error) from None
        except Exception:
            spool.close()
            raise
        finally:
            if body is not None:
                body.close()

    def read_range(
        self,
        scope: TenantScope,
        object_id: str,
        start: int,
        end: int,
    ) -> bytes:
        descriptor = self.metadata(scope, object_id)
        if descriptor is None:
            raise NotFound()
        if start < 0 or end < start or end >= descriptor.size_bytes:
            raise ValueError("range exceeds object size")
        body = None
        try:
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=self._object_key(scope, object_id),
                Range=f"bytes={start}-{end}",
            )
            body = response["Body"]
            content = body.read()
            if len(content) != end - start + 1:
                raise DependencyUnavailable()
            return content
        except (BotoCoreError, ClientError, OSError) as error:
            raise DependencyUnavailable(cause=error) from None
        finally:
            if body is not None:
                body.close()

    def download_descriptor(
        self, scope: TenantScope, object_id: str, filename: str
    ) -> DownloadDescriptor:
        descriptor = self.metadata(scope, object_id)
        if descriptor is None:
            raise NotFound()
        return DownloadDescriptor(descriptor, filename)

    def tombstone(self, scope: TenantScope, object_id: str) -> None:
        with self._lock:
            if self.metadata(scope, object_id) is None:
                raise NotFound()
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=self._tombstone_key(scope, object_id),
                    Body=b"",
                    ContentLength=0,
                    Metadata={
                        "object-id-sha256": self._object_id_digest(object_id),
                        "state": "tombstone",
                    },
                    IfNoneMatch="*",
                )
                self._client.delete_object(
                    Bucket=self._bucket, Key=self._object_key(scope, object_id)
                )
            except (BotoCoreError, ClientError, OSError) as error:
                raise DependencyUnavailable(cause=error) from None

    def delete_temporary(self, scope: TenantScope, upload_id: UUID) -> None:
        with self._lock:
            reservation = self._temporary.get(upload_id)
            if reservation is None:
                return
            if reservation.scope != scope:
                raise NotFound()
            try:
                self._client.delete_object(
                    Bucket=self._bucket, Key=self._temporary_key(scope, upload_id)
                )
            except (BotoCoreError, ClientError, OSError) as error:
                raise DependencyUnavailable(cause=error) from None
            del self._temporary[upload_id]

    def _head(self, key: str) -> dict[str, Any] | None:
        try:
            return self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            status = int(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise DependencyUnavailable(cause=error) from None
        except (BotoCoreError, OSError) as error:
            raise DependencyUnavailable(cause=error) from None

    @staticmethod
    def _scope_prefix(scope: TenantScope) -> str:
        if scope.project_id is None:
            raise NotFound()
        return f"org/{scope.organization_id}/project/{scope.project_id}"

    def _temporary_key(self, scope: TenantScope, upload_id: UUID) -> str:
        return f"{self._scope_prefix(scope)}/tmp/{upload_id}"

    def _object_key(self, scope: TenantScope, object_id: str) -> str:
        return f"{self._scope_prefix(scope)}/objects/{self._object_id_digest(object_id)}"

    def _tombstone_key(self, scope: TenantScope, object_id: str) -> str:
        return f"{self._scope_prefix(scope)}/tombstones/{self._object_id_digest(object_id)}"

    @staticmethod
    def _object_id_digest(object_id: str) -> str:
        if not object_id:
            raise ValueError("object_id must not be empty")
        return hashlib.sha256(object_id.encode("utf-8")).hexdigest()
