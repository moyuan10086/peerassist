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
    page: int = 1,
    importance: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        type=item_type,
        page=page,
        section=section,
        locator=f"page {page}, {item_id}",
        text=text,
        metadata={"importance": importance} if importance else {},
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
    assert artifacts.review_plan.schema_version == "peerassist.review_plan.v2"

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


def test_profile_reconstructs_local_pdf_blocks_without_losing_line_evidence(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-local-lines",
        source_sha256="1" * 64,
        items=[
            EvidenceItem(
                id="P0001-B0001-L001",
                type=EvidenceType.TEXT_SPAN,
                page=1,
                section="Title",
                locator="page 1, block 1, line 1",
                text="Stop Guessing When to Stop Testing:",
                metadata={"block_id": "P0001-B0001", "line": 1},
            ),
            EvidenceItem(
                id="P0001-B0001-L002",
                type=EvidenceType.TEXT_SPAN,
                page=1,
                section="Title",
                locator="page 1, block 1, line 2",
                text="Efficient Model Evaluation with Just Enough Data",
                metadata={"block_id": "P0001-B0001", "line": 2},
            ),
            EvidenceItem(
                id="P0001-B0002-L001",
                type=EvidenceType.TEXT_SPAN,
                page=1,
                section="Abstract",
                locator="page 1, block 2, line 1",
                text="We propose an adaptive evaluation frame-",
                metadata={"block_id": "P0001-B0002", "line": 1},
            ),
            EvidenceItem(
                id="P0001-B0002-L002",
                type=EvidenceType.TEXT_SPAN,
                page=1,
                section="Abstract",
                locator="page 1, block 2, line 2",
                text="work that reduces evaluation cost by 80%.",
                metadata={"block_id": "P0001-B0002", "line": 2},
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    assert artifacts.profile.title.value == (
        "Stop Guessing When to Stop Testing: Efficient Model Evaluation with Just Enough Data"
    )
    claim = artifacts.claim_graph.claims[0]
    assert claim.text == "We propose an adaptive evaluation framework that reduces evaluation cost by 80%."
    assert claim.evidence_ids == ["P0001-B0002-L001", "P0001-B0002-L002"]


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
    assert artifacts.claim_graph.edges[0].relation == "conflicts_with"


def test_decrease_is_not_unconditionally_treated_as_conflict(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-decrease",
        source_sha256="e" * 64,
        items=[
            _item("claim", "We show validation loss decreases after training.", section="Abstract"),
            _item(
                "result",
                "Validation loss decreases after training in the held-out results.",
                section="Results",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    claim = next(candidate for candidate in artifacts.claim_graph.claims if "claim" in candidate.evidence_ids)
    assert claim.support_status.value != "conflicting"
    assert artifacts.claim_graph.edges[0].relation == "supported_by"


def test_worse_than_reverse_comparison_can_support_outperformance(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-comparison-direction",
        source_sha256="f" * 64,
        items=[
            _item("claim", "Our method outperforms baseline X.", section="Abstract"),
            _item(
                "result",
                "Baseline X is worse than our method in the held-out results.",
                section="Results",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    claim = next(candidate for candidate in artifacts.claim_graph.claims if "claim" in candidate.evidence_ids)
    assert claim.support_status.value != "conflicting"
    assert artifacts.claim_graph.edges[0].relation == "supported_by"


def test_worse_than_same_direction_conflicts_with_outperformance(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-comparison-conflict",
        source_sha256="0" * 64,
        items=[
            _item("claim", "Our method outperforms baseline X.", section="Abstract"),
            _item(
                "result",
                "Our method is worse than baseline X in the held-out results.",
                section="Results",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    claim = next(candidate for candidate in artifacts.claim_graph.claims if "claim" in candidate.evidence_ids)
    assert claim.support_status.value == "conflicting"
    assert artifacts.claim_graph.edges[0].relation == "conflicts_with"


def test_reverse_negated_outperformance_is_not_a_conflict(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-negated-comparison",
        source_sha256="1" * 64,
        items=[
            _item("claim", "Our method outperforms baseline X.", section="Abstract"),
            _item(
                "result",
                "Baseline X does not outperform our method.",
                section="Results",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    claim = next(candidate for candidate in artifacts.claim_graph.claims if "claim" in candidate.evidence_ids)
    assert claim.support_status.value != "conflicting"
    assert artifacts.claim_graph.edges[0].relation == "supported_by"


def test_negated_claim_conflicts_with_positive_comparison(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-negated-claim",
        source_sha256="4" * 64,
        items=[
            _item(
                "claim",
                "Our method does not outperform baseline X.",
                section="Abstract",
            ),
            _item(
                "result",
                "Our method outperforms baseline X.",
                section="Results",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    claim = next(candidate for candidate in artifacts.claim_graph.claims if "claim" in candidate.evidence_ids)
    assert claim.support_status.value == "conflicting"
    assert artifacts.claim_graph.edges[0].relation == "conflicts_with"


def test_not_better_comparison_conflicts_with_outperformance(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-not-better",
        source_sha256="5" * 64,
        items=[
            _item("claim", "Our method outperforms baseline X.", section="Abstract"),
            _item(
                "result",
                "Our method is not better than baseline X.",
                section="Results",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    claim = next(candidate for candidate in artifacts.claim_graph.claims if "claim" in candidate.evidence_ids)
    assert claim.support_status.value == "conflicting"


def test_reverse_outperformance_with_scope_is_a_conflict(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-scoped-comparison",
        source_sha256="2" * 64,
        items=[
            _item("claim", "Our method outperforms baseline X.", section="Abstract"),
            _item(
                "result",
                "Baseline X outperforms our method on Benchmark Y.",
                section="Results",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    claim = next(candidate for candidate in artifacts.claim_graph.claims if "claim" in candidate.evidence_ids)
    assert claim.support_status.value == "conflicting"
    assert artifacts.claim_graph.edges[0].relation == "conflicts_with"


def test_comparison_scope_is_trimmed_for_named_dataset_and_table(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-generic-scope",
        source_sha256="6" * 64,
        items=[
            _item("claim", "Our method outperforms baseline X.", section="Abstract"),
            _item(
                "cifar-result",
                "Baseline X outperforms our method on CIFAR-10.",
                section="Results",
            ),
            _item(
                "table-result",
                "Our method is worse than baseline X in Table 2.",
                section="Results",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)

    claim = next(candidate for candidate in artifacts.claim_graph.claims if "claim" in candidate.evidence_ids)
    assert claim.support_status.value == "conflicting"
    claim_relations = {
        edge.relation
        for edge in artifacts.claim_graph.edges
        if edge.target_claim_id == claim.claim_id
    }
    assert claim_relations == {"conflicts_with"}


def test_multiword_dataset_names_with_shared_prefix_do_not_cross_link(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-multiword-datasets",
        source_sha256="3" * 64,
        items=[
            _item(
                "exp-train",
                "Experiment A evaluates Dataset Alpha Train with metric F1.",
                section="Experiment A",
            ),
            _item(
                "exp-test",
                "Experiment B evaluates Dataset Alpha Test with metric accuracy.",
                section="Experiment B",
            ),
            _item(
                "data-train",
                "Dataset Alpha Train contains n=100 samples.",
                section="Dataset Alpha Train",
            ),
            _item(
                "data-test",
                "Dataset Alpha Test contains n=80 samples.",
                section="Dataset Alpha Test",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)
    by_label = {
        experiment.label.value: experiment
        for experiment in artifacts.experiment_inventory.experiments
    }

    assert "data-train" in by_label["experiment a"].datasets.evidence_ids
    assert "data-test" not in by_label["experiment a"].datasets.evidence_ids
    assert "data-test" in by_label["experiment b"].datasets.evidence_ids
    assert "data-train" not in by_label["experiment b"].datasets.evidence_ids


def test_dataset_names_with_connectors_do_not_cross_link(tmp_path: Path) -> None:
    ledger = EvidenceLedger(
        paper_id="paper-connector-datasets",
        source_sha256="7" * 64,
        items=[
            _item(
                "exp-train",
                "Experiment A evaluates Dataset Alpha for Training.",
                section="Experiment A",
            ),
            _item(
                "exp-test",
                "Experiment B evaluates Dataset Alpha for Testing.",
                section="Experiment B",
            ),
            _item(
                "data-train",
                "Dataset Alpha for Training contains n=100 samples.",
                section="Dataset Alpha for Training",
            ),
            _item(
                "data-test",
                "Dataset Alpha for Testing contains n=80 samples.",
                section="Dataset Alpha for Testing",
            ),
        ],
    )

    artifacts = build_paper_understanding(ledger, tmp_path)
    by_label = {
        experiment.label.value: experiment
        for experiment in artifacts.experiment_inventory.experiments
    }

    assert "data-train" in by_label["experiment a"].datasets.evidence_ids
    assert "data-test" not in by_label["experiment a"].datasets.evidence_ids
    assert "data-test" in by_label["experiment b"].datasets.evidence_ids
    assert "data-train" not in by_label["experiment b"].datasets.evidence_ids


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
                "Experiment B compares Dataset B with baseline Beta.",
                section="Experiment B",
            ),
            _item("data-b", "Dataset B contains n=80 samples.", section="Dataset B"),
            _item(
                "fig-a",
                "Figure A: F1 for Experiment A.",
                section="Experiment A Results",
                item_type=EvidenceType.FIGURE_CAPTION,
            ),
            _item(
                "table-b",
                "Table B: Accuracy on Benchmark-B for Experiment B.",
                section="Experiment B",
                item_type=EvidenceType.TABLE,
            ),
            _item(
                "minor-claim",
                "We show a minor wording improvement.",
                section="Abstract",
                importance="minor",
            ),
            _item(
                "core-writing",
                "The writing section documents a core protocol constraint.",
                section="Writing Style",
                importance="core",
            ),
            minor,
        ],
        metadata={"warnings": ["page_1_bbox_uncertain"]},
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
    assert "data" in first.datasets.evidence_ids
    assert "data-b" not in first.datasets.evidence_ids
    assert first.key_figures.evidence_ids == ["fig-a"]
    assert second.datasets.provenance is ProvenanceKind.REPORTED
    assert "data-b" in second.datasets.evidence_ids
    assert "data" not in second.datasets.evidence_ids
    assert second.metrics.provenance is ProvenanceKind.INFERRED
    assert second.key_tables.evidence_ids == ["table-b"]
    assert "fig-a" not in second.key_figures.evidence_ids
    assert "minor-writing" in artifacts.review_plan.collapsed_evidence_ids
    assert "minor-claim" in artifacts.review_plan.collapsed_evidence_ids
    assert "core-writing" not in artifacts.review_plan.collapsed_evidence_ids
    warning_route = next(
        item for item in artifacts.review_plan.reading_route if "parser uncertainty" in item.reason
    )
    assert warning_route.warning_codes == ["page_1_bbox_uncertain"]
    assert warning_route.needs_human_review is True
    assert warning_route.evidence_ids
    priorities = [item.priority for item in artifacts.review_plan.reading_route]
    assert priorities == sorted(priorities, key=lambda value: value != "core")
    assert all("minor-writing" not in item.evidence_ids for item in artifacts.review_plan.reading_route)
    assert all("minor-claim" not in item.evidence_ids for item in artifacts.review_plan.reading_route)
    assert all(
        "minor-claim" not in assignment.evidence_ids
        for assignment in artifacts.review_plan.agent_assignments
    )
