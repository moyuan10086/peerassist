"""Settings-driven provider construction for the API boundary."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from common.config import PlatformSettings
from peerassist.platform.adapters import oidc
from peerassist.platform.adapters.memory import (
    FakeIdentityProvider,
    MemoryObjectStore,
    MemoryUnitOfWorkFactory,
)
from peerassist.platform.adapters.postgres import PostgresUnitOfWorkFactory
from peerassist.platform.adapters.postgres_schema import PostgresSchemaReadiness
from peerassist.platform.adapters.s3 import S3ObjectStore
from peerassist.platform.ports import IdentityProvider, ObjectStore, UnitOfWorkFactory
from peerassist.platform.services.sessions import BrowserSessionService

from .dependencies import LifecycleResource, ReadinessCheck

_SAFE_DEPENDENCY_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


class CompositionError(RuntimeError):
    """Raised when the selected environment cannot be composed safely."""


@dataclass(frozen=True)
class _Ready:
    name: str

    async def check(self) -> bool:
        return True


@dataclass(frozen=True)
class PlatformDependencies:
    """Provider-neutral dependencies owned by one FastAPI application."""

    uow_factory: UnitOfWorkFactory
    identity_provider: IdentityProvider
    object_store: ObjectStore
    session_service: BrowserSessionService
    readiness_checks: tuple[ReadinessCheck, ...]
    lifecycle_resources: tuple[LifecycleResource, ...] = ()
    readiness_check_timeout_seconds: float = 2.0
    readiness_overall_timeout_seconds: float = 3.0
    lifecycle_stop_timeout_seconds: float = 2.0
    lifecycle_cleanup_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        names = [check.name for check in self.readiness_checks]
        if any(_SAFE_DEPENDENCY_NAME.fullmatch(name) is None for name in names):
            raise ValueError("readiness dependency names must be public-safe identifiers")
        if len(names) != len(set(names)):
            raise ValueError("readiness dependency names must be unique")
        if (
            self.readiness_check_timeout_seconds <= 0
            or self.readiness_overall_timeout_seconds <= 0
            or self.lifecycle_stop_timeout_seconds <= 0
            or self.lifecycle_cleanup_timeout_seconds <= 0
        ):
            raise ValueError("readiness timeouts must be positive")

    @classmethod
    def for_test(
        cls,
        *,
        readiness_checks: tuple[ReadinessCheck, ...] | None = None,
        lifecycle_resources: tuple[LifecycleResource, ...] = (),
        readiness_check_timeout_seconds: float = 2.0,
        readiness_overall_timeout_seconds: float = 3.0,
        lifecycle_stop_timeout_seconds: float = 2.0,
        lifecycle_cleanup_timeout_seconds: float = 10.0,
    ) -> PlatformDependencies:
        return _memory_dependencies(
            issuer="http://identity.test/realms/peerassist",
            audience="peerassist-api",
            algorithms=frozenset({"RS256"}),
            readiness_checks=readiness_checks,
            lifecycle_resources=lifecycle_resources,
            readiness_check_timeout_seconds=readiness_check_timeout_seconds,
            readiness_overall_timeout_seconds=readiness_overall_timeout_seconds,
            lifecycle_stop_timeout_seconds=lifecycle_stop_timeout_seconds,
            lifecycle_cleanup_timeout_seconds=lifecycle_cleanup_timeout_seconds,
        )


def build_dependencies(settings: PlatformSettings) -> PlatformDependencies:
    """Build explicitly selected adapters, failing closed in production."""

    if settings.environment == "production" or settings.provider_profile == "external":
        if (
            not settings.oidc_issuer
            or not settings.oidc_audience.strip()
            or not settings.oidc_algorithms
        ):
            raise CompositionError("OIDC identity provider is unavailable.")
        try:
            uow_factory = PostgresUnitOfWorkFactory.from_url(
                settings.database_url.get_secret_value()
            )
        except Exception:
            raise CompositionError("PostgreSQL provider is unavailable.") from None
        try:
            identity_provider = oidc.OidcIdentityProvider(
                issuer=settings.oidc_issuer,
                audience=settings.oidc_audience,
                client_id=settings.oidc_client_id,
                accepted_algorithms=frozenset(settings.oidc_algorithms),
                require_https=settings.environment == "production",
            )
        except Exception:
            raise CompositionError("OIDC identity provider is unavailable.") from None
        if settings.s3_access_key_id is None or settings.s3_secret_access_key is None:
            raise CompositionError("Object store provider is unavailable.")
        try:
            object_store = S3ObjectStore.from_endpoint(
                endpoint=settings.s3_endpoint,
                bucket=settings.s3_bucket,
                region="us-east-1",
                access_key_id=settings.s3_access_key_id.get_secret_value(),
                secret_access_key=settings.s3_secret_access_key.get_secret_value(),
                path_style=settings.s3_path_style,
            )
        except Exception:
            raise CompositionError("Object store provider is unavailable.") from None
        session_keys = settings.decoded_session_keys()
        if not session_keys:
            raise CompositionError("Browser session provider is unavailable.")
        session_service = BrowserSessionService(
            uow_factory,
            identity_provider,
            key_ring=session_keys,
            redirect_uri=f"{settings.public_base_url}/api/v1/auth/callback",
        )
        return PlatformDependencies(
            uow_factory=uow_factory,
            identity_provider=identity_provider,
            object_store=object_store,
            session_service=session_service,
            readiness_checks=(
                PostgresSchemaReadiness(uow_factory.engine),
                identity_provider,
                object_store,
            ),
            lifecycle_resources=(uow_factory, identity_provider, object_store),
        )
    return _memory_dependencies(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        algorithms=frozenset(settings.oidc_algorithms),
    )


def _memory_dependencies(
    *,
    issuer: str,
    audience: str,
    algorithms: frozenset[str],
    readiness_checks: tuple[ReadinessCheck, ...] | None = None,
    lifecycle_resources: tuple[LifecycleResource, ...] = (),
    readiness_check_timeout_seconds: float = 2.0,
    readiness_overall_timeout_seconds: float = 3.0,
    lifecycle_stop_timeout_seconds: float = 2.0,
    lifecycle_cleanup_timeout_seconds: float = 10.0,
) -> PlatformDependencies:
    uow_factory = MemoryUnitOfWorkFactory()
    identity_provider = FakeIdentityProvider(
        issuer=issuer,
        audience=audience,
        accepted_algorithms=algorithms,
    )
    checks = readiness_checks or (
        _Ready("database"),
        _Ready("identity"),
        _Ready("object_store"),
    )
    return PlatformDependencies(
        uow_factory=uow_factory,
        identity_provider=identity_provider,
        object_store=MemoryObjectStore(),
        session_service=BrowserSessionService(
            uow_factory,
            identity_provider,
            key_ring={"development": hashlib.sha256(b"peerassist-development-session-key").digest()},
            redirect_uri="http://testserver/api/v1/auth/callback",
        ),
        readiness_checks=checks,
        lifecycle_resources=lifecycle_resources,
        readiness_check_timeout_seconds=readiness_check_timeout_seconds,
        readiness_overall_timeout_seconds=readiness_overall_timeout_seconds,
        lifecycle_stop_timeout_seconds=lifecycle_stop_timeout_seconds,
        lifecycle_cleanup_timeout_seconds=lifecycle_cleanup_timeout_seconds,
    )
