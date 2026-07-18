from __future__ import annotations

import asyncio
import hashlib
import io
import os
from uuid import uuid4

import pytest

from peerassist.platform.adapters.s3 import S3ObjectStore
from peerassist.platform.models import TenantScope

pytestmark = pytest.mark.requires_docker


def _store() -> S3ObjectStore:
    return S3ObjectStore.from_endpoint(
        endpoint=os.environ["PEERASSIST_TEST_S3_ENDPOINT"],
        bucket=os.environ["M1_TEST_S3_BUCKET"],
        region=os.environ["M1_TEST_S3_REGION"],
        access_key_id=os.environ["M1_TEST_S3_ACCESS_KEY"],
        secret_access_key=os.environ["M1_TEST_S3_SECRET_KEY"],
        path_style=True,
    )


def test_minio_readiness_and_tenant_key_isolation() -> None:
    store = _store()
    assert asyncio.run(store.check()) is True
    scope_a = TenantScope(uuid4(), uuid4())
    scope_b = TenantScope(uuid4(), uuid4())
    content = b"%PDF-1.7\nminio contract\n%%EOF\n"
    upload = store.create_temporary(scope_a, len(content))
    temporary = store.write_temporary(scope_a, upload, io.BytesIO(content))
    descriptor = store.publish(scope_a, temporary, "paper/source")

    assert descriptor.sha256 == hashlib.sha256(content).hexdigest()
    assert store.metadata(scope_b, "paper/source") is None
    assert store.read_range(scope_a, "paper/source", 0, 4) == b"%PDF-"
    store.delete_temporary(scope_a, upload)
