from __future__ import annotations

from peerassist.evidence_audit import audit_concern_evidence


def test_audit_concern_evidence_checks_active_evidence_ids_against_ledger() -> None:
    rows = audit_concern_evidence(
        concerns=[
            {
                "id": "concern-good",
                "status": "confirmed",
                "evidence_ids": ["P01-L001"],
            },
            {
                "id": "concern-bad",
                "status": "confirmed",
                "evidence_ids": ["P99-L999"],
            },
            {
                "id": "concern-pending",
                "status": "pending_human_confirmation",
                "evidence_ids": ["P99-L999"],
            },
            {
                "id": "concern-no-evidence",
                "status": "confirmed",
                "evidence_ids": [],
            },
        ],
        ledger_items=[{"id": "P01-L001"}],
    )

    assert rows == [
        {
            "concern_id": "concern-good",
            "faithful": True,
            "evidence_ids": ["P01-L001"],
            "missing_evidence_ids": [],
            "unsupported_numeric_facts": [],
            "audit": "evidence_ids_and_numeric_facts",
        },
        {
            "concern_id": "concern-bad",
            "faithful": False,
            "evidence_ids": ["P99-L999"],
            "missing_evidence_ids": ["P99-L999"],
            "unsupported_numeric_facts": [],
            "audit": "evidence_ids_and_numeric_facts",
        },
    ]


def test_audit_concern_evidence_flags_numeric_facts_not_present_in_bound_evidence() -> None:
    rows = audit_concern_evidence(
        concerns=[
            {
                "id": "concern-supported-number",
                "status": "confirmed",
                "evidence_ids": ["P01-L001"],
                "title": "Reported percentage 40% needs clarification",
                "impact": "The manuscript says 30/100 (40%).",
            },
            {
                "id": "concern-unsupported-number",
                "status": "confirmed",
                "evidence_ids": ["P01-L001"],
                "title": "Reported percentage 90% needs clarification",
                "impact": "This concern introduces a 90% value.",
            },
        ],
        ledger_items=[
            {
                "id": "P01-L001",
                "text": "The success rate was 30/100 (40%).",
            }
        ],
    )

    by_id = {row["concern_id"]: row for row in rows}
    assert by_id["concern-supported-number"]["faithful"] is True
    assert by_id["concern-supported-number"]["unsupported_numeric_facts"] == []
    assert by_id["concern-unsupported-number"]["faithful"] is False
    assert by_id["concern-unsupported-number"]["unsupported_numeric_facts"] == ["90%"]
