from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from inspect import signature
from pathlib import Path
from typing import get_type_hints
from uuid import UUID, uuid4

import pytest

from peerassist.platform.models import (
    Actor,
    ActorKind,
    AuditEvent,
    BrowserSession,
    CommandRecord,
    ExternalIdentity,
    LegacyRegistration,
    ObjectDescriptor,
    OidcTransaction,
    Organization,
    PaperVersion,
    Project,
    TenantScope,
    User,
    WorkItem,
)
from peerassist.platform.ports import ProjectRepository, UnitOfWork

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
