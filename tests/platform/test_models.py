from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from inspect import getmembers, isfunction, signature
from pathlib import Path
from typing import get_type_hints
from uuid import UUID, uuid4

import pytest

from peerassist.platform import models
from peerassist.platform.errors import Forbidden, IdempotencyConflict
from peerassist.platform.models import (
    Action,
    Actor,
    ActorKind,
    AuditEvent,
    AuthenticatedIdentity,
    BrowserSession,
    CommandRecord,
    ExternalIdentity,
    InternalServiceGrant,
    LegacyRegistration,
    ObjectDescriptor,
    OidcTransaction,
    Organization,
    OutboxEvent,
    PaperVersion,
    Project,
    ReviewEvent,
    TenantScope,
    User,
    WorkItem,
)
from peerassist.platform.ports import (
    ArtifactRepository,
    AuditRepository,
    CommandRepository,
    LegacyRegistrationRepository,
    OrganizationRepository,
    OutboxRepository,
    PaperRepository,
    ProjectRepository,
    ReviewJobRepository,
    UnitOfWork,
    WorkItemRepository,
)

NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def test_primary_ids_are_strict_uuid_values() -> None:
    with pytest.raises(TypeError, match="UUID"):
        Actor(actor_id=str(uuid4()), kind=ActorKind.USER)  # type: ignore[arg-type]

    actor = Actor(actor_id=uuid4(), kind=ActorKind.USER)
    assert isinstance(actor.actor_id, UUID)


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 7, 17, 8, 0),
        datetime(2026, 7, 17, 10, 0, tzinfo=timezone(timedelta(hours=2))),
    ],
)
def test_persisted_timestamps_must_be_timezone_aware_utc(timestamp: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        User(id=uuid4(), status="active", display_name="Ada", created_at=timestamp, updated_at=NOW)


@pytest.mark.parametrize("version", [-1, 0])
def test_mutable_aggregate_versions_are_positive(version: int) -> None:
    with pytest.raises(ValueError, match="version"):
        Organization(
            id=uuid4(),
            slug="research-lab",
            name="Research Lab",
            status="active",
            version=version,
            created_at=NOW,
            updated_at=NOW,
        )


def test_project_rejects_an_unscoped_or_mismatched_tenant() -> None:
    organization_id = uuid4()
    project_id = uuid4()
    project = Project(
        id=project_id,
        organization_id=organization_id,
        name="Peer Review",
        status="active",
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )

    assert project.scope == TenantScope(organization_id=organization_id, project_id=project_id)
    with pytest.raises(ValueError, match="project_id"):
        TenantScope(organization_id=organization_id, project_id=UUID(int=0))


def test_work_item_enforces_sequence_attempt_and_lease_semantics() -> None:
    common = dict(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        job_id=uuid4(),
        attempt_id=uuid4(),
        stage="parse",
        input_revision=0,
        attempt_count=0,
        max_attempts=3,
        available_at=NOW,
        created_at=NOW,
    )

    with pytest.raises(ValueError, match="lease"):
        WorkItem(**common, lease_owner="worker-1")
    with pytest.raises(ValueError, match="input_revision"):
        WorkItem(**{**common, "input_revision": -1})
    with pytest.raises(ValueError, match="max_attempts"):
        WorkItem(**{**common, "max_attempts": 0})


def test_public_descriptors_reject_private_paths_and_secret_shaped_fields() -> None:
    descriptor = ObjectDescriptor(
        object_id="obj_01HSAFEOPAQUE",
        size_bytes=10,
        sha256="a" * 64,
        media_type="application/pdf",
    )

    assert descriptor.object_id == "obj_01HSAFEOPAQUE"
    with pytest.raises(ValueError, match="opaque"):
        ObjectDescriptor(
            object_id=str(Path("/srv/private/manuscript.pdf")),
            size_bytes=10,
            sha256="a" * 64,
            media_type="application/pdf",
        )

    public_models = (ObjectDescriptor, PaperVersion, LegacyRegistration, AuditEvent)
    forbidden_names = {"access_token", "refresh_token", "raw_token", "provider_response", "absolute_path"}
    for model in public_models:
        assert forbidden_names.isdisjoint(field.name for field in fields(model))


def test_legacy_registration_exposes_only_an_opaque_locator() -> None:
    registration = LegacyRegistration(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        legacy_type="m0_review_job",
        opaque_locator="legacy_01HREGISTERED",
        manifest_sha256="b" * 64,
        status="read_only",
        version=1,
        created_at=NOW,
    )

    assert registration.read_only is True
    with pytest.raises(ValueError, match="opaque"):
        LegacyRegistration(
            id=uuid4(),
            organization_id=uuid4(),
            project_id=uuid4(),
            legacy_type="m0_review_job",
            opaque_locator="/private/jobs/job-1",
            manifest_sha256="b" * 64,
            status="read_only",
            version=1,
            created_at=NOW,
        )


def test_browser_and_oidc_models_retain_only_protected_server_side_material() -> None:
    identity = ExternalIdentity(
        id=uuid4(),
        user_id=uuid4(),
        issuer="https://identity.example.test",
        subject="subject-1",
        verified_claims={"email_verified": True},
        last_seen_at=NOW,
        created_at=NOW,
    )
    session = BrowserSession(
        id=uuid4(),
        user_id=identity.user_id,
        identity_id=identity.id,
        session_digest="a" * 64,
        csrf_digest="b" * 64,
        provider_credential_ref="credential_01HOPAQUE",
        expires_at=NOW + timedelta(hours=8),
        idle_expires_at=NOW + timedelta(hours=1),
        created_at=NOW,
    )
    transaction = OidcTransaction(
        id=uuid4(),
        state_digest="c" * 64,
        nonce_digest="d" * 64,
        encrypted_pkce_verifier=b"ciphertext",
        encryption_key_id="session-key-1",
        return_path="/compat/paper",
        expires_at=NOW + timedelta(minutes=10),
        created_at=NOW,
    )

    assert session.provider_credential_ref.startswith("credential_")
    assert transaction.return_path == "/compat/paper"
    assert not hasattr(session, "access_token")
    with pytest.raises(ValueError, match="relative"):
        OidcTransaction(
            id=uuid4(),
            state_digest="c" * 64,
            nonce_digest="d" * 64,
            encrypted_pkce_verifier=b"ciphertext",
            encryption_key_id="session-key-1",
            return_path="https://evil.example.test/steal",
            expires_at=NOW + timedelta(minutes=10),
            created_at=NOW,
        )


def test_domain_models_are_immutable_value_records() -> None:
    actor = Actor(actor_id=uuid4(), kind=ActorKind.USER)
    with pytest.raises(FrozenInstanceError):
        actor.actor_id = uuid4()  # type: ignore[misc]


def test_command_record_validates_digest_and_nonempty_key() -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        CommandRecord(
            id=uuid4(),
            organization_id=uuid4(),
            actor_id=uuid4(),
            operation="paper.upload",
            idempotency_key=" ",
            payload_digest="a" * 64,
            created_at=NOW,
        )


def test_uow_exposes_domain_repositories_without_a_provider_session() -> None:
    repositories = get_type_hints(UnitOfWork)

    assert "oidc_transactions" in repositories
    assert "session" not in repositories
    assert signature(ProjectRepository.get).parameters["scope"].annotation in {
        TenantScope,
        "TenantScope",
    }
    assert signature(ProjectRepository.add).parameters["scope"].annotation in {
        TenantScope,
        "TenantScope",
    }


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    [
        (NOW, NOW),
        (NOW, NOW - timedelta(seconds=1)),
        (NOW, NOW + timedelta(minutes=15, seconds=1)),
        (NOW.replace(tzinfo=None), NOW + timedelta(minutes=5)),
    ],
)
def test_internal_service_grants_are_utc_and_short_lived(
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    with pytest.raises(ValueError, match=r"UTC|expires_at|short-lived"):
        InternalServiceGrant(
            service_actor_id=uuid4(),
            scope=TenantScope(uuid4(), uuid4()),
            audience="peerassist-worker",
            actions=frozenset({Action.INTERNAL_TOOL_EXECUTE}),
            issued_at=issued_at,
            expires_at=expires_at,
        )


def test_internal_service_grant_contains_no_raw_token() -> None:
    grant = InternalServiceGrant(
        grant_id=uuid4(),
        service_actor_id=uuid4(),
        scope=TenantScope(uuid4(), uuid4()),
        audience="peerassist-worker",
        actions=frozenset({Action.INTERNAL_TOOL_EXECUTE}),
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    assert not hasattr(grant, "raw_token")
    assert not hasattr(grant, "token")


def test_event_payloads_take_deeply_immutable_snapshots() -> None:
    source = {"nested": [{"value": "original"}]}
    common = dict(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=uuid4(),
        aggregate_sequence=1,
        event_type="review.started",
        schema_version=1,
        payload=source,
        created_at=NOW,
    )
    review_event = ReviewEvent(**common, job_id=uuid4())
    outbox_event = OutboxEvent(
        **common,
        aggregate_type="review_job",
        aggregate_id=uuid4(),
    )

    source["nested"][0]["value"] = "mutated"
    assert review_event.payload["nested"][0]["value"] == "original"
    assert outbox_event.payload["nested"][0]["value"] == "original"
    with pytest.raises(TypeError):
        review_event.payload["new"] = "blocked"  # type: ignore[index]
    with pytest.raises(TypeError):
        review_event.payload["nested"][0]["value"] = "blocked"  # type: ignore[index]


def test_claims_responses_and_audit_metadata_are_deeply_immutable_snapshots() -> None:
    source = {"groups": ["reviewers"], "profile": {"verified": True}}
    identity = AuthenticatedIdentity(
        issuer="https://identity.example.test",
        subject="subject-1",
        claims=source,
        expires_at=NOW + timedelta(minutes=5),
    )
    command = CommandRecord(
        id=uuid4(),
        organization_id=uuid4(),
        actor_id=uuid4(),
        operation="paper.upload",
        idempotency_key="request-1",
        payload_digest="a" * 64,
        response_status=201,
        response_body=source,
        created_at=NOW,
        completed_at=NOW,
    )
    audit = AuditEvent(
        id=uuid4(),
        actor_id=uuid4(),
        organization_id=uuid4(),
        action=Action.PAPER_UPLOAD,
        resource_type="paper",
        resource_id=uuid4(),
        outcome="allowed",
        request_id="request-1",
        safe_metadata=source,
        created_at=NOW,
    )

    source["groups"].append("owners")
    source["profile"]["verified"] = False
    for snapshot in (identity.claims, command.response_body, audit.safe_metadata):
        assert snapshot["groups"] == ("reviewers",)
        assert snapshot["profile"]["verified"] is True
        with pytest.raises(TypeError):
            snapshot["profile"]["verified"] = False  # type: ignore[index]


@pytest.mark.parametrize("claim_name", ["access_token", "oidc_refresh_token", "provider_response"])
def test_identity_claim_snapshots_reject_secret_fields(claim_name: str) -> None:
    with pytest.raises(ValueError, match="safe public JSON"):
        AuthenticatedIdentity(
            issuer="https://identity.example.test",
            subject="subject-1",
            claims={claim_name: "secret-value"},
            expires_at=NOW + timedelta(minutes=5),
        )


@pytest.mark.parametrize(
    "unsafe_payload",
    [
        {"access_token": "secret-value"},
        {"nested": {"oidc_access_token": "secret-value"}},
        {"nested": {"provider_response": "private body"}},
        {"manuscript_content": "private paper text"},
        {"object_key": "org/private/object"},
        {"workspace": "/srv/private/manuscript.pdf"},
        {"presigned_url": "https://storage.example.test/signed"},
    ],
)
def test_public_payload_boundaries_reject_secrets_and_private_paths(
    unsafe_payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="safe public JSON"):
        AuditEvent(
            id=uuid4(),
            actor_id=uuid4(),
            organization_id=uuid4(),
            action=Action.PAPER_UPLOAD,
            resource_type="paper",
            resource_id=uuid4(),
            outcome="denied",
            request_id="request-1",
            safe_metadata=unsafe_payload,
            created_at=NOW,
        )


def test_platform_errors_publish_only_registered_messages_and_safe_details() -> None:
    error = Forbidden(details={"resource_type": "paper", "reason": "membership_revoked"})

    assert str(error) == "The requested operation is not permitted."
    assert repr(error) == "Forbidden(code='forbidden')"
    assert error.public_fields(request_id="request-1") == {
        "code": "forbidden",
        "message": "The requested operation is not permitted.",
        "request_id": "request-1",
        "retryable": False,
        "details": {"resource_type": "paper", "reason": "membership_revoked"},
    }
    with pytest.raises(TypeError):
        Forbidden("provider failed with token secret")  # type: ignore[call-arg]


def test_platform_error_internal_cause_never_enters_public_fields_or_display() -> None:
    error = Forbidden(cause=RuntimeError("provider token secret-value"))
    public = error.public_fields(request_id="request-1")

    assert error.__cause__ is not None
    for rendered in (str(error), repr(error), repr(public)):
        assert "provider" not in rendered
        assert "secret-value" not in rendered


def test_platform_error_public_snapshot_cannot_be_mutated_after_construction() -> None:
    original_details = {"resource_type": "paper", "reason": "membership_revoked"}
    error = Forbidden(details=original_details)
    original_details["reason"] = "changed"

    assert error.public_fields(request_id="request-1")["details"]["reason"] == "membership_revoked"
    for name, value in (
        ("message", "provider secret"),
        ("details", {"reason": "provider_secret"}),
        ("code", "provider_secret"),
        ("retryable", True),
    ):
        with pytest.raises((AttributeError, TypeError)):
            setattr(error, name, value)

    error.__dict__.update(
        {
            "_public_code": "provider_secret",
            "_public_message": "provider token secret",
            "_public_details": {"reason": "provider_secret"},
            "_sealed": False,
        }
    )
    assert error.public_fields(request_id="request-1") == {
        "code": "forbidden",
        "message": "The requested operation is not permitted.",
        "request_id": "request-1",
        "retryable": False,
        "details": {"resource_type": "paper", "reason": "membership_revoked"},
    }
    with pytest.raises(AttributeError):
        error.message = "provider token secret"  # type: ignore[misc]


@pytest.mark.parametrize(
    "request_id",
    [
        "/srv/private/request",
        "request token secret",
        "request\nforged",
        "x" * 129,
        "",
    ],
)
def test_platform_error_rejects_unsafe_request_ids(request_id: str) -> None:
    error = Forbidden()
    with pytest.raises(ValueError, match="request_id"):
        error.public_fields(request_id=request_id)


@pytest.mark.parametrize(
    "unsafe_details",
    [
        {"access_token": "secret-value"},
        {"reason": "/srv/private/manuscript.pdf"},
        {"provider_response": "issuer said no"},
        {"reason": "provider returned raw token secret-value"},
        {"reason": RuntimeError("provider secret")},
    ],
)
def test_platform_errors_reject_unsafe_public_details(unsafe_details: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        IdempotencyConflict(details=unsafe_details)  # type: ignore[arg-type]


@pytest.mark.parametrize("unsafe_value", [float("nan"), float("inf"), "\ud800"])
def test_persisted_json_snapshots_reject_noncanonical_values(unsafe_value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="JSON"):
        AuthenticatedIdentity(
            issuer="https://identity.example.test",
            subject="subject-1",
            claims={"value": unsafe_value},
            expires_at=NOW + timedelta(minutes=5),
        )


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "TOKEN",
        "id-token",
        "session token",
        "client.secret",
        "Api-Key",
        "PASSWORD",
        "authORIZATION",
        "signed url",
        "private.path",
        "Provider-Response",
        "manuscript content",
        "object key",
        "oidc.client_secret",
        "accessToken",
        "clientSecret",
        "presignedUrl",
        "providerResponse",
        "manuscriptText",
        "pdf-content",
    ],
)
def test_public_json_key_normalization_closes_secret_bypasses(unsafe_key: str) -> None:
    with pytest.raises(ValueError, match="safe public JSON"):
        AuditEvent(
            id=uuid4(),
            actor_id=uuid4(),
            organization_id=uuid4(),
            action=Action.PAPER_UPLOAD,
            resource_type="paper",
            resource_id=uuid4(),
            outcome="denied",
            request_id="request-1",
            safe_metadata={"nested": {unsafe_key: "secret-value"}},
            created_at=NOW,
        )


@pytest.mark.parametrize(
    "claim_key",
    [
        "id_token",
        "session-token",
        "client secret",
        "api.key",
        "password",
        "authorization",
        "cookie",
        "sessionToken",
        "apiKey",
    ],
)
def test_identity_claims_reject_raw_credential_variants(claim_key: str) -> None:
    with pytest.raises(ValueError, match="safe public JSON"):
        AuthenticatedIdentity(
            issuer="https://identity.example.test",
            subject="subject-1",
            claims={claim_key: "secret-value"},
            expires_at=NOW + timedelta(minutes=5),
        )


def test_all_tenant_repository_mutations_require_scope_first() -> None:
    repositories = (
        OrganizationRepository,
        ProjectRepository,
        PaperRepository,
        ReviewJobRepository,
        ArtifactRepository,
        CommandRepository,
        WorkItemRepository,
        OutboxRepository,
        AuditRepository,
        LegacyRegistrationRepository,
    )
    mutation_names = {
        "add",
        "save",
        "save_membership",
        "add_version",
        "append_event",
        "reserve",
        "complete",
        "enqueue",
        "fail",
        "mark_published",
        "append",
    }

    for repository in repositories:
        for method_name, method in getmembers(repository, isfunction):
            if method_name not in mutation_names:
                continue
            parameters = list(signature(method).parameters.values())
            assert parameters[1].name == "scope", (repository.__name__, method_name)
            assert parameters[1].annotation in {TenantScope, "TenantScope"}, (
                repository.__name__,
                method_name,
            )


def _consent(**changes: object):
    values = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "project_id": uuid4(),
        "review_job_id": uuid4(),
        "paper_version_id": uuid4(),
        "service": "model",
        "provider_config_revision": 3,
        "policy_version": "model-policy-v1",
        "data_scope": {"fields": ["title", "abstract"]},
        "status": "granted",
        "generation": 1,
        "version": 1,
        "decided_by": uuid4(),
        "decided_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
        "superseded_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return models.ExternalServiceConsent(**values)


def test_external_service_consent_is_immutable_and_effective_only_for_current_grant() -> None:
    source_scope = {"fields": ["title", "abstract"]}
    consent = _consent(data_scope=source_scope)

    source_scope["fields"].append("full_text")
    assert consent.data_scope["fields"] == ("title", "abstract")
    assert consent.is_effective(provider_config_revision=3, at=NOW + timedelta(minutes=30))
    assert not consent.is_effective(provider_config_revision=4, at=NOW + timedelta(minutes=30))
    assert not consent.is_effective(provider_config_revision=3, at=NOW + timedelta(hours=1))
    assert not replace(consent, status="denied").is_effective(
        provider_config_revision=3,
        at=NOW,
    )
    assert not replace(consent, superseded_at=NOW).is_effective(
        provider_config_revision=3,
        at=NOW,
    )
    with pytest.raises(FrozenInstanceError):
        consent.status = "denied"  # type: ignore[misc]


def test_external_service_consent_is_not_effective_before_its_decision_time() -> None:
    decided_at = NOW + timedelta(minutes=5)
    consent = _consent(
        decided_at=decided_at,
        expires_at=decided_at + timedelta(hours=1),
    )

    assert not consent.is_effective(provider_config_revision=3, at=NOW)
    assert consent.is_effective(provider_config_revision=3, at=decided_at)


def test_not_required_consent_must_record_a_decision() -> None:
    with pytest.raises(ValueError, match="decision"):
        _consent(
            status="not_required",
            decided_by=None,
            decided_at=None,
            expires_at=None,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"status": "approved"}, "status"),
        ({"generation": 0}, "generation"),
        ({"version": 0}, "version"),
        ({"provider_config_revision": 0}, "provider_config_revision"),
        ({"data_scope": {"fields": ("abstract",)}}, "JSON"),
        ({"data_scope": {"value": float("nan")}}, "JSON"),
    ],
)
def test_external_service_consent_rejects_invalid_state_and_json(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _consent(**changes)


def _document_block(**changes: object):
    values = {
        "id": uuid4(),
        "section": "major_issues",
        "text": "The evidence does not support this conclusion.",
        "source_type": "finding",
        "finding_lineage_id": "fln_statistics_claim",
        "finding_id": "finding_statistics_claim_r1",
        "finding_revision": 1,
        "evidence_ids": ("evidence-1",),
        "evidence_locator": {"page": 3, "spans": [{"start": 12, "end": 42}]},
    }
    values.update(changes)
    return models.ReviewDocumentBlock(**values)


def test_review_document_blocks_have_fixed_sections_and_immutable_references() -> None:
    locator = {"page": 3, "spans": [{"start": 12, "end": 42}]}
    block = _document_block(evidence_locator=locator)

    locator["spans"][0]["start"] = 99
    assert models.REVIEW_DOCUMENT_SECTIONS == (
        "overall_assessment",
        "major_issues",
        "minor_issues",
        "revision_suggestions",
    )
    assert block.evidence_ids == ("evidence-1",)
    assert block.evidence_locator["spans"][0]["start"] == 12
    with pytest.raises(TypeError):
        block.evidence_locator["page"] = 4  # type: ignore[index]
    with pytest.raises(ValueError, match="section"):
        _document_block(section="appendix")
    with pytest.raises(ValueError, match="finding"):
        _document_block(finding_id=None, finding_revision=None)
    with pytest.raises(ValueError, match="text"):
        _document_block(text="x" * 100_001)
    with pytest.raises((TypeError, ValueError), match="JSON"):
        _document_block(evidence_locator={"page": (3,)})


def test_review_document_empty_factory_and_document_version_are_independent() -> None:
    document_id = uuid4()
    organization_id = uuid4()
    project_id = uuid4()
    job_id = uuid4()
    editor_id = uuid4()

    document = models.ReviewDocument.empty(
        id=document_id,
        organization_id=organization_id,
        project_id=project_id,
        review_job_id=job_id,
        last_edited_by=editor_id,
        created_at=NOW,
    )
    same_document = models.ReviewDocument.empty(
        id=document_id,
        organization_id=organization_id,
        project_id=project_id,
        review_job_id=job_id,
        last_edited_by=editor_id,
        created_at=NOW,
    )

    assert document.id == document_id
    assert tuple(block.section for block in document.blocks) == models.REVIEW_DOCUMENT_SECTIONS
    assert tuple(block.id for block in document.blocks) == tuple(
        block.id for block in same_document.blocks
    )
    assert all(block.text == "" and block.source_type == "manual" for block in document.blocks)
    assert document.document_version == 1
    assert document.base_decision_event_id is None
    assert document.last_edited_by == editor_id
    assert document.created_at == NOW
    assert document.updated_at == NOW
    with pytest.raises(ValueError, match="document_version"):
        replace(document, document_version=0)


def test_review_document_requires_every_section_but_allows_multiple_blocks_per_section() -> None:
    blocks = tuple(
        _document_block(
            section=section,
            finding_lineage_id=None,
            finding_id=None,
            finding_revision=None,
            evidence_ids=(),
            evidence_locator=None,
        )
        for section in models.REVIEW_DOCUMENT_SECTIONS
    )
    common = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "project_id": uuid4(),
        "review_job_id": uuid4(),
        "document_version": 1,
        "base_decision_event_id": None,
        "last_edited_by": uuid4(),
        "created_at": NOW,
        "updated_at": NOW,
    }

    with pytest.raises(ValueError, match="all four sections"):
        models.ReviewDocument(blocks=blocks[:-1], **common)

    additional_major_issue = replace(blocks[1], id=uuid4(), text="A second major issue.")
    document = models.ReviewDocument(
        blocks=(*blocks, additional_major_issue),
        **common,
    )
    assert [block.section for block in document.blocks].count("major_issues") == 2
