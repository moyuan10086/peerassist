from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from peerassist.platform.adapters.memory import MemoryObjectStore, MemoryUnitOfWorkFactory
from peerassist.platform.errors import (
    DependencyUnavailable,
    Forbidden,
    IdempotencyConflict,
    StaleVersion,
)
from peerassist.platform.models import (
    Actor,
    ActorKind,
    Artifact,
    Organization,
    Paper,
    PaperVersion,
    Project,
    ProjectMembership,
    ReviewDocumentBlock,
    Role,
    TenantScope,
    User,
)
from peerassist.platform.services.review_workspace import (
    ChangeExternalServiceConsent,
    DecideFinding,
    ReviewWorkspaceService,
    SaveReviewDocument,
)
from peerassist.platform.services.reviews import CreateReviewJob, ReviewService

NOW = datetime(2026, 7, 24, 9, 0, tzinfo=UTC)


def _seed() -> tuple[
    ReviewWorkspaceService,
    ReviewService,
    MemoryUnitOfWorkFactory,
    MemoryObjectStore,
    dict[str, Actor],
    Project,
    PaperVersion,
]:
    factory = MemoryUnitOfWorkFactory(clock=lambda: NOW)
    store = MemoryObjectStore(clock=lambda: NOW)
    actors = {
        name: Actor(uuid4(), ActorKind.USER)
        for name in ("owner", "reviewer", "viewer", "outsider")
    }
    organization = Organization(uuid4(), "workspace", "Workspace", "active", 1, NOW, NOW)
    project = Project(uuid4(), organization.id, "Evidence review", "active", 1, NOW, NOW)
    paper_id = uuid4()
    version = PaperVersion(
        uuid4(),
        organization.id,
        project.id,
        paper_id,
        "papers/source.pdf",
        "paper.pdf",
        "application/pdf",
        8,
        "a" * 64,
        actors["reviewer"].actor_id,
        NOW,
    )
    paper = Paper(
        paper_id,
        organization.id,
        project.id,
        version.sha256,
        version.id,
        "active",
        1,
        NOW,
        NOW,
    )
    with factory(actors["owner"]) as uow:
        for name, actor in actors.items():
            uow.users.add(User(actor.actor_id, "active", name, NOW, NOW))
        uow.organizations.add(TenantScope(organization.id), organization)
        uow.projects.add(project.scope, project)
        for name, role in (
            ("owner", Role.PROJECT_OWNER),
            ("reviewer", Role.REVIEWER),
            ("viewer", Role.VIEWER),
        ):
            uow.projects.save_membership(
                project.scope,
                ProjectMembership(
                    uuid4(),
                    organization.id,
                    project.id,
                    actors[name].actor_id,
                    role,
                    "active",
                    1,
                    NOW,
                    NOW,
                ),
                None,
            )
        uow.papers.add(project.scope, paper, version)
        uow.commit()
    reviews = ReviewService(factory, clock=lambda: NOW)
    workspace = ReviewWorkspaceService(factory, store, clock=lambda: NOW)
    return workspace, reviews, factory, store, actors, project, version


def _job(reviews, actor, project, version):
    return reviews.create(
        actor,
        CreateReviewJob(project.id, version.id, "full", "create", "request-create"),
    )


def _consent(
    project,
    job,
    *,
    action,
    expected_version,
    key,
    paper_version_id=None,
    provider_revision=1,
    policy_version="policy-v1",
    data_scope=None,
    expires_at=None,
):
    return ChangeExternalServiceConsent(
        project_id=project.id,
        job_id=job.id,
        paper_version_id=paper_version_id or job.paper_version_id,
        service="model-review",
        provider_config_revision=provider_revision,
        policy_version=policy_version,
        data_scope=data_scope or {"paper_text": True, "evidence": True},
        action=action,
        expected_consent_version=expected_version,
        idempotency_key=key,
        request_id=f"request-{key}",
        expires_at=expires_at,
    )


