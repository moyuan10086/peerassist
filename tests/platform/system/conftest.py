from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4

import pytest
import requests

ROOT = Path(__file__).parents[3]
COMPOSE_FILE = ROOT / "infrastructure/compose/compose.m1.yml"


class _LoginFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action = ""
        self.inputs: dict[str, str] = {}
        self._inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "form" and values.get("id") == "kc-form-login":
            self._inside = True
            self.action = values.get("action", "")
        elif tag == "input" and self._inside and values.get("name"):
            self.inputs[values["name"]] = values.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._inside = False


@dataclass(frozen=True)
class ReferenceEnvironment:
    env_file: Path
    values: dict[str, str]

    @property
    def base_url(self) -> str:
        return self.values["PEERASSIST_PUBLIC_ORIGIN"]

    @property
    def project_name(self) -> str:
        return self.values["PEERASSIST_REFERENCE_PROJECT"]

    @property
    def scope(self) -> dict[str, str]:
        path = Path(self.values["PEERASSIST_BOOTSTRAP_STATE_DIR"]) / "scope.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        return {"organization_id": str(payload["organization_id"]), "project_id": str(payload["project_id"])}

    def compose(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                self.project_name,
                "--env-file",
                str(self.env_file),
                "-f",
                str(COMPOSE_FILE),
                *args,
            ],
            cwd=ROOT,
            check=check,
            capture_output=True,
            text=True,
        )

    def psql(self, query: str) -> str:
        result = self.compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            self.values["PEERASSIST_DB_USER"],
            "-d",
            self.values["PEERASSIST_DB_NAME"],
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            query,
        )
        return result.stdout.strip()

    def token_session(self, username: str) -> requests.Session:
        response = requests.post(
            f"{self.base_url}/identity/realms/{self.values['PEERASSIST_OIDC_REALM']}"
            "/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": self.values["PEERASSIST_OIDC_AUTOMATION_CLIENT_ID"],
                "client_secret": self.values["PEERASSIST_OIDC_AUTOMATION_CLIENT_SECRET"],
                "username": username,
                "password": self.values["PEERASSIST_OIDC_USER_PASSWORD"],
            },
            timeout=20,
        )
        response.raise_for_status()
        session = requests.Session()
        session.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        return session

    def browser_session(self, username: str | None = None) -> requests.Session:
        session = requests.Session()
        login = session.get(
            f"{self.base_url}/api/v1/auth/login?return_path=/paper",
            allow_redirects=True,
            timeout=20,
        )
        login.raise_for_status()
        assert "PeerAssist" in login.text and "kc-form-login" in login.text
        parser = _LoginFormParser()
        parser.feed(login.text)
        assert parser.action
        payload = parser.inputs | {
            "username": username or self.values["PEERASSIST_OIDC_USERNAME"],
            "password": self.values["PEERASSIST_OIDC_USER_PASSWORD"],
            "login": "登录",
        }
        completed = session.post(
            urljoin(login.url, parser.action),
            data=payload,
            allow_redirects=True,
            timeout=20,
        )
        completed.raise_for_status()
        assert completed.url == f"{self.base_url}/paper"
        csrf_token = session.cookies.get("peerassist_csrf")
        assert csrf_token
        session.headers["X-CSRF-Token"] = csrf_token
        return session

    def subject(self, username: str) -> str:
        session = self.token_session(username)
        token = session.headers["Authorization"].removeprefix("Bearer ")
        encoded = token.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded))
        return str(payload["sub"])

    def wait_until(
        self,
        probe: Callable[[], Any],
        *,
        timeout: float = 90,
        interval: float = 0.5,
    ) -> Any:
        deadline = time.monotonic() + timeout
        last: Any = None
        while time.monotonic() < deadline:
            try:
                last = probe()
                if last is not None and last is not False:
                    return last
            except (requests.RequestException, ValueError):
                pass
            time.sleep(interval)
        raise AssertionError(f"condition was not met within {timeout:.0f}s; last={last!r}")

    def create_review(
        self,
        session: requests.Session,
        *,
        label: str,
        pdf_path: Path | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        project_id = self.scope["project_id"]
        source = pdf_path or ROOT / "demos/Text/bert/paper.pdf"
        suffix = uuid4().hex
        with source.open("rb") as stream:
            upload = session.post(
                f"{self.base_url}/api/v1/projects/{project_id}/papers",
                headers={"Idempotency-Key": f"{label}-paper-{suffix}"},
                files={"file": (source.name, stream, "application/pdf")},
                timeout=60,
            )
        upload.raise_for_status()
        uploaded = upload.json()
        review = session.post(
            f"{self.base_url}/api/v1/projects/{project_id}/review-jobs",
            headers={"Idempotency-Key": f"{label}-job-{suffix}"},
            json={"paper_version_id": uploaded["version"]["id"], "mode": "full"},
            timeout=20,
        )
        review.raise_for_status()
        return uploaded, review.json()


@pytest.fixture(scope="session")
def reference_env() -> ReferenceEnvironment:
    raw_path = os.environ.get("PEERASSIST_REFERENCE_ENV_FILE", "")
    if not raw_path:
        pytest.skip("PEERASSIST_REFERENCE_ENV_FILE is not configured")
    env_file = Path(raw_path)
    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        assert separator and key.startswith("PEERASSIST_") and value
        values[key] = value
    required = {
        "PEERASSIST_PUBLIC_ORIGIN",
        "PEERASSIST_REFERENCE_PROJECT",
        "PEERASSIST_BOOTSTRAP_STATE_DIR",
        "PEERASSIST_DB_USER",
        "PEERASSIST_DB_NAME",
        "PEERASSIST_OIDC_REALM",
        "PEERASSIST_OIDC_AUTOMATION_CLIENT_ID",
        "PEERASSIST_OIDC_AUTOMATION_CLIENT_SECRET",
        "PEERASSIST_OIDC_USERNAME",
        "PEERASSIST_OIDC_USER_PASSWORD",
    }
    assert required <= values.keys()
    return ReferenceEnvironment(env_file, values)
