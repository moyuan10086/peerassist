from __future__ import annotations

import hashlib
import io
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from peerassist.platform.errors import ImmutableResource, NotFound, PayloadTooLarge
from peerassist.platform.models import TemporaryObjectDescriptor, TenantScope


def test_bounded_temp_write_reports_actual_size_and_digest(object_store_factory) -> None:
    store = object_store_factory()
    scope = TenantScope(uuid4(), uuid4())
    upload = store.create_temporary(scope, 5)
    descriptor = store.write_temporary(scope, upload, io.BytesIO(b"hello"))
    assert descriptor.size_bytes == 5
    assert descriptor.sha256 == hashlib.sha256(b"hello").hexdigest()
    too_large = store.create_temporary(scope, 4)
    with pytest.raises(PayloadTooLarge):
        store.write_temporary(scope, too_large, io.BytesIO(b"hello"))


def test_conditional_immutable_publish_denies_overwrite_but_replays_same_digest(object_store_factory) -> None:
    store = object_store_factory()
    scope = TenantScope(uuid4(), uuid4())
    object_id = f"org/{scope.organization_id}/project/{scope.project_id}/paper/source.pdf"
    upload = store.create_temporary(scope, 5)
    temporary = store.write_temporary(scope, upload, io.BytesIO(b"hello"))
    published = store.publish(scope, temporary, object_id)
    assert store.publish(scope, temporary, object_id) == published
    other_upload = store.create_temporary(scope, 5)
    other = store.write_temporary(scope, other_upload, io.BytesIO(b"other"))
    with pytest.raises(ImmutableResource):
        store.publish(scope, other, object_id)


def test_publish_validates_descriptor_digest_and_tenant(object_store_factory) -> None:
    store = object_store_factory()
    scope = TenantScope(uuid4(), uuid4())
    upload = store.create_temporary(scope, 5)
    temporary = store.write_temporary(scope, upload, io.BytesIO(b"hello"))
    forged = TemporaryObjectDescriptor(upload, 5, "0" * 64)
    with pytest.raises(ValueError, match="digest"):
        store.publish(scope, forged, "object-1")
    with pytest.raises(NotFound):
        store.publish(TenantScope(uuid4(), scope.project_id), temporary, "object-1")


def test_exact_range_reads_and_cross_tenant_hiding(object_store_factory) -> None:
    store = object_store_factory()
    scope = TenantScope(uuid4(), uuid4())
    upload = store.create_temporary(scope, 10)
    temporary = store.write_temporary(scope, upload, io.BytesIO(b"0123456789"))
    store.publish(scope, temporary, "object-1")
    assert store.read_range(scope, "object-1", 2, 5) == b"2345"
    assert store.open_immutable(scope, "object-1").read() == b"0123456789"
    hidden = TenantScope(uuid4(), scope.project_id)
    assert store.metadata(hidden, "object-1") is None
    with pytest.raises(NotFound):
        store.open_immutable(hidden, "object-1")


def test_cleanup_tombstone_and_descriptor_snapshots(object_store_factory) -> None:
    store = object_store_factory()
    scope = TenantScope(uuid4(), uuid4())
    upload = store.create_temporary(scope, 3)
    temporary = store.write_temporary(scope, upload, io.BytesIO(b"abc"))
    descriptor = store.publish(scope, temporary, "object-1")
    download = store.download_descriptor(scope, "object-1", "paper.pdf")
    with pytest.raises((FrozenInstanceError, AttributeError)):
        descriptor.object_id = "corrupted"
    assert download.object.object_id == "object-1"
    store.delete_temporary(scope, upload)
    with pytest.raises(NotFound):
        store.publish(scope, temporary, "object-2")
    store.tombstone(scope, "object-1")
    assert store.metadata(scope, "object-1") is None
    with pytest.raises(NotFound):
        store.open_immutable(scope, "object-1")
