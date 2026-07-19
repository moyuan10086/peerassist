from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.requires_docker


def test_cross_identity_resources_are_hidden_and_revocation_is_immediate(
    reference_env: Any,
) -> None:
    base = reference_env.base_url
    scope = reference_env.scope
    admin = reference_env.token_session("org-a-admin")
    outsider = reference_env.token_session("org-b-admin")
    outsider_me = outsider.get(f"{base}/api/v1/me", timeout=20)
    outsider_me.raise_for_status()
    assert outsider.get(f"{base}/api/v1/projects/{scope['project_id']}", timeout=20).status_code == 404

    uploaded, job = reference_env.create_review(admin, label="tenant-isolation")

    def artifacts_ready():
        response = admin.get(
            f"{base}/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}/artifacts",
            timeout=20,
        )
        response.raise_for_status()
        return response.json() if len(response.json()) == 2 else None

    artifacts = reference_env.wait_until(artifacts_ready)
    hidden_urls = (
        f"/api/v1/projects/{scope['project_id']}/papers/{uploaded['paper']['id']}/source",
        f"/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}",
        f"/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}/artifacts/{artifacts[0]['id']}",
        f"/api/v1/organizations/{scope['organization_id']}/audit-events",
    )
    for path in hidden_urls:
        response = outsider.get(base + path, timeout=20)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    reviewer = reference_env.token_session("org-a-reviewer")
    reviewer_me = reviewer.get(f"{base}/api/v1/me", timeout=20)
    reviewer_me.raise_for_status()
    grant = admin.post(
        f"{base}/api/v1/projects/{scope['project_id']}/members",
        headers={"Idempotency-Key": f"grant-reviewer-{uuid4().hex}"},
        json={"user_id": reviewer_me.json()["id"], "role": "reviewer"},
        timeout=20,
    )
    grant.raise_for_status()
    membership = grant.json()
    assert reviewer.get(
        f"{base}/api/v1/projects/{scope['project_id']}", timeout=20
    ).status_code == 200
    revoked = admin.patch(
        f"{base}/api/v1/projects/{scope['project_id']}/members/{membership['id']}",
        headers={"Idempotency-Key": f"revoke-reviewer-{uuid4().hex}"},
        json={
            "role": "reviewer",
            "status": "revoked",
            "expected_version": membership["version"],
        },
        timeout=20,
    )
    revoked.raise_for_status()
    assert revoked.json()["status"] == "revoked"
    assert reviewer.get(
        f"{base}/api/v1/projects/{scope['project_id']}", timeout=20
    ).status_code == 404
