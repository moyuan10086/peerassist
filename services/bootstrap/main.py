"""Create the initial tenant and Worker scope for the M1 reference profile."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

import httpx
from common.config import PlatformSettings
from peerassist.platform.models import Actor, ActorKind, Organization, Project
from peerassist.platform.services.bootstrap import BootstrapOrganization, BootstrapOrganizationService
from peerassist.platform.services.memberships import CreateProject, MembershipService
from services.api.composition import PlatformDependencies, build_dependencies


class BootstrapHttp(Protocol):
    def post(self, url: str, **kwargs: object): ...
    def get(self, url: str, **kwargs: object): ...


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    organization: Organization
    project: Project


def bootstrap_platform(
    dependencies: PlatformDependencies,
    environ: Mapping[str, str],
    http: BootstrapHttp,
) -> BootstrapResult:
    values = _inputs(environ)
    token_response = http.post(
        values["token_url"],
        data={
            "grant_type": "password",
            "client_id": values["client_id"],
            "client_secret": values["client_secret"],
            "username": values["username"],
            "password": values["password"],
        },
        timeout=20.0,
    )
    token_response.raise_for_status()
    token_payload = token_response.json()
    access_token = token_payload.get("access_token") if isinstance(token_payload, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("bootstrap identity token is unavailable")
    me_response = http.get(
        f"{values['api_base_url']}/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20.0,
    )
    me_response.raise_for_status()
    me_payload = me_response.json()
    if not isinstance(me_payload, dict):
        raise ValueError("bootstrap identity is unavailable")
    user_id = UUID(str(me_payload["id"]))
    identity_payload = me_payload.get("identity")
    issuer = identity_payload.get("issuer") if isinstance(identity_payload, dict) else None
    subject = identity_payload.get("subject") if isinstance(identity_payload, dict) else None
    if not isinstance(issuer, str) or not issuer or not isinstance(subject, str) or not subject:
        raise ValueError("bootstrap identity claims are unavailable")
    operator = Actor(UUID(values["operator_id"]), ActorKind.OPERATOR)
    organization_result = BootstrapOrganizationService(dependencies.uow_factory).execute(
        operator,
        BootstrapOrganization(
            issuer,
            subject,
            values["organization_slug"],
            values["organization_name"],
            "reference-organization-v1",
            "reference-bootstrap-organization",
        ),
    )
    project = MembershipService(dependencies.uow_factory).create_project(
        Actor(user_id, ActorKind.USER),
        CreateProject(
            organization_result.organization.id,
            values["project_name"],
            "reference-project-v1",
            "reference-bootstrap-project",
        ),
    )
    _write_scope(
        Path(values["scope_file"]),
        organization_result.organization.id,
        project.id,
    )
    return BootstrapResult(organization_result.organization, project)


def _inputs(environ: Mapping[str, str]) -> dict[str, str]:
    names = {
        "api_base_url": "PEERASSIST_BOOTSTRAP_API_BASE_URL",
        "token_url": "PEERASSIST_BOOTSTRAP_TOKEN_URL",
        "client_id": "PEERASSIST_BOOTSTRAP_CLIENT_ID",
        "client_secret": "PEERASSIST_BOOTSTRAP_CLIENT_SECRET",
        "username": "PEERASSIST_BOOTSTRAP_USERNAME",
        "password": "PEERASSIST_BOOTSTRAP_PASSWORD",
        "operator_id": "PEERASSIST_BOOTSTRAP_OPERATOR_ID",
        "organization_slug": "PEERASSIST_BOOTSTRAP_ORGANIZATION_SLUG",
        "organization_name": "PEERASSIST_BOOTSTRAP_ORGANIZATION_NAME",
        "project_name": "PEERASSIST_BOOTSTRAP_PROJECT_NAME",
        "scope_file": "PEERASSIST_BOOTSTRAP_SCOPE_FILE",
    }
    values = {key: environ.get(name, "").strip() for key, name in names.items()}
    if any(not value for value in values.values()):
        raise ValueError("reference bootstrap configuration is incomplete")
    if not values["api_base_url"].startswith("http") or not values["token_url"].startswith("http"):
        raise ValueError("reference bootstrap URLs are invalid")
    if len(values["client_secret"]) > 4096 or len(values["password"]) > 4096:
        raise ValueError("reference bootstrap secret is invalid")
    return values


def _write_scope(path: Path, organization_id: UUID, project_id: UUID) -> None:
    if path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError("bootstrap scope destination is invalid")
    payload = json.dumps(
        {"organization_id": str(organization_id), "project_id": str(project_id)},
        sort_keys=True,
        separators=(",", ":"),
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    try:
        settings = PlatformSettings()
        dependencies = build_dependencies(settings)
        with httpx.Client(follow_redirects=False) as client:
            result = bootstrap_platform(dependencies, os.environ, client)
        sys.stdout.write(
            json.dumps(
                {
                    "organization_id": str(result.organization.id),
                    "project_id": str(result.project.id),
                    "status": "ok",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 0
    except Exception:
        sys.stderr.write('{"error":{"code":"platform_bootstrap_failed"}}\n')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
