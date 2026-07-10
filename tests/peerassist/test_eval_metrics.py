from __future__ import annotations

from peerassist.eval_metrics import (
    evidence_binding_rate,
    precision_recall,
    stratified_mean,
    unevidenced_new_fact_rate,
)


def test_precision_recall_handles_zero_denominators() -> None:
    assert precision_recall(tp=8, fp=2, fn=4) == {"precision": 0.8, "recall": 8 / 12}
    assert precision_recall(tp=0, fp=0, fn=0) == {"precision": 0.0, "recall": 0.0}


def test_evidence_binding_rate_counts_concerns_with_evidence() -> None:
    concerns = [
        {"id": "c1", "evidence_ids": ["P01-L001"]},
        {"id": "c2", "evidence_ids": []},
        {"id": "c3", "evidence_ids": ["T001-R001-C001"]},
    ]

    assert evidence_binding_rate(concerns) == 2 / 3


def test_unevidenced_new_fact_rate_ignores_pending_manual_checks() -> None:
    concerns = [
        {"id": "c1", "evidence_ids": [], "status": "pending_human_confirmation"},
        {"id": "c2", "evidence_ids": [], "status": "confirmed"},
        {"id": "c3", "evidence_ids": ["P01-L001"], "status": "rewritten"},
    ]

    assert unevidenced_new_fact_rate(concerns) == 1 / 2


def test_stratified_mean_groups_by_domain_and_pdf_type() -> None:
    rows = [
        {"domain": "cs", "pdf_type": "text", "latency": 4.0},
        {"domain": "cs", "pdf_type": "text", "latency": 6.0},
        {"domain": "bio", "pdf_type": "scan", "latency": 10.0},
    ]

    result = stratified_mean(rows, metric="latency", by=("domain", "pdf_type"))

    assert result[("cs", "text")] == 5.0
    assert result[("bio", "scan")] == 10.0
