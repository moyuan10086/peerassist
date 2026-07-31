"""Immutable PostgreSQL 16 catalog signature for schema revision 0003."""

from __future__ import annotations

from types import MappingProxyType

from .postgres_v0002_signature import (
    CATALOG_INSPECTION_SQL,
    catalog_fingerprint,
    catalog_signature_from_row,
)
from .postgres_v0002_signature import (
    EXPECTED_CATALOG_SIGNATURE as V0002_SIGNATURE,
)

__all__ = [
    "CATALOG_INSPECTION_SQL",
    "EXPECTED_CATALOG_FINGERPRINT",
    "EXPECTED_CATALOG_SIGNATURE",
    "OWNED_TABLES",
    "catalog_fingerprint",
    "catalog_signature_from_row",
]


def _upgrade_review_job_constraint(entry: str) -> str:
    if "|ck_review_jobs_stage|" in entry:
        return entry.replace(
            "'finalize'::character varying, 'completed'::character varying",
            "'finalize'::character varying, 'export'::character varying, 'completed'::character varying",
        )
    if "|ck_review_jobs_status|" in entry:
        return entry.replace(
            "'blocked'::character varying, 'cancelling'::character varying",
            "'blocked'::character varying, 'exporting_report'::character varying, "
            "'cancelling'::character varying",
        )
    return entry


EXPECTED_CATALOG_SIGNATURE = MappingProxyType(
    {
        key: (
            tuple(_upgrade_review_job_constraint(entry) for entry in entries)
            if key == "constraints"
            else entries
        )
        for key, entries in V0002_SIGNATURE.items()
    }
)
OWNED_TABLES = tuple(entry.split("|", 1)[0] for entry in EXPECTED_CATALOG_SIGNATURE["tables"])
EXPECTED_CATALOG_FINGERPRINT = catalog_fingerprint(EXPECTED_CATALOG_SIGNATURE)