def _publish_review_result(
    factory,
    store,
    actor,
    project,
    job,
    *,
    revision=2,
    concern_overrides=None,
    extra_concerns=None,
):
    concern = {
        "finding_lineage_id": "lineage-method",
        "finding_id": "finding-method-v2",
        "revision": revision,
        "level": "major_concern",
        "title": "Sampling method",
        "impact": "The sampling design limits the strength of the conclusions.",
        "author_action": "Explain the sampling limitations and mitigation.",
        "evidence_ids": ["evidence-page-3"],
        "evidence": [
            {
                "id": "evidence-page-3",
                "locator": "pdf:page:3",
                "page": 3,
                "text": "Participants were recruited by convenience sampling.",
            }
        ],
    }
    concern.update(concern_overrides or {})
    payload = {
        "schema_version": "peerassist.review_result.v1",
        "concerns": [concern, *(extra_concerns or ())],
    }
    raw = json.dumps(payload).encode("utf-8")
    upload_id = store.create_temporary(project.scope, len(raw))
    temporary = store.write_temporary(project.scope, upload_id, io.BytesIO(raw))
    descriptor = store.publish(
        project.scope,
        temporary,
        f"review-jobs/{job.id}/review_result.json",
    )
    store.delete_temporary(project.scope, upload_id)
    artifact = Artifact(
        uuid4(),
        project.organization_id,
        project.id,
        job.id,
        "review_result.json",
        descriptor,
        "available",
        NOW,
    )
    with factory(actor) as uow:
        uow.artifacts.add(project.scope, artifact)
        uow.commit()
    return payload


def _decision(
    project,
    job,
    document,
    *,
    action="accept",
    key="decision",
    finding_revision=2,
    rewrite_text=None,
    expected_review_version=None,
    expected_document_version=None,
    last_decision_event_id=None,
):
    return DecideFinding(
        project_id=project.id,
        job_id=job.id,
        finding_lineage_id="lineage-method",
        finding_id="finding-method-v2",
        finding_revision=finding_revision,
        action=action,
        rewrite_text=rewrite_text,
        expected_review_version=expected_review_version or job.version,
        expected_document_version=expected_document_version or document.document_version,
        last_decision_event_id=(
            document.base_decision_event_id
            if last_decision_event_id is None
            else last_decision_event_id
        ),
        idempotency_key=key,
        request_id=f"request-{key}",
    )


def test_consent_grant_deny_revoke_and_idempotent_replay() -> None:
    workspace, reviews, _, _, actors, project, version = _seed()
    first_job = _job(reviews, actors["reviewer"], project, version)
    pending = workspace.change_consent(
        actors["reviewer"], _consent(project, first_job, action="reapply", expected_version=0, key="apply")
    )
    granted = workspace.change_consent(
        actors["reviewer"],
        _consent(
            project,
            first_job,
            action="grant",
            expected_version=1,
            key="grant",
            expires_at=NOW + timedelta(hours=1),
        ),
    )
    replay = workspace.change_consent(
        actors["reviewer"],
        _consent(
            project,
            first_job,
            action="grant",
            expected_version=1,
            key="grant",
            expires_at=NOW + timedelta(hours=1),
        ),
    )
    revoked = workspace.change_consent(
        actors["reviewer"], _consent(project, first_job, action="revoke", expected_version=2, key="revoke")
    )

    second_job = reviews.create(
        actors["reviewer"],
        CreateReviewJob(project.id, version.id, "fast", "create-two", "request-create-two"),
    )
    workspace.change_consent(
        actors["reviewer"], _consent(project, second_job, action="reapply", expected_version=0, key="apply-two")
    )
    denied = workspace.change_consent(
        actors["reviewer"], _consent(project, second_job, action="deny", expected_version=1, key="deny")
    )

    assert pending.status == "pending" and pending.generation == 1 and pending.version == 1
    assert granted.status == "granted" and granted.version == 2 and replay == granted
    assert revoked.status == "revoked" and revoked.version == 3
    assert denied.status == "denied" and denied.version == 2


