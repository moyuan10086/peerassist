from __future__ import annotations

from typing import Any

import pytest
import requests

pytestmark = pytest.mark.requires_docker


def test_keycloak_subject_survives_restart_and_readiness_recovers(
    reference_env: Any,
) -> None:
    username = reference_env.values["PEERASSIST_OIDC_USERNAME"]
    before = reference_env.subject(username)
    reference_env.compose("restart", "keycloak")

    def subject_after_restart():
        try:
            return reference_env.subject(username)
        except requests.RequestException:
            return None

    after = reference_env.wait_until(subject_after_restart, timeout=90, interval=1)
    assert after == before
    reference_env.wait_until(
        lambda: requests.get(f"{reference_env.base_url}/api/v1/ready", timeout=5).status_code
        == 200,
        timeout=60,
    )


def test_expired_worker_lease_recovers_without_duplicate_artifacts_or_scratch(
    reference_env: Any,
) -> None:
    base = reference_env.base_url
    scope = reference_env.scope
    admin = reference_env.token_session("org-a-admin")
    reference_env.compose("stop", "worker")
    try:
        _, job = reference_env.create_review(admin, label="worker-recovery")
        pending = reference_env.psql(
            f"SELECT count(*) FROM work_items WHERE job_id = '{job['id']}';"
        )
        assert pending == "1"
        reference_env.psql(
            "UPDATE work_items SET available_at = now() - interval '2 minutes', "
            "lease_owner = 'terminated-worker', "
            "lease_expires_at = now() - interval '1 second', attempt_count = 1 "
            f"WHERE job_id = '{job['id']}';"
        )
    finally:
        reference_env.compose("start", "worker")

    def artifacts_ready():
        response = admin.get(
            f"{base}/api/v1/projects/{scope['project_id']}/review-jobs/{job['id']}/artifacts",
            timeout=20,
        )
        response.raise_for_status()
        return response.json() if len(response.json()) == 2 else None

    artifacts = reference_env.wait_until(artifacts_ready, timeout=120)
    assert {item["logical_name"] for item in artifacts} == {
        "paper_summary.md",
        "review.md",
        "review_result.json",
    }
    before_counts = reference_env.psql(
        "SELECT "
        f"(SELECT count(*) FROM artifacts WHERE job_id = '{job['id']}'),"
        f"(SELECT count(*) FROM review_events WHERE job_id = '{job['id']}' "
        "AND event_type = 'review_job.artifacts_published'),"
        f"(SELECT count(*) FROM work_items WHERE job_id = '{job['id']}');"
    )
    assert before_counts == "2|1|0"
    scratch = reference_env.compose(
        "exec",
        "-T",
        "worker",
        "sh",
        "-c",
        "find /var/lib/peerassist/scratch -mindepth 1 -print -quit",
    )
    assert scratch.stdout.strip() == ""

    reference_env.compose("restart", "worker")
    reference_env.wait_until(
        lambda: reference_env.compose("ps", "--status", "running", "--quiet", "worker").stdout.strip(),
        timeout=30,
    )
    after_counts = reference_env.psql(
        "SELECT "
        f"(SELECT count(*) FROM artifacts WHERE job_id = '{job['id']}'),"
        f"(SELECT count(*) FROM review_events WHERE job_id = '{job['id']}' "
        "AND event_type = 'review_job.artifacts_published');"
    )
    assert after_counts == "2|1"


def test_readiness_reports_object_store_failure_without_private_details(
    reference_env: Any,
) -> None:
    reference_env.compose("stop", "minio")
    try:
        def unavailable():
            response = requests.get(f"{reference_env.base_url}/api/v1/ready", timeout=10)
            return response if response.status_code == 503 else None

        response = reference_env.wait_until(unavailable, timeout=45)
        payload = response.json()
        assert payload["status"] == "unavailable"
        assert payload["dependencies"]["object_store"] == "unavailable"
        assert "secret" not in response.text.casefold()
        assert "minio:" not in response.text.casefold()
    finally:
        reference_env.compose("start", "minio")
        reference_env.wait_until(
            lambda: requests.get(f"{reference_env.base_url}/api/v1/ready", timeout=5).status_code
            == 200,
            timeout=60,
        )
