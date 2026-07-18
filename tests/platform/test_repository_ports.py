from __future__ import annotations

import inspect

from peerassist.platform import ports
from peerassist.platform.adapters.memory import _Commands as MemoryCommands
from peerassist.platform.adapters.memory import _Papers as MemoryPapers
from peerassist.platform.adapters.memory import _ReviewJobs as MemoryReviewJobs
from peerassist.platform.adapters.memory import _WorkItems as MemoryWorkItems
from peerassist.platform.adapters.postgres_queue import _Commands as PostgresCommands
from peerassist.platform.adapters.postgres_queue import _WorkItems as PostgresWorkItems
from peerassist.platform.adapters.postgres_review import _Papers as PostgresPapers
from peerassist.platform.adapters.postgres_review import _ReviewJobs as PostgresReviewJobs


def test_repository_protocols_include_all_shared_contract_operations() -> None:
    expected = {
        ports.PaperRepository: {"get_version": ("self", "scope", "version_id")},
        ports.ReviewJobRepository: {"list_events": ("self", "scope", "job_id")},
        ports.CommandRepository: {
            "reserve_or_replay": ("self", "scope", "command"),
        },
        ports.WorkItemRepository: {
            "get": ("self", "scope", "item_id"),
            "renew": ("self", "scope", "item_id", "lease_owner", "lease_seconds"),
        },
    }
    for protocol, methods in expected.items():
        for name, parameters in methods.items():
            assert tuple(inspect.signature(getattr(protocol, name)).parameters) == parameters


def test_memory_and_postgres_repositories_conform_to_protocol_method_signatures() -> None:
    pairs = (
        (ports.PaperRepository, MemoryPapers, PostgresPapers),
        (ports.ReviewJobRepository, MemoryReviewJobs, PostgresReviewJobs),
        (ports.CommandRepository, MemoryCommands, PostgresCommands),
        (ports.WorkItemRepository, MemoryWorkItems, PostgresWorkItems),
    )
    for protocol, *implementations in pairs:
        for name, method in protocol.__dict__.items():
            if name.startswith("_") or not inspect.isfunction(method):
                continue
            expected = tuple(inspect.signature(method).parameters)
            for implementation in implementations:
                assert tuple(inspect.signature(getattr(implementation, name)).parameters) == expected