def test_consent_reapply_supersedes_terminal_generation_without_rewriting_history() -> None:
    workspace, reviews, factory, _, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    workspace.change_consent(
        actors["reviewer"], _consent(project, job, action="reapply", expected_version=0, key="apply")
    )
    denied = workspace.change_consent(
        actors["reviewer"], _consent(project, job, action="deny", expected_version=1, key="deny")
    )
    reapplied = workspace.change_consent(
        actors["reviewer"],
        _consent(
            project,
            job,
            action="reapply",
            expected_version=denied.version,
            key="reapply",
            provider_revision=2,
            policy_version="policy-v2",
            data_scope={"paper_text": True},
        ),
    )

    with factory(actors["reviewer"]) as uow:
        old = uow.consents._state.consents[denied.id]
    assert old.status == "denied" and old.superseded_at == NOW and old.version == 3
    assert reapplied.id != denied.id
    assert reapplied.status == "pending" and reapplied.generation == 2 and reapplied.version == 1


def test_consent_replay_returns_original_snapshot_after_revoke_and_reapply() -> None:
    workspace, reviews, _, _, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    workspace.change_consent(
        actors["reviewer"], _consent(project, job, action="reapply", expected_version=0, key="apply")
    )
    grant_request = _consent(
        project,
        job,
        action="grant",
        expected_version=1,
        key="grant",
        expires_at=NOW + timedelta(hours=1),
    )
    granted = workspace.change_consent(actors["reviewer"], grant_request)
    workspace.change_consent(
        actors["reviewer"], _consent(project, job, action="revoke", expected_version=2, key="revoke")
    )
    replay = workspace.change_consent(actors["reviewer"], grant_request)
    assert replay == granted and replay.status == "granted" and replay.version == 2


def test_deterministic_consent_ids_include_tenant_scope() -> None:
    workspace, _, _, _, _, project, version = _seed()
    request = _consent(
        project,
        type("Job", (), {"id": uuid4(), "paper_version_id": version.id})(),
        action="reapply",
        expected_version=0,
        key="tenant-id",
    )
    first = workspace._pending_consent(
        request,
        organization_id=project.organization_id,
        generation=1,
        now=NOW,
    )
    second = workspace._pending_consent(
        request,
        organization_id=uuid4(),
        generation=1,
        now=NOW,
    )
    assert first.id != second.id
    assert workspace._document_id(
        TenantScope(project.organization_id, project.id), request.job_id
    ) != workspace._document_id(TenantScope(uuid4(), project.id), request.job_id)


def test_consent_expiration_is_explicit_and_future_expiry_is_required() -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    workspace.change_consent(
        actors["reviewer"], _consent(project, job, action="reapply", expected_version=0, key="apply")
    )
    with pytest.raises(ValueError, match="future"):
        workspace.change_consent(
            actors["reviewer"],
            _consent(
                project,
                job,
                action="grant",
                expected_version=1,
                key="past-expiry",
                expires_at=NOW,
            ),
        )
    granted = workspace.change_consent(
        actors["reviewer"],
        _consent(
            project,
            job,
            action="grant",
            expected_version=1,
            key="grant",
            expires_at=NOW + timedelta(minutes=1),
        ),
    )
    with pytest.raises(ValueError, match="not expired"):
        workspace.change_consent(
            actors["reviewer"],
            _consent(project, job, action="expire", expected_version=granted.version, key="early-expire"),
        )
    after_expiry = ReviewWorkspaceService(
        factory,
        store,
        clock=lambda: NOW + timedelta(minutes=2),
    )
    expired = after_expiry.change_consent(
        actors["reviewer"],
        _consent(project, job, action="expire", expected_version=granted.version, key="expire"),
    )
    assert expired.status == "expired" and expired.version == 3


def test_consent_rejects_paper_version_mismatch_invalid_transition_and_viewer() -> None:
    workspace, reviews, _, _, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    with pytest.raises(ValueError, match="paper version"):
        workspace.change_consent(
            actors["reviewer"],
            _consent(
                project,
                job,
                action="reapply",
                expected_version=0,
                key="wrong-paper",
                paper_version_id=uuid4(),
            ),
        )
    pending = workspace.change_consent(
        actors["reviewer"], _consent(project, job, action="reapply", expected_version=0, key="apply")
    )
    with pytest.raises(ValueError, match="pending"):
        workspace.change_consent(
            actors["reviewer"], _consent(project, job, action="revoke", expected_version=pending.version, key="bad-revoke")
        )
    with pytest.raises(Forbidden):
        workspace.change_consent(
            actors["viewer"], _consent(project, job, action="deny", expected_version=pending.version, key="viewer")
        )


