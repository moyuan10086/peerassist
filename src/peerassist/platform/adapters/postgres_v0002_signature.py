"""Immutable PostgreSQL 16 catalog signature for schema revision 0002."""

from __future__ import annotations

from types import MappingProxyType

from .postgres_v0001_signature import (
    CATALOG_INSPECTION_SQL,
    catalog_fingerprint,
    catalog_signature_from_row,
)
from .postgres_v0001_signature import (
    EXPECTED_CATALOG_SIGNATURE as V0001_SIGNATURE,
)

__all__ = [
    "CATALOG_INSPECTION_SQL",
    "EXPECTED_CATALOG_FINGERPRINT",
    "EXPECTED_CATALOG_SIGNATURE",
    "OWNED_TABLES",
    "catalog_fingerprint",
    "catalog_signature_from_row",
]

_CHANGED_TABLES = frozenset(
    {"external_service_consents", "project_memberships", "review_documents", "review_events"}
)

_V0002_DELTA = {
    "tables": (
        "external_service_consents|r|p",
        "project_memberships|r|p",
        "review_documents|r|p",
        "review_events|r|p",
    ),
    "columns": (
        "external_service_consents|1|id|uuid|t|||",
        "external_service_consents|2|organization_id|uuid|t|||",
        "external_service_consents|3|project_id|uuid|t|||",
        "external_service_consents|4|review_job_id|uuid|t|||",
        "external_service_consents|5|paper_version_id|uuid|t|||",
        "external_service_consents|6|service|character varying(128)|t|||",
        "external_service_consents|7|provider_config_revision|integer|t|||",
        "external_service_consents|8|policy_version|character varying(128)|t|||",
        "external_service_consents|9|data_scope|jsonb|t|||",
        "external_service_consents|10|status|character varying(32)|t|||",
        "external_service_consents|11|generation|integer|t|||",
        "external_service_consents|12|version|integer|t|||",
        "external_service_consents|13|decided_by|uuid|f|||",
        "external_service_consents|14|decided_at|timestamp with time zone|f|||",
        "external_service_consents|15|expires_at|timestamp with time zone|f|||",
        "external_service_consents|16|superseded_at|timestamp with time zone|f|||",
        "external_service_consents|17|created_at|timestamp with time zone|t|||",
        "external_service_consents|18|updated_at|timestamp with time zone|t|||",
        "project_memberships|1|id|uuid|t|||",
        "project_memberships|2|organization_id|uuid|t|||",
        "project_memberships|3|project_id|uuid|t|||",
        "project_memberships|4|user_id|uuid|t|||",
        "project_memberships|5|role|character varying(32)|t|||",
        "project_memberships|6|status|character varying(32)|t|||",
        "project_memberships|7|version|integer|t|||",
        "project_memberships|8|created_at|timestamp with time zone|t|||",
        "project_memberships|9|updated_at|timestamp with time zone|t|||",
        "project_memberships|10|revoked_at|timestamp with time zone|f|||",
        "project_memberships|11|is_default|boolean|t|false||",
        "review_documents|1|id|uuid|t|||",
        "review_documents|2|organization_id|uuid|t|||",
        "review_documents|3|project_id|uuid|t|||",
        "review_documents|4|review_job_id|uuid|t|||",
        "review_documents|5|blocks|jsonb|t|||",
        "review_documents|6|document_version|integer|t|||",
        "review_documents|7|base_decision_event_id|uuid|f|||",
        "review_documents|8|last_edited_by|uuid|t|||",
        "review_documents|9|created_at|timestamp with time zone|t|||",
        "review_documents|10|updated_at|timestamp with time zone|t|||",
        "review_events|1|id|uuid|t|||",
        "review_events|2|organization_id|uuid|t|||",
        "review_events|3|project_id|uuid|t|||",
        "review_events|4|job_id|uuid|t|||",
        "review_events|5|aggregate_sequence|bigint|t|||",
        "review_events|6|event_type|character varying(128)|t|||",
        "review_events|7|schema_version|integer|t|||",
        "review_events|8|payload|jsonb|t|||",
        "review_events|9|created_at|timestamp with time zone|t|||",
    ),
    "constraints": (
        "external_service_consents|ck_external_service_consents_decision_pair|c|CHECK ((decided_by IS NULL) = (decided_at IS NULL))",
        "external_service_consents|ck_external_service_consents_status|c|CHECK (status::text = ANY (ARRAY['denied'::character varying, 'expired'::character varying, 'granted'::character varying, 'not_required'::character varying, 'pending'::character varying, 'revoked'::character varying]::text[]))",
        "external_service_consents|ck_external_service_consents_versions_positive|c|CHECK (provider_config_revision > 0 AND generation > 0 AND version > 0)",
        "external_service_consents|fk_external_service_consents_decided_by|f|FOREIGN KEY (decided_by) REFERENCES users(id)",
        "external_service_consents|fk_external_service_consents_job_tenant|f|FOREIGN KEY (organization_id, project_id, review_job_id) REFERENCES review_jobs(organization_id, project_id, id)",
        "external_service_consents|fk_external_service_consents_paper_version_tenant|f|FOREIGN KEY (organization_id, project_id, paper_version_id) REFERENCES paper_versions(organization_id, project_id, id)",
        "external_service_consents|pk_external_service_consents|p|PRIMARY KEY (id)",
        "external_service_consents|uq_external_service_consents_generation|u|UNIQUE (review_job_id, paper_version_id, service, generation)",
        "project_memberships|ck_project_memberships_role|c|CHECK (role::text = ANY (ARRAY['project_owner'::character varying, 'reviewer'::character varying, 'viewer'::character varying]::text[]))",
        "project_memberships|ck_project_memberships_status|c|CHECK (status::text = ANY (ARRAY['active'::character varying, 'revoked'::character varying]::text[]))",
        "project_memberships|ck_project_memberships_version_positive|c|CHECK (version > 0)",
        "project_memberships|fk_project_memberships_project_tenant|f|FOREIGN KEY (organization_id, project_id) REFERENCES projects(organization_id, id)",
        "project_memberships|fk_project_memberships_user|f|FOREIGN KEY (user_id) REFERENCES users(id)",
        "project_memberships|pk_project_memberships|p|PRIMARY KEY (id)",
        "project_memberships|uq_project_memberships_project_user|u|UNIQUE (project_id, user_id)",
        "review_documents|ck_review_documents_version_positive|c|CHECK (document_version > 0)",
        "review_documents|fk_review_documents_base_event_tenant|f|FOREIGN KEY (organization_id, project_id, review_job_id, base_decision_event_id) REFERENCES review_events(organization_id, project_id, job_id, id)",
        "review_documents|fk_review_documents_job_tenant|f|FOREIGN KEY (organization_id, project_id, review_job_id) REFERENCES review_jobs(organization_id, project_id, id)",
        "review_documents|fk_review_documents_last_edited_by|f|FOREIGN KEY (last_edited_by) REFERENCES users(id)",
        "review_documents|pk_review_documents|p|PRIMARY KEY (id)",
        "review_documents|uq_review_documents_job|u|UNIQUE (review_job_id)",
        "review_events|ck_review_events_schema_version_positive|c|CHECK (schema_version > 0)",
        "review_events|ck_review_events_sequence_positive|c|CHECK (aggregate_sequence > 0)",
        "review_events|fk_review_events_job_tenant|f|FOREIGN KEY (organization_id, project_id, job_id) REFERENCES review_jobs(organization_id, project_id, id)",
        "review_events|pk_review_events|p|PRIMARY KEY (id)",
        "review_events|uq_review_events_job_sequence|u|UNIQUE (job_id, aggregate_sequence)",
        "review_events|uq_review_events_tenant_id|u|UNIQUE (organization_id, project_id, job_id, id)",
    ),
    "indexes": (
        "external_service_consents|ix_external_service_consents_tenant_job|CREATE INDEX ix_external_service_consents_tenant_job ON external_service_consents USING btree (organization_id, project_id, review_job_id)",
        "external_service_consents|pk_external_service_consents|CREATE UNIQUE INDEX pk_external_service_consents ON external_service_consents USING btree (id)",
        "external_service_consents|uq_external_service_consents_current|CREATE UNIQUE INDEX uq_external_service_consents_current ON external_service_consents USING btree (review_job_id, paper_version_id, service) WHERE superseded_at IS NULL",
        "external_service_consents|uq_external_service_consents_generation|CREATE UNIQUE INDEX uq_external_service_consents_generation ON external_service_consents USING btree (review_job_id, paper_version_id, service, generation)",
        "project_memberships|ix_project_memberships_tenant_user|CREATE INDEX ix_project_memberships_tenant_user ON project_memberships USING btree (organization_id, user_id)",
        "project_memberships|pk_project_memberships|CREATE UNIQUE INDEX pk_project_memberships ON project_memberships USING btree (id)",
        "project_memberships|uq_project_memberships_default_user_organization|CREATE UNIQUE INDEX uq_project_memberships_default_user_organization ON project_memberships USING btree (organization_id, user_id) WHERE is_default AND status::text = 'active'::text AND revoked_at IS NULL",
        "project_memberships|uq_project_memberships_project_user|CREATE UNIQUE INDEX uq_project_memberships_project_user ON project_memberships USING btree (project_id, user_id)",
        "review_documents|ix_review_documents_tenant_job|CREATE INDEX ix_review_documents_tenant_job ON review_documents USING btree (organization_id, project_id, review_job_id)",
        "review_documents|pk_review_documents|CREATE UNIQUE INDEX pk_review_documents ON review_documents USING btree (id)",
        "review_documents|uq_review_documents_job|CREATE UNIQUE INDEX uq_review_documents_job ON review_documents USING btree (review_job_id)",
        "review_events|ix_review_events_tenant_cursor|CREATE INDEX ix_review_events_tenant_cursor ON review_events USING btree (organization_id, project_id, created_at, id)",
        "review_events|pk_review_events|CREATE UNIQUE INDEX pk_review_events ON review_events USING btree (id)",
        "review_events|uq_review_events_job_sequence|CREATE UNIQUE INDEX uq_review_events_job_sequence ON review_events USING btree (job_id, aggregate_sequence)",
        "review_events|uq_review_events_tenant_id|CREATE UNIQUE INDEX uq_review_events_tenant_id ON review_events USING btree (organization_id, project_id, job_id, id)",
    ),
}


def _table_name(entry: str) -> str:
    return entry.split("|", 1)[0]


def _merge(key: str) -> tuple[str, ...]:
    groups: dict[str, list[str]] = {}
    entries = [
        entry
        for entry in V0001_SIGNATURE[key]
        if _table_name(entry) not in _CHANGED_TABLES
    ] + list(_V0002_DELTA.get(key, ()))
    for entry in entries:
        groups.setdefault(_table_name(entry), []).append(entry)
    return tuple(entry for table in sorted(groups) for entry in groups[table])


EXPECTED_CATALOG_SIGNATURE = MappingProxyType(
    {
        key: _merge(key)
        if key in _V0002_DELTA
        else V0001_SIGNATURE[key]
        for key in V0001_SIGNATURE
    }
)

OWNED_TABLES = tuple(entry.split("|", 1)[0] for entry in EXPECTED_CATALOG_SIGNATURE["tables"])
EXPECTED_CATALOG_FINGERPRINT = catalog_fingerprint(EXPECTED_CATALOG_SIGNATURE)
