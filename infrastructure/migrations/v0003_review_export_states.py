"""Frozen schema operations for review export states revision 0003."""

from __future__ import annotations

import sqlalchemy as sa
from alembic.operations import Operations

REVISION = "0003_review_export_states"
V0002_CATALOG_FINGERPRINT = "df9d06223cafb3ca982a8de3724f61744bf7415ef2dff0c68bf2adf4fb7d64d5"
V0003_CATALOG_FINGERPRINT = "574a62ad37716b3ca44ca6f51e8cc14b1c44f0de8181e4669ae09e2c91a9914e"

_STAGE_V0002 = (
    "queued",
    "prepare",
    "parse",
    "positioning",
    "citation_audit",
    "execution",
    "review",
    "report",
    "finalize",
    "completed",
)
_STAGE_V0003 = (*_STAGE_V0002[:-1], "export", "completed")
_STATUS_V0002 = (
    "queued",
    "running",
    "blocked",
    "cancelling",
    "cancelled",
    "failed",
    "completed",
)
_STATUS_V0003 = (
    "queued",
    "running",
    "blocked",
    "exporting_report",
    "cancelling",
    "cancelled",
    "failed",
    "completed",
)


def _allowed(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _replace_constraints(op: Operations, stages: tuple[str, ...], statuses: tuple[str, ...]) -> None:
    op.drop_constraint("ck_review_jobs_stage", "review_jobs", type_="check")
    op.drop_constraint("ck_review_jobs_status", "review_jobs", type_="check")
    op.create_check_constraint("ck_review_jobs_stage", "review_jobs", _allowed("stage", stages))
    op.create_check_constraint("ck_review_jobs_status", "review_jobs", _allowed("status", statuses))


def upgrade_v0003(op: Operations) -> None:
    _replace_constraints(op, _STAGE_V0003, _STATUS_V0003)
    _set_revision(op, REVISION, V0003_CATALOG_FINGERPRINT)


def downgrade_v0003(op: Operations) -> None:
    _replace_constraints(op, _STAGE_V0002, _STATUS_V0002)
    _set_revision(op, "0002_review_workspace", V0002_CATALOG_FINGERPRINT)


def _set_revision(op: Operations, revision: str, fingerprint: str) -> None:
    op.execute(
        sa.text(
            "UPDATE schema_metadata SET value = :revision, updated_at = now() WHERE key = 'platform_revision'"
        ).bindparams(revision=revision)
    )
    op.execute(
        sa.text(
            "UPDATE schema_metadata SET value = :fingerprint, updated_at = now() "
            "WHERE key = 'platform_catalog_fingerprint'"
        ).bindparams(fingerprint=fingerprint)
    )