def test_consent_expected_version_and_payload_digest_are_independent_conflicts() -> None:
    workspace, reviews, _, _, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    workspace.change_consent(
        actors["reviewer"], _consent(project, job, action="reapply", expected_version=0, key="apply")
    )
    with pytest.raises(StaleVersion):
        workspace.change_consent(
            actors["reviewer"], _consent(project, job, action="deny", expected_version=9, key="stale")
        )
    with pytest.raises(IdempotencyConflict):
        workspace.change_consent(
            actors["reviewer"],
            _consent(
                project,
                job,
                action="reapply",
                expected_version=0,
                key="apply",
                policy_version="changed",
            ),
        )


def test_review_document_is_lazily_created_and_saved_with_independent_cas() -> None:
    workspace, reviews, _, _, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    original = workspace.get_document(actors["reviewer"], project.id, job.id)
    assert workspace.get_document(actors["reviewer"], project.id, job.id) == original
    assert {block.section for block in original.blocks} == {
        "overall_assessment",
        "major_issues",
        "minor_issues",
        "revision_suggestions",
    }

    edited_blocks = tuple(
        replace(block, text="Strong overall contribution.")
        if block.section == "overall_assessment"
        else block
        for block in original.blocks
    )
    saved = workspace.save_document(
        actors["reviewer"],
        SaveReviewDocument(
            project.id,
            job.id,
            edited_blocks,
            original.document_version,
            "save",
            "request-save",
        ),
    )
    replay = workspace.save_document(
        actors["reviewer"],
        SaveReviewDocument(
            project.id,
            job.id,
            edited_blocks,
            original.document_version,
            "save",
            "request-save",
        ),
    )
    assert saved.document_version == 2
    assert replay == saved
    assert reviews.get(actors["reviewer"], project.id, job.id).version == job.version
    with pytest.raises(StaleVersion):
        workspace.save_document(
            actors["reviewer"],
            SaveReviewDocument(
                project.id,
                job.id,
                original.blocks,
                original.document_version,
                "stale-save",
                "request-stale-save",
            ),
        )
    with pytest.raises(IdempotencyConflict):
        workspace.save_document(
            actors["reviewer"],
            SaveReviewDocument(
                project.id,
                job.id,
                tuple(replace(block, text="Changed") for block in edited_blocks),
                original.document_version,
                "save",
                "request-save",
            ),
        )
    with pytest.raises(Forbidden):
        workspace.save_document(
            actors["viewer"],
            SaveReviewDocument(
                project.id,
                job.id,
                saved.blocks,
                saved.document_version,
                "viewer-save",
                "request-viewer-save",
            ),
        )


def test_document_replay_returns_original_version_after_a_later_save() -> None:
    workspace, reviews, _, _, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    original = workspace.get_document(actors["reviewer"], project.id, job.id)
    first_blocks = tuple(
        replace(block, text="First edit") if block.section == "overall_assessment" else block
        for block in original.blocks
    )
    first_request = SaveReviewDocument(
        project.id, job.id, first_blocks, original.document_version, "first-save", "request-first-save"
    )
    first = workspace.save_document(actors["reviewer"], first_request)
    second_blocks = tuple(
        replace(block, text="Second edit") if block.section == "overall_assessment" else block
        for block in first.blocks
    )
    workspace.save_document(
        actors["reviewer"],
        SaveReviewDocument(project.id, job.id, second_blocks, first.document_version, "second-save", "request-second-save"),
    )
    replay = workspace.save_document(actors["reviewer"], first_request)
    assert replay == first


def test_document_save_rejects_finding_revision_not_in_current_artifact() -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    _publish_review_result(factory, store, actors["reviewer"], project, job, revision=2)
    document = workspace.get_document(actors["reviewer"], project.id, job.id)
    stale = ReviewDocumentBlock(
        uuid4(),
        "major_issues",
        "Old projection",
        "finding",
        "lineage-method",
        "finding-method-v2",
        1,
        ("evidence-page-3",),
        {"page": 3},
    )
    with pytest.raises(StaleVersion):
        workspace.save_document(
            actors["reviewer"],
            SaveReviewDocument(
                project.id,
                job.id,
                (*document.blocks, stale),
                document.document_version,
                "stale-finding-save",
                "request-stale-finding-save",
            ),
        )


