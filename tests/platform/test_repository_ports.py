from __future__ import annotations

import inspect
from typing import get_type_hints

from peerassist.platform import ports
from peerassist.platform.adapters import memory as memory_adapters
from peerassist.platform.adapters.memory import _Audit as MemoryAudit
from peerassist.platform.adapters.memory import _Commands as MemoryCommands
from peerassist.platform.adapters.memory import _Organizations as MemoryOrganizations
from peerassist.platform.adapters.memory import _Papers as MemoryPapers
from peerassist.platform.adapters.memory import _Projects as MemoryProjects
from peerassist.platform.adapters.memory import _ReportVersions as MemoryReportVersions
from peerassist.platform.adapters.memory import _ReviewJobs as MemoryReviewJobs
from peerassist.platform.adapters.memory import _Users as MemoryUsers
from peerassist.platform.adapters.memory import _WorkItems as MemoryWorkItems
from peerassist.platform.adapters.postgres_queue import _Audit as PostgresAudit
from peerassist.platform.adapters.postgres_queue import _Commands as PostgresCommands
from peerassist.platform.adapters.postgres_queue import _WorkItems as PostgresWorkItems
from peerassist.platform.adapters.postgres_repositories import (
    _Organizations as PostgresOrganizations,
)
from peerassist.platform.adapters.postgres_repositories import _Projects as PostgresProjects
from peerassist.platform.adapters.postgres_repositories import _Users as PostgresUsers
from peerassist.platform.adapters.postgres_review import (
    _Consents as PostgresConsents,
)
from peerassist.platform.adapters.postgres_review import (
    _Papers as PostgresPapers,
)
from peerassist.platform.adapters.postgres_review import (
    _ReportVersions as PostgresReportVersions,
)
from peerassist.platform.adapters.postgres_review import (
    _ReviewDocuments as PostgresReviewDocuments,
)
from peerassist.platform.adapters.postgres_review import (
    _ReviewJobs as PostgresReviewJobs,
)


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
        getattr(ports, "ConsentRepository"): {
            "get_current": (
                "self",
                "scope",
                "review_job_id",
                "paper_version_id",
                "service",
            ),
            "add": ("self", "scope", "consent"),
            "save": ("self", "scope", "consent", "expected_version"),
            "supersede_and_add": (
                "self",
                "scope",
                "superseded",
                "replacement",
                "expected_version",
            ),
        },
        getattr(ports, "ReviewDocumentRepository"): {
            "get": ("self", "scope", "review_job_id"),
            "add": ("self", "scope", "document"),
            "save": (
                "self",
                "scope",
                "document",
                "expected_document_version",
            ),
        },
        getattr(ports, "ReportVersionRepository"): {
            "get": ("self", "scope", "report_version_id"),
            "list_for_job": ("self", "scope", "review_job_id"),
            "add": ("self", "scope", "report_version"),
        },
    }
    for protocol, methods in expected.items():
        for name, parameters in methods.items():
            assert tuple(inspect.signature(getattr(protocol, name)).parameters) == parameters


def test_memory_and_postgres_repositories_conform_to_protocol_method_signatures() -> None:
    pairs = (
        (ports.UserRepository, MemoryUsers, PostgresUsers),
        (ports.OrganizationRepository, MemoryOrganizations, PostgresOrganizations),
        (ports.ProjectRepository, MemoryProjects, PostgresProjects),
        (ports.PaperRepository, MemoryPapers, PostgresPapers),
        (ports.ReviewJobRepository, MemoryReviewJobs, PostgresReviewJobs),
        (ports.ConsentRepository, memory_adapters._Consents, PostgresConsents),
        (
            ports.ReviewDocumentRepository,
            memory_adapters._ReviewDocuments,
            PostgresReviewDocuments,
        ),
        (ports.ReportVersionRepository, MemoryReportVersions, PostgresReportVersions),
        (ports.CommandRepository, MemoryCommands, PostgresCommands),
        (ports.WorkItemRepository, MemoryWorkItems, PostgresWorkItems),
        (ports.AuditRepository, MemoryAudit, PostgresAudit),
    )
    for protocol, *implementations in pairs:
        for name, method in protocol.__dict__.items():
            if name.startswith("_") or not inspect.isfunction(method):
                continue
            expected = tuple(inspect.signature(method).parameters)
            for implementation in implementations:
                assert tuple(inspect.signature(getattr(implementation, name)).parameters) == expected


def test_review_workspace_repositories_are_exposed_by_both_uows() -> None:
    pairs = (
        (ports.ConsentRepository, memory_adapters._Consents),
        (ports.ReviewDocumentRepository, memory_adapters._ReviewDocuments),
    )
    for protocol, implementation in pairs:
        for name, method in protocol.__dict__.items():
            if name.startswith("_") or not inspect.isfunction(method):
                continue
            assert tuple(inspect.signature(getattr(implementation, name)).parameters) == tuple(
                inspect.signature(method).parameters
            )

    annotations = get_type_hints(ports.UnitOfWork)
    assert annotations["consents"] is ports.ConsentRepository
    assert annotations["review_documents"] is ports.ReviewDocumentRepository
    assert annotations["report_versions"] is ports.ReportVersionRepository

    assert {
        "consents",
        "review_documents",
        "report_versions",
    }.issubset(memory_adapters.MemoryUnitOfWork.__dict__.get("__annotations__", {}))

    from peerassist.platform.adapters.postgres import PostgresUnitOfWork

    assert {"consents", "review_documents", "report_versions"}.issubset(
        PostgresUnitOfWork._repository_names
    )
