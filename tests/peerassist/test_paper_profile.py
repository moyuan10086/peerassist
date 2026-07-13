from __future__ import annotations

import json
from pathlib import Path

from peerassist.paper_profile import build_paper_understanding
from schemas.peerassist import EvidenceItem, EvidenceLedger, EvidenceType, ProvenanceKind


def _item(
    item_id: str,
    text: str,
    *,
    section: str,
    item_type: EvidenceType = EvidenceType.TEXT_SPAN,
) -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        type=item_type,
        page=1,
        section=section,
        locator=f"page 1, {item_id}",
        text=text,
    )


def _complete_ledger() -> EvidenceLedger:
    return EvidenceLedger(
        paper_id="paper-1",
        source_sha256="a" * 64,
        items=[
            _item("title", "Evidence-Grounded Review Assistants", section="Title"),
            _item(
                "abstract",
                "We study whether evidence-grounded agents improve peer review. "
                "We propose PeerAssist and show that it reduces evidence-location time.",
                section="Abstract",
            ),
            _item(
                "intro",
                "Our contributions are a claim graph, deterministic checks, and human confirmation.",
                section="Introduction",
            ),
            _item(
                "method",
                "The method takes parsed PDF spans as input and outputs evidence-linked concerns. "
                "It assumes stable page and bounding-box locators.",
                section="Method",
            ),
            _item(
                "dataset",
                "We evaluate on the PeerAssist-Eval-v1 dataset with n=200 papers and "
                "a 60/20/20 train validation test split.",
                section="Experiments",
            ),
            _item(
                "baseline",
                "Baselines include manual review and a generic LLM reviewer.",
                section="Experiments",
            ),
            _item(
                "metric",
                "Metrics are precision, recall, evidence faithfulness, and review time.",
                section="Experiments",
            ),
            _item(
                "seed",
                "All experiments use random seed 42 and report 95% confidence intervals.",
                section="Experiments",
            ),
            _item(
                "ablation",
                "An ablation removes the claim graph and reduces core-concern recall.",
                section="Results",
            ),
            _item(
                "result",
                "PeerAssist reduces mechanical checking time by 42% while preserving core recall.",
                section="Results",
            ),
            _item(
                "figure",
                "Figure 2: Reviewer time by workflow stage.",
                section="Results",
                item_type=EvidenceType.FIGURE_CAPTION,
            ),
            _item(
                "table",
                "Table 1: Precision and recall on PeerAssist-Eval-v1.",
                section="Results",
                item_type=EvidenceType.TABLE,
            ),
            _item(
                "conclusion",
                "We conclude that evidence-grounded assistance reduces review time, "
                "but the study is limited to computer-science manuscripts.",
                section="Conclusion",
            ),
        ],
        metadata={"warnings": ["table_structure_partial"]},
    )


def test_builds_grounded_profile_claims_experiments_and_review_plan(tmp_path: Path) -> None:
    artifacts = build_paper_understanding(_complete_ledger(), tmp_path)

    assert artifacts.profile.title.value == "Evidence-Grounded Review Assistants"
    assert artifacts.profile.title.evidence_ids == ["title"]
    assert "evidence-grounded agents" in artifacts.profile.research_question.value.lower()
    assert artifacts.profile.contributions.value
    assert artifacts.profile.method_inputs.evidence_ids == ["method"]
    assert artifacts.profile.method_outputs.evidence_ids == ["method"]
    assert artifacts.profile.method_assumptions.evidence_ids == ["method"]
    assert artifacts.profile.conclusion_boundaries.evidence_ids == ["conclusion"]
    assert "table_structure_partial" in artifacts.profile.parse_warnings.value

    claims = artifacts.claim_graph.claims
    assert claims
    assert claims == sorted(claims, key=lambda claim: (-claim.centrality, claim.claim_id))
    assert all(claim.claim_id.startswith("claim-") for claim in claims)
    assert all(claim.evidence_ids for claim in claims)
    assert any(edge.relation == "supported_by" for edge in artifacts.claim_graph.edges)
    assert all(
        edge.source_evidence_id
        not in next(
            claim.evidence_ids
            for claim in claims
            if claim.claim_id == edge.target_claim_id
        )
        for edge in artifacts.claim_graph.edges
    )
    assert artifacts.review_plan.core_claim_ids[0] == claims[0].claim_id
    assert artifacts.review_plan.reading_route[0].priority == "core"

    experiment = artifacts.experiment_inventory.experiments[0]
    assert "PeerAssist-Eval-v1" in " ".join(experiment.datasets.value)
    assert experiment.sample_sizes.evidence_ids == ["dataset"]
    assert experiment.data_splits.evidence_ids == ["dataset"]
    assert experiment.baselines.evidence_ids == ["baseline"]
    assert experiment.metrics.evidence_ids == ["metric"]
    assert experiment.random_seeds.evidence_ids == ["seed"]
    assert experiment.statistics.evidence_ids == ["seed"]
    assert experiment.ablations.evidence_ids == ["ablation"]
    assert experiment.key_figures.evidence_ids == ["figure"]
    assert experiment.key_tables.evidence_ids == ["table"]
    assert experiment.datasets.provenance is ProvenanceKind.REPORTED

    for path in artifacts.artifact_paths.values():
        assert Path(path).is_file()
        json.loads(Path(path).read_text(encoding="utf-8"))