def test_finding_decision_appends_authoritative_event_and_projects_evidence_atomically() -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    payload = _publish_review_result(factory, store, actors["reviewer"], project, job)
    document = workspace.get_document(actors["reviewer"], project.id, job.id)

    request = _decision(project, job, document)
    result = workspace.decide_finding(actors["reviewer"], request)
    replay = workspace.decide_finding(actors["reviewer"], request)

    assert result.review_version == job.version + 1
    assert replay == result
    projected = workspace.get_document(actors["reviewer"], project.id, job.id)
    assert result.document_version == document.document_version + 1
    assert projected.base_decision_event_id == result.event.id
    assert result.event.aggregate_sequence == 2
    assert result.event.payload["finding_lineage_id"] == "lineage-method"
    assert result.event.payload["finding_id"] == "finding-method-v2"
    assert result.event.payload["finding_revision"] == 2
    assert result.event.payload["action"] == "accept"
    finding_block = next(
        block for block in projected.blocks if block.finding_lineage_id == "lineage-method"
    )
    assert finding_block.section == "major_issues"
    assert finding_block.text == payload["concerns"][0]["author_action"]
    assert finding_block.evidence_ids == ("evidence-page-3",)
    assert dict(finding_block.evidence_locator) == payload["concerns"][0]["evidence"][0]
    with pytest.raises(IdempotencyConflict):
        workspace.decide_finding(
            actors["reviewer"],
            replace(request, action="delete"),
        )


def test_document_save_cannot_mutate_or_add_finding_projections() -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    _publish_review_result(factory, store, actors["reviewer"], project, job)
    document = workspace.get_document(actors["reviewer"], project.id, job.id)
    accepted = workspace.decide_finding(actors["reviewer"], _decision(project, job, document))
    current_job = reviews.get(actors["reviewer"], project.id, job.id)
    current_document = workspace.get_document(actors["reviewer"], project.id, job.id)
    finding = next(
        block for block in current_document.blocks if block.finding_lineage_id == "lineage-method"
    )
    mutations = (
        replace(finding, text="Forged text"),
        replace(finding, evidence_ids=("forged-evidence",)),
        replace(finding, section="minor_issues"),
        ReviewDocumentBlock(
            uuid4(),
            "major_issues",
            "Forged finding",
            "finding_decision",
            "forged-lineage",
            "forged-id",
            1,
        ),
    )
    events_before = reviews.events(actors["reviewer"], project.id, job.id)
    for index, mutation in enumerate(mutations):
        blocks = tuple(mutation if block.id == finding.id else block for block in current_document.blocks)
        with pytest.raises(StaleVersion):
            workspace.save_document(
                actors["reviewer"],
                SaveReviewDocument(
                    project.id,
                    job.id,
                    blocks,
                    current_document.document_version,
                    f"forged-{index}",
                    f"request-forged-{index}",
                ),
            )
        assert reviews.get(actors["reviewer"], project.id, job.id) == current_job
        assert workspace.get_document(actors["reviewer"], project.id, job.id) == current_document
        assert reviews.events(actors["reviewer"], project.id, job.id) == events_before
        with factory(actors["reviewer"]) as uow:
            assert (
                uow.commands.get(
                    project.scope,
                    actors["reviewer"].actor_id,
                    "review_workspace.document_save",
                    f"forged-{index}",
                )
                is None
            )
    assert accepted.document_version == current_document.document_version


