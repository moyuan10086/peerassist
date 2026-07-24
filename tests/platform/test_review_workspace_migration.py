from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from peerassist.platform.adapters.memory import MemoryUnitOfWorkFactory
from peerassist.platform.errors import Forbidden
from peerassist.platform.models import (
    Actor,
    ActorKind,
    LegacyRegistration,
    Organization,
    Paper,
    PaperVersion,
    Project,
    ProjectMembership,
    Role,
    TenantScope,
    User,
)
from peerassist.platform.services.review_workspace_migration import (
    LegacyProviderState,
    LegacyWorkspaceMigrationService,
)

NOW = datetime(2026, 7, 24, 11, 0, tzinfo=UTC)


class LegacyFactsReader:
    def __init__(self, facts: dict[str, object], job_id: UUID) -> None:
        self.facts = facts
        self.job_id = job_id

    def read_workspace_facts(self, scope, registration):
        del scope, registration
        return self.facts


def _seed(*, digest: str = "a" * 64):
    factory = MemoryUnitOfWorkFactory(clock=lambda: NOW)
    actors = {
        name: Actor(uuid4(), ActorKind.USER)
        for name in ("admin", "reviewer")
    }
    organization = Organization(uuid4(), "legacy-migration", "Legacy Migration", "active", 1, NOW, NOW)
    project = Project(uuid4(), organization.id, "Review", "active", 1, NOW, NOW)
    paper_id = uuid4()
    version = PaperVersion(
        uuid4(), organization.id, project.id, paper_id, "papers/source", "paper.pdf",
        "application/pdf", 4, digest, actors["reviewer"].actor_id, NOW,
    )
    paper = Paper(
        paper_id, organization.id, project.id, digest, version.id,
        "active", 1, NOW, NOW,
    )
    registration = LegacyRegistration(
        uuid4(), organization.id, project.id, "m0_review_job", str(uuid4()),
        "b" * 64, "read_only", 1, NOW,
    )
    with factory(actors["admin"]) as uow:
        for name, actor in actors.items():
            uow.users.add(User(actor.actor_id, "active", name, NOW, NOW))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.projects.add(project.scope, project)
        uow.projects.save_membership(
            project.scope,
            ProjectMembership(
                uuid4(), organization.id, project.id, actors["admin"].actor_id,
                Role.PROJECT_OWNER, "active", 1, NOW, NOW,
            ),
            None,
        )
        uow.projects.save_membership(
            project.scope,
            ProjectMembership(
                uuid4(), organization.id, project.id, actors["reviewer"].actor_id,
                Role.REVIEWER, "active", 1, NOW, NOW,
            ),
            None,
        )
        uow.papers.add(project.scope, paper, version)
        uow.legacy_registrations.add(project.scope, registration)
        uow.commit()
    return factory, actors, project, version, registration


def _facts(version: PaperVersion, actor: Actor) -> dict[str, object]:
    return {
        "paper_sha256": version.sha256,
        "mode": "full",
        "consent": {
            "service": "model-review",
            "status": "granted",
            "provider_config_revision": 3,
            "policy_version": "peerassist.model-policy.v1",
            "data_scope": {"paper_text": True, "evidence": True},
            "decided_by": str(actor.actor_id),
            "decided_at": NOW.isoformat(),
        },
        "document_sections": {
            "overall_assessment": "Overall assessment",
            "major_issues": "Major issue",
            "minor_issues": "Minor issue",
            "revision_suggestions": "Revision suggestion",
        },
    }


def test_complete_legacy_facts_import_once_as_platform_authority() -> None:
    factory, actors, project, version, registration = _seed()
    service = LegacyWorkspaceMigrationService(
        factory,
        LegacyFactsReader(_facts(version, actors["reviewer"]), uuid4()),
        provider_state=lambda service: LegacyProviderState(service, 3, True),
        clock=lambda: NOW,
    )

    first = service.migrate(actors["admin"], project.id, registration.id)
    replay = service.migrate(actors["admin"], project.id, registration.id)

    assert first.status == "migrated" and replay == first
    with factory(actors["admin"]) as uow:
        job = uow.review_jobs.get(project.scope, first.job_id)
        assert job is not None and job.status == "awaiting_human_confirmation"
        consent = uow.consents.get_current(
            project.scope, job.id, version.id, "model-review"
        )
        assert consent is not None and consent.status == "granted"
        document = uow.review_documents.get(project.scope, job.id)
        assert document is not None
        assert {block.section: block.text for block in document.blocks}["major_issues"] == "Major issue"
        assert len(uow.review_jobs.list_events(project.scope, job.id)) == 1


@pytest.mark.parametrize("provider_enabled", [False, True])
def test_incomplete_or_disabled_legacy_consent_requires_reauthorization(
    provider_enabled: bool,
) -> None:
    factory, actors, project, version, registration = _seed()
    facts = _facts(version, actors["reviewer"])
    if provider_enabled:
        del facts["consent"]["policy_version"]  # type: ignore[index]
    service = LegacyWorkspaceMigrationService(
        factory,
        LegacyFactsReader(facts, uuid4()),
        provider_state=lambda service: LegacyProviderState(service, 3, provider_enabled),
        clock=lambda: NOW,
    )

    result = service.migrate(actors["admin"], project.id, registration.id)

    assert result.status == "consent_reauthorization_required"
    with factory(actors["admin"]) as uow:
        job = uow.review_jobs.get(project.scope, result.job_id)
        assert job is not None and job.status == "blocked"
        assert uow.consents.get_current(project.scope, job.id, version.id, "model-review") is None
        assert [event.event_type for event in uow.review_jobs.list_events(project.scope, job.id)] == [
            "consent_reauthorization_required"
        ]


def test_unmapped_digest_is_not_guessed_and_reviewer_cannot_migrate() -> None:
    factory, actors, project, version, registration = _seed()
    facts = _facts(version, actors["reviewer"])
    facts["paper_sha256"] = "f" * 64
    service = LegacyWorkspaceMigrationService(
        factory,
        LegacyFactsReader(facts, uuid4()),
        provider_state=lambda service: LegacyProviderState(service, 3, True),
        clock=lambda: NOW,
    )

    result = service.migrate(actors["admin"], project.id, registration.id)
    assert result.status == "paper_mapping_required" and result.job_id is None

    with pytest.raises(Forbidden):
        LegacyWorkspaceMigrationService(
            factory,
            LegacyFactsReader(_facts(version, actors["reviewer"]), uuid4()),
            provider_state=lambda service: LegacyProviderState(service, 3, True),
            clock=lambda: NOW,
        ).migrate(actors["reviewer"], project.id, registration.id)