def test_profile_fields_are_evidence_bound_or_marked_for_human_review(tmp_path: Path) -> None:
    artifacts = build_paper_understanding(_complete_ledger(), tmp_path)

    for field in artifacts.profile.grounded_fields():
        assert field.evidence_ids or field.needs_human_review
    for experiment in artifacts.experiment_inventory.experiments:
        for field in experiment.grounded_fields():
            assert field.evidence_ids or field.needs_human_review


def test_missing_sections_do_not_invent_profile_or_experiment_content(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-empty",
        source_sha256="b" * 64,
        items=[_item("title", "A Short Note", section="Title")],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    assert artifacts.profile.title.value == "A Short Note"
    assert artifacts.profile.abstract.value is None
    assert artifacts.profile.abstract.needs_human_review is True
    assert artifacts.profile.research_question.value is None
    assert artifacts.profile.contributions.value == []
    assert artifacts.claim_graph.claims == []
    assert artifacts.experiment_inventory.experiments == []
    assert artifacts.review_plan.core_claim_ids == []


def test_claim_ids_and_core_ranking_are_stable(tmp_path: Path) -> None:
    first = build_paper_understanding(_complete_ledger(), tmp_path / "first")
    second = build_paper_understanding(_complete_ledger(), tmp_path / "second")

    assert [claim.claim_id for claim in first.claim_graph.claims] == [
        claim.claim_id for claim in second.claim_graph.claims
    ]
    assert first.review_plan.core_claim_ids == second.review_plan.core_claim_ids


def test_claim_support_detects_conflict_without_self_support(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-conflict",
        source_sha256="c" * 64,
        items=[
            _item(
                "claim",
                "We show accuracy improves on Benchmark X.",
                section="Abstract",
            ),
            _item(
                "conflict",
                "Accuracy does not improve on Benchmark X in the held-out results.",
                section="Results",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    claim = next(
        candidate
        for candidate in artifacts.claim_graph.claims
        if "claim" in candidate.evidence_ids
    )
    assert claim.support_status.value == "conflicting"
    assert claim.support_evidence_ids == ["conflict"]
    assert claim.conclusion_boundaries.needs_human_review is True
    assert artifacts.claim_graph.edges[0].source_evidence_id == "conflict"


def test_multiple_experiments_inferred_provenance_and_minor_collapse(tmp_path: Path) -> None:
    minor = EvidenceItem(
        id="minor-writing",
        type=EvidenceType.TEXT_SPAN,
        page=3,
        section="Writing Style",
        locator="page 3",
        text="A sentence could be shorter.",
        metadata={"importance": "minor"},
    )
    ledger = EvidenceLedger(
        paper_id="paper-multi",
        source_sha256="d" * 64,
        items=[
            _item("claim", "We show the system improves accuracy.", section="Abstract"),
            _item("data", "Dataset A contains n=100 samples.", section="Dataset A"),
            _item(
                "exp-a",
                "Experiment A uses Dataset A and metric F1 with baseline Alpha.",
                section="Experiment A",
            ),
            _item(
                "exp-b",
                "Experiment B compares the second configuration with baseline Beta.",
                section="Experiment B",
            ),
            _item(
                "fig-a",
                "Figure A: F1 for Experiment A.",
                section="Experiment A",
                item_type=EvidenceType.FIGURE_CAPTION,
            ),
            _item(
                "table-b",
                "Table B: Accuracy on Benchmark-B for Experiment B.",
                section="Experiment B",
                item_type=EvidenceType.TABLE,
            ),
            minor,
        ],
        metadata={"warnings": ["page_2_bbox_uncertain"]},
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    assert len(artifacts.experiment_inventory.experiments) == 2
    by_label = {
        experiment.label.value: experiment
        for experiment in artifacts.experiment_inventory.experiments
    }
    first = by_label["experiment a"]
    second = by_label["experiment b"]
    assert first.datasets.provenance is ProvenanceKind.REPORTED
    assert first.key_figures.evidence_ids == ["fig-a"]
    assert second.datasets.provenance is ProvenanceKind.INFERRED
    assert second.metrics.provenance is ProvenanceKind.INFERRED
    assert second.key_tables.evidence_ids == ["table-b"]
    assert "fig-a" not in second.key_figures.evidence_ids
    assert "minor-writing" in artifacts.review_plan.collapsed_evidence_ids
    assert any("parser uncertainty" in item.reason for item in artifacts.review_plan.reading_route)
    assert all("minor-writing" not in item.evidence_ids for item in artifacts.review_plan.reading_route)
