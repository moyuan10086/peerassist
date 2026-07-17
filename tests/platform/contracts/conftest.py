from __future__ import annotations

from datetime import UTC, datetime

import pytest

from peerassist.platform.adapters.memory import (
    FakeIdentityProvider,
    MemoryObjectStore,
    MemoryUnitOfWorkFactory,
)


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--adapter",
        action="store",
        default="memory",
        help="contract adapter: memory/postgresql, minio, fake/keycloak",
    )


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def uow_factory(request: pytest.FixtureRequest, clock: MutableClock):
    adapter = request.config.getoption("--adapter")
    if adapter != "memory":
        pytest.skip(f"unit-of-work adapter {adapter!r} is not implemented in Task 3")
    return MemoryUnitOfWorkFactory(clock=clock)


@pytest.fixture
def object_store_factory(request: pytest.FixtureRequest, clock: MutableClock):
    adapter = request.config.getoption("--adapter")
    if adapter != "memory":
        pytest.skip(f"object-store adapter {adapter!r} is not implemented in Task 3")
    return lambda: MemoryObjectStore(clock=clock)


@pytest.fixture
def identity_provider_factory(request: pytest.FixtureRequest, clock: MutableClock):
    adapter = request.config.getoption("--adapter")
    if adapter not in {"memory", "fake"}:
        pytest.skip(f"identity adapter {adapter!r} is not implemented in Task 3")
    return lambda **kwargs: FakeIdentityProvider(clock=clock, **kwargs)