def test_finding_decision_rejects_artifact_descriptor_mismatch_without_writes() -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    _publish_review_result(factory, store, actors["reviewer"], project, job)
    document = workspace.get_document(actors["reviewer"], project.id, job.id)
    with factory(actors["reviewer"]) as uow:
        commands_before = tuple(uow.commands._state.commands.values())
    with factory(actors["reviewer"]) as uow:
        artifact = next(iter(uow.artifacts.list_for_job(project.scope, job.id)))
        uow.artifacts._state.artifacts[artifact.id] = replace(
            artifact,
            object=replace(artifact.object, size_bytes=artifact.object.size_bytes + 1),
        )
        uow.commit()
    events_before = reviews.events(actors["reviewer"], project.id, job.id)
    with pytest.raises(DependencyUnavailable):
        workspace.decide_finding(actors["reviewer"], _decision(project, job, document))
    assert reviews.get(actors["reviewer"], project.id, job.id) == job
    assert workspace.get_document(actors["reviewer"], project.id, job.id) == document
    assert reviews.events(actors["reviewer"], project.id, job.id) == events_before
    with factory(actors["reviewer"]) as uow:
        assert tuple(uow.commands._state.commands.values()) == commands_before


def test_finding_identity_duplicate_after_target_is_rejected() -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    duplicate = {
        "finding_lineage_id": "lineage-method",
        "finding_id": "finding-method-v2",
        "revision": 2,
        "level": "major_concern",
        "title": "Duplicate",
        "impact": "Duplicate identity after target.",
        "author_action": "Reject duplicate identity.",
        "evidence_ids": ["duplicate-evidence"],
        "evidence": [{"id": "duplicate-evidence", "locator": "pdf:page:9"}],
    }
    _publish_review_result(
        factory,
        store,
        actors["reviewer"],
        project,
        job,
        extra_concerns=[duplicate],
    )
    document = workspace.get_document(actors["reviewer"], project.id, job.id)
    events_before = reviews.events(actors["reviewer"], project.id, job.id)
    with factory(actors["reviewer"]) as uow:
        commands_before = tuple(uow.commands._state.commands.values())
    with pytest.raises(ValueError, match="identity"):
        workspace.decide_finding(actors["reviewer"], _decision(project, job, document))
    assert reviews.get(actors["reviewer"], project.id, job.id) == job
    assert workspace.get_document(actors["reviewer"], project.id, job.id) == document
    assert reviews.events(actors["reviewer"], project.id, job.id) == events_before
    with factory(actors["reviewer"]) as uow:
        assert tuple(uow.commands._state.commands.values()) == commands_before


@pytest.mark.parametrize(
    "concern_overrides",
    [
        {"evidence": {"id": "evidence-page-3"}},
        {"evidence": [{"locator": "pdf:page:3"}]},
        {
            "evidence": [
                {"id": "duplicate", "locator": "pdf:page:1"},
                {"id": "duplicate", "locator": "pdf:page:2"},
            ],
            "evidence_ids": ["duplicate"],
        },
        {"evidence_ids": ["dangling"]},
        {"evidence_ids": ["evidence-page-3", "evidence-page-3"]},
        {"evidence": [], "evidence_ids": []},
    ],
)
def test_finding_decision_rejects_malformed_or_dangling_evidence(
    concern_overrides,
) -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    _publish_review_result(
        factory,
        store,
        actors["reviewer"],
        project,
        job,
        concern_overrides=concern_overrides,
    )
    document = workspace.get_document(actors["reviewer"], project.id, job.id)
    events_before = reviews.events(actors["reviewer"], project.id, job.id)
    with factory(actors["reviewer"]) as uow:
        commands_before = tuple(uow.commands._state.commands.values())
    with pytest.raises(ValueError, match="evidence"):
        workspace.decide_finding(actors["reviewer"], _decision(project, job, document))
    assert reviews.get(actors["reviewer"], project.id, job.id) == job
    assert workspace.get_document(actors["reviewer"], project.id, job.id) == document
    assert reviews.events(actors["reviewer"], project.id, job.id) == events_before
    with factory(actors["reviewer"]) as uow:
        assert tuple(uow.commands._state.commands.values()) == commands_before


