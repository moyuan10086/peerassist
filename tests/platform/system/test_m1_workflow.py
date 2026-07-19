from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.requires_docker


def test_browser_oidc_upload_worker_decision_and_persistence(
    reference_env: Any,
) -> None:
    session = reference_env.browser_session()
    base = reference_env.base_url
    scope = reference_env.scope
    current = session.get(f"{base}/api/v1/auth/session", timeout=20)
    current.raise_for_status()
    assert current.json()["authenticated"] is True
    assert current.json()["user"]["is_admin"] is True

    organizations = session.get(f"{base}/api/v1/organizations", timeout=20)
    organizations.raise_for_status()
    assert [item["id"] for item in organizations.json()] == [scope["organization_id"]]
    projects = session.get(
        f"{base}/api/v1/organizations/{scope['organization_id']}/projects", timeout=20
    )
    projects.raise_for_status()
    assert [item["id"] for item in projects.json()] == [scope["project_id"]]

    pdf_path = Path(__file__).parents[3] / "demos/Text/bert/paper.pdf"
    uploaded, job = reference_env.create_review(session, label="system-workflow", pdf_path=pdf_path)
    paper_id = uploaded["paper"]["id"]
    version = uploaded["version"]
    ranged = session.get(
        f"{base}/api/v1/projects/{scope['project_id']}/papers/{paper_id}/source",
        headers={"Range": "bytes=0-1023"},
        timeout=20,
    )
    assert ranged.status_code == 206
    assert ranged.content == pdf_path.read_bytes()[:1024]
    assert ranged.headers["Content-Range"] == f"bytes 0-1023/{version['size_bytes']}"

    counts = reference_env.psql(
        "SELECT "
        f"(SELECT count(*) FROM review_jobs WHERE id = '{job['id']}'),"
        f"(SELECT count(*) FROM outbox_events WHERE aggregate_id = '{job['id']}');"
    )
    assert counts == "1|1"

    def completed_job():
        response = session.get(
            f"{base}/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}",
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if payload["status"] == "blocked" else None

    job = reference_env.wait_until(completed_job)

    def completed_artifacts():
        response = session.get(
            f"{base}/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}/artifacts",
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if len(payload) == 2 else None

    artifacts = reference_env.wait_until(completed_artifacts)
    assert {item["logical_name"] for item in artifacts} == {"paper_summary.md", "review.md"}
    contents: dict[str, bytes] = {}
    for artifact in artifacts:
        url = (
            f"{base}/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}"
            f"/artifacts/{artifact['id']}"
        )
        response = session.get(url, timeout=20)
        response.raise_for_status()
        contents[artifact["logical_name"]] = response.content
        fragment = session.get(url, headers={"Range": "bytes=0-31"}, timeout=20)
        assert fragment.status_code == 206
        assert fragment.content == response.content[:32]
        assert fragment.headers["Content-Range"].startswith("bytes 0-31/")
    assert "这篇论文讲了什么" in contents["paper_summary.md"].decode("utf-8")
    assert "审阅报告" in contents["review.md"].decode("utf-8")

    decision = session.post(
        f"{base}/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}/decisions",
        headers={"Idempotency-Key": f"system-decision-{uuid4().hex}"},
        json={
            "decision_type": "concern",
            "subject_id": "system-review",
            "decision": "confirm",
            "expected_version": job["version"],
        },
        timeout=20,
    )
    decision.raise_for_status()
    finalized = session.post(
        f"{base}/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}/finalize",
        headers={"Idempotency-Key": f"system-finalize-{uuid4().hex}"},
        json={"expected_version": decision.json()["version"]},
        timeout=20,
    )
    finalized.raise_for_status()
    assert finalized.json()["status"] == "completed"

    events = session.get(
        f"{base}/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}/events",
        timeout=20,
    ).json()
    assert [event["aggregate_sequence"] for event in events] == list(range(1, len(events) + 1))
    assert {event["event_type"] for event in events} >= {
        "review_job.created",
        "review_job.artifacts_published",
        "review_job.decision_recorded",
        "review_job.finalized",
    }

    reference_env.compose("restart", "api")
    reference_env.wait_until(
        lambda: session.get(f"{base}/api/v1/ready", timeout=5).status_code == 200,
        timeout=60,
    )
    persisted = session.get(
        f"{base}/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}", timeout=20
    )
    persisted.raise_for_status()
    assert persisted.json()["status"] == "completed"
    assert session.get(f"{base}/api/v1/auth/session", timeout=20).json()["authenticated"] is True
