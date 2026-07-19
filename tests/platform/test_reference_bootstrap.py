from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from services.bootstrap.main import bootstrap_platform

from peerassist.platform.models import Actor, ActorKind, TenantScope
from services.api.composition import PlatformDependencies


class _Response:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("request failed")


class _BootstrapHttp:
    def __init__(self, dependencies: PlatformDependencies, bearer: str) -> None:
        self.dependencies = dependencies
        self.bearer = bearer

    def post(self, url: str, **kwargs: object) -> _Response:
        if url.endswith("/token"):
            return _Response({"access_token": self.bearer})
        raise AssertionError(url)

    def get(self, url: str, **kwargs: object) -> _Response:
        if url.endswith("/api/v1/me"):
            actor = self.dependencies.session_service.resolve_bearer(self.bearer)
            with self.dependencies.uow_factory(actor) as uow:
                identity = uow.users.get_identity_by_id(actor.identity_id)
            assert identity is not None
            return _Response(
                {
                    "id": str(actor.actor_id),
                    "display_name": "Bootstrap Admin",
                    "identity": {"issuer": identity.issuer, "subject": identity.subject},
                }
            )
        raise AssertionError(url)


def test_reference_bootstrap_creates_initial_organization_project_and_worker_scope(
    tmp_path: Path,
) -> None:
    dependencies = PlatformDependencies.for_test()
    now = datetime.now(UTC)
    bearer = dependencies.identity_provider.issue(
        {
            "iss": dependencies.identity_provider.issuer,
            "sub": "reference-admin",
            "aud": dependencies.identity_provider.audience,
            "alg": "RS256",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "name": "Bootstrap Admin",
        }
    )
    scope_file = tmp_path / "scope.json"
    environment = {
        "PEERASSIST_BOOTSTRAP_API_BASE_URL": "http://api:8080",
        "PEERASSIST_BOOTSTRAP_TOKEN_URL": "http://api:8080/identity/realms/test/token",
        "PEERASSIST_BOOTSTRAP_CLIENT_ID": "automation",
        "PEERASSIST_BOOTSTRAP_CLIENT_SECRET": "private-client-secret",
        "PEERASSIST_BOOTSTRAP_USERNAME": "reference-admin",
        "PEERASSIST_BOOTSTRAP_PASSWORD": "private-user-password",
        "PEERASSIST_BOOTSTRAP_OPERATOR_ID": str(uuid4()),
        "PEERASSIST_BOOTSTRAP_ORGANIZATION_SLUG": "peerassist",
        "PEERASSIST_BOOTSTRAP_ORGANIZATION_NAME": "PeerAssist",
        "PEERASSIST_BOOTSTRAP_PROJECT_NAME": "Review Workspace",
        "PEERASSIST_BOOTSTRAP_SCOPE_FILE": str(scope_file),
    }

    result = bootstrap_platform(dependencies, environment, _BootstrapHttp(dependencies, bearer))

    assert result.organization.name == "PeerAssist"
    assert result.project.name == "Review Workspace"
    assert json.loads(scope_file.read_text(encoding="utf-8")) == {
        "organization_id": str(result.organization.id),
        "project_id": str(result.project.id),
    }
    with dependencies.uow_factory(Actor(UUID(environment["PEERASSIST_BOOTSTRAP_OPERATOR_ID"]), ActorKind.OPERATOR)) as uow:
        assert uow.organizations.get(TenantScope(result.organization.id)) == result.organization
        assert uow.projects.get(result.project.scope) == result.project