def test_finding_rewrite_downgrade_delete_replace_only_the_lineage_projection() -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    _publish_review_result(factory, store, actors["reviewer"], project, job)
    document = workspace.get_document(actors["reviewer"], project.id, job.id)
    workspace.decide_finding(
        actors["reviewer"],
        _decision(
            project,
            job,
            document,
            action="rewrite",
            rewrite_text="Explain the sampling limitations and mitigation.",
            key="rewrite",
        ),
    )
    rewritten_document = workspace.get_document(actors["reviewer"], project.id, job.id)
    block = next(item for item in rewritten_document.blocks if item.finding_lineage_id == "lineage-method")
    assert block.text == "Explain the sampling limitations and mitigation."
    workspace.decide_finding(
        actors["reviewer"],
        _decision(
            project,
            reviews.get(actors["reviewer"], project.id, job.id),
            rewritten_document,
            action="downgrade",
            key="downgrade",
        ),
    )
    downgraded_document = workspace.get_document(actors["reviewer"], project.id, job.id)
    block = next(item for item in downgraded_document.blocks if item.finding_lineage_id == "lineage-method")
    assert block.section == "minor_issues"
    workspace.decide_finding(
        actors["reviewer"],
        _decision(
            project,
            reviews.get(actors["reviewer"], project.id, job.id),
            downgraded_document,
            action="delete",
            key="delete",
        ),
    )
    assert not any(
        item.finding_lineage_id == "lineage-method"
        for item in workspace.get_document(actors["reviewer"], project.id, job.id).blocks
    )
    assert {item.section for item in workspace.get_document(actors["reviewer"], project.id, job.id).blocks} == {
        "overall_assessment",
        "major_issues",
        "minor_issues",
        "revision_suggestions",
    }


def test_finding_decision_rejects_stale_revision_blank_rewrite_and_event_drift() -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    _publish_review_result(factory, store, actors["reviewer"], project, job)
    document = workspace.get_document(actors["reviewer"], project.id, job.id)
    with pytest.raises(StaleVersion):
        workspace.decide_finding(
            actors["reviewer"],
            _decision(project, job, document, finding_revision=1, key="old-revision"),
        )


def test_finding_replay_returns_original_event_and_versions_after_later_decision() -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    _publish_review_result(factory, store, actors["reviewer"], project, job)
    document = workspace.get_document(actors["reviewer"], project.id, job.id)
    first_request = _decision(project, job, document, key="first-decision")
    first = workspace.decide_finding(actors["reviewer"], first_request)
    current_job = reviews.get(actors["reviewer"], project.id, job.id)
    current_document = workspace.get_document(actors["reviewer"], project.id, job.id)
    workspace.decide_finding(
        actors["reviewer"],
        _decision(project, current_job, current_document, action="delete", key="second-decision"),
    )
    replay = workspace.decide_finding(actors["reviewer"], first_request)
    assert replay.event == first.event
    assert replay.review_version == first.review_version
    assert replay.document_version == first.document_version
    with pytest.raises(ValueError, match="rewrite_text"):
        workspace.decide_finding(
            actors["reviewer"],
            _decision(project, job, document, action="rewrite", rewrite_text=" ", key="blank"),
        )
    with pytest.raises(StaleVersion):
        workspace.decide_finding(
            actors["reviewer"],
            _decision(
                project,
                job,
                document,
                key="event-drift",
                last_decision_event_id=uuid4(),
            ),
        )


def test_finding_document_conflict_rolls_back_job_event_document_and_command() -> None:
    workspace, reviews, factory, store, actors, project, version = _seed()
    job = _job(reviews, actors["reviewer"], project, version)
    _publish_review_result(factory, store, actors["reviewer"], project, job)
    document = workspace.get_document(actors["reviewer"], project.id, job.id)
    events_before = reviews.events(actors["reviewer"], project.id, job.id)

    with pytest.raises(StaleVersion):
        workspace.decide_finding(
            actors["reviewer"],
            _decision(
                project,
                job,
                document,
                key="atomic-conflict",
                expected_document_version=document.document_version + 1,
            ),
        )

    assert reviews.get(actors["reviewer"], project.id, job.id) == job
    assert workspace.get_document(actors["reviewer"], project.id, job.id) == document
    assert reviews.events(actors["reviewer"], project.id, job.id) == events_before
    with factory(actors["reviewer"]) as uow:
        assert (
            uow.commands.get(
                project.scope,
                actors["reviewer"].actor_id,
                "review_workspace.finding_decision",
                "atomic-conflict",
            )
            is None
        )
