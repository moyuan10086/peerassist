"""Report stage tests.

Pins down the post-hoc claim audit semantics that drive the final review's
verdict cells: status capping (always one-way toward more conservative),
Pending-promotion to the LLM verdict, agent self-tag reconciliation, and
the structural axis-self-selection / ablation-coverage weakness bullets.

The audit's LLM call is mandatory in production but injectable for tests
via the ``llm_call`` parameter. Each test wires a deterministic stub.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent_runtime.runner import augment_claims_with_assessment_status, augment_experiment_with_eval_status
from common.pipeline_context import execution_stage_dir, init_full_pipeline_context, save_bridge_state
from review.report.claim_audit import audit_review_markdown
from review.report.final_report import validate_final_report_logic
from review.report.stage_runner import _upsert_execution_reproduction_block, run_report_stage


def _stub_llm(verdicts: list[dict[str, Any]], missing: list[str] | None = None):
    """Build a deterministic LLM stub for ``audit_review_markdown``."""

    def _call(prompt: str) -> dict[str, Any]:
        return {"verdicts": verdicts, "ablation_missing_components": missing or []}

    return _call


def test_final_report_logic_allows_variable_number_of_claims() -> None:
    md = (
        "## 2. Technical Positioning\n"
        "| Research domain | Method | A | B |\n"
        "| --- | --- | --- | --- |\n"
        "| Other | Baseline | × | √ |\n"
        "| This Work | TinyMethod | √ | √ |\n\n"
        "## 3. Claims\n"
        "| Claim | Evidence | Assessment | Location |\n"
        "|---|---|---|---|\n"
        "| Novelty claim. | Supporting: Related Work comparison.<br><br>Missing: external verification. | partial | Intro |\n"
        "| Method claim. | Supporting: Section 3.<br><br>Missing: None. | ok | Section 3 |\n"
        "| Artifact claim. | Supporting: Appendix A.<br><br>Missing: release URL. | partial | Appendix A |\n"
        "| Result claim. | Supporting: Table 1.<br><br>Missing: None. | ok | Table 1 |\n\n"
        "## 5. Experiment\n"
        "### Main Result\n"
        "| Task | Metric | Paper Result |\n"
        "|---|---|---|\n"
        "| T | M | 1.0 |\n\n"
        "### Ablation Result\n"
        "| Ablation Dimension | Configuration | Full Model | Paper Result | Difference |\n"
        "|---|---|---|---|---|\n"
        "| Optimal setup | full | 1.0 | 1.0 | 0 |\n"
    )

    assert validate_final_report_logic(md) is None


def test_audit_caps_supported_to_inconclusive_when_llm_disagrees(
    review_md_with_claims_table,
) -> None:
    new_md, outcome = audit_review_markdown(
        review_md_with_claims_table,
        llm_call=_stub_llm([{"id": 0, "verdict": "inconclusive", "reason": "gap within 1 sigma"}]),
    )

    result = outcome.claim_results[0]
    assert result.original_status == "supported"
    assert result.final_status == "inconclusive"
    # Markdown reflects the cap and an audit weakness bullet got injected.
    assert "⚠ Inconclusive" in new_md
    assert "[audit] Status downgraded to Inconclusive" in new_md


def test_audit_promotes_pending_to_llm_verdict() -> None:
    md = (
        "## 3. Claims\n"
        "| Claim | Evidence | Assessment | Status | Location |\n"
        "|---|---|---|---|---|\n"
        "| Method M describes a new attention block. | Section 3.1 introduces M. | "
        "ok | Pending | Section 3.1 |\n"
        "## 4. Summary\n"
        "**Weaknesses:**\n- w\n\n"
    )
    new_md, outcome = audit_review_markdown(
        md, llm_call=_stub_llm([{"id": 0, "verdict": "supported", "reason": "anchored"}])
    )

    result = outcome.claim_results[0]
    assert result.original_status == ""  # Pending normalises to ""
    assert result.final_status == "supported"
    assert "✓ Supported" in new_md
    # Promotions out of Pending must NOT emit a "downgraded" weakness bullet.
    assert not any("Status downgraded" in b for b in outcome.extra_weaknesses)


def test_claim_status_augmentation_preserves_pipe_inside_evidence_cell() -> None:
    md = (
        "## **3. Claims**\n"
        "| Claim | Evidence | Assessment | Location |\n"
        "|---|---|---|---|\n"
        "| COMPGCN scales with relations. | Table 1 reports O(Kd^2 + Bd + B | R | d). | "
        "Scope is limited. | Table 1 |\n"
        "## 4. Summary\n"
        "**Weaknesses:**\n- w\n\n"
    )

    augmented = augment_claims_with_assessment_status(md, summary={}, alignment={})
    row = next(line for line in augmented.splitlines() if line.startswith("| COMPGCN"))
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    assert len(cells) == 5
    assert "B &#124; R &#124; d" in cells[1]
    assert cells[3] == "Pending"

    audited, outcome = audit_review_markdown(
        augmented,
        llm_call=_stub_llm([{"id": 0, "verdict": "partially_supported", "reason": "limited"}]),
    )

    audited_row = next(line for line in audited.splitlines() if line.startswith("| COMPGCN"))
    audited_cells = [cell.strip() for cell in audited_row.strip("|").split("|")]
    assert len(audited_cells) == 5
    assert "B &#124; R &#124; d" in audited_cells[1]
    assert "Partially supported" in audited_cells[3]
    assert outcome.claim_results[0].final_status == "partially supported"


def test_audit_applies_agent_self_tag_as_conservative_cap() -> None:
    md = (
        "## 3. Claims\n"
        "| Claim | Evidence | Assessment | Status | Location |\n"
        "|---|---|---|---|---|\n"
        "| Trivial. | Evidence. | "
        "ok [verdict: in_conflict; reason: paper value below comparator] | "
        '<span style="color: green;">✓ Supported</span> | Loc |\n'
        "## 4. Summary\n"
    )
    new_md, outcome = audit_review_markdown(
        md, llm_call=_stub_llm([{"id": 0, "verdict": "partially_supported", "reason": "weak"}])
    )

    result = outcome.claim_results[0]
    # The LLM audits independently, then the agent self-tag is applied as a
    # conservative cap so the final status keeps the more critical verdict.
    assert result.agent_self_verdict == "in conflict"
    assert result.llm_verdict == "partially supported"
    assert result.final_status == "in conflict"
    # The bracketed self-tag is stripped from the visible cell.
    assert "[verdict:" not in new_md
    assert "In conflict" in new_md


def test_audit_injects_missing_component_weakness_from_llm() -> None:
    md = (
        "## 3. Claims\n"
        "| Claim | Evidence | Assessment | Status | Location |\n"
        "|---|---|---|---|---|\n"
        "| Method M with A, B, and C. | Sec 2. | ok | Pending | Sec 2 |\n"
        "## 4. Summary\n"
        "**Weaknesses:**\n- w\n\n"
        "## 5. Experiment\n"
        "### Ablation Result\n"
        "| Dim | Cfg | Full | Paper | Δ |\n"
        "|---|---|---|---|---|\n"
        "| A | no | 1.0 | 0.5 | -0.5 |\n"
    )
    new_md, outcome = audit_review_markdown(
        md,
        llm_call=_stub_llm(
            [{"id": 0, "verdict": "partially_supported", "reason": "B and C not ablated"}],
            missing=["B", "C"],
        ),
    )

    assert outcome.ablation_components_missing == ["B", "C"]
    assert any("B" in b and "C" in b and "ablation" in b.lower() for b in outcome.extra_weaknesses)
    assert "[audit]" in new_md.split("## 5.")[0]


def test_audit_skips_llm_when_no_claims_table_but_runs_axis_audit() -> None:
    md = (
        "## 2. Technical Positioning\n"
        "| Research domain | Method | A | B | C |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| Other | X | × | × | × |\n"
        "| Other | Y | × | × | × |\n"
        "| Other | Z | × | × | × |\n"
        "| This Work | Ours | √ | √ | √ |\n"
        "## 4. Summary\n"
        "**Weaknesses:**\n- w\n\n"
    )

    def must_not_call(prompt: str) -> dict[str, Any]:
        raise AssertionError("LLM should not be called when there are no claims")

    new_md, outcome = audit_review_markdown(md, llm_call=must_not_call)
    # Axis audit is structural and runs regardless of whether claims exist.
    assert outcome.claim_results == []
    assert outcome.axis_self_selection_ratio is not None
    assert any("favor the proposed system" in b for b in outcome.extra_weaknesses)
    assert "[audit]" in new_md


def test_audit_propagates_llm_failure_without_silent_fallback() -> None:
    md = (
        "## 3. Claims\n"
        "| Claim | Evidence | Assessment | Status | Location |\n"
        "|---|---|---|---|---|\n"
        "| C. | E. | ok | Pending | L |\n"
        "## 4. Summary\n"
    )

    def boom(prompt: str) -> dict[str, Any]:
        raise RuntimeError("LLM unreachable")

    # Mandatory-LLM contract: a transport failure must surface, not get
    # swallowed into a default verdict.
    with pytest.raises(RuntimeError, match="LLM unreachable"):
        audit_review_markdown(md, llm_call=boom)


def test_execution_alignment_updates_claim_and_experiment_status() -> None:
    alignment = {
        "comparisons": [
            {
                "paper_key": "FB15k-237 / TinyMethod / mrr",
                "observed_key": "metrics/eval_metrics.json / mrr",
                "dataset": "FB15k-237",
                "metric": "mrr",
                "paper_value": 0.355,
                "observed_value": 0.200,
                "delta": -0.155,
                "passed": False,
                "within_tolerance": False,
            }
        ]
    }
    md = (
        "## **3. Claims**\n"
        "| Claim | Evidence | Assessment | Location |\n"
        "|---|---|---|---|\n"
        "| TinyMethod reaches MRR 0.355 on FB15k-237. | Table 1 reports MRR 0.355 on FB15k-237. | ok | Table 1 |\n\n"
        "## **5. Experiment**\n"
        "### Main Result\n"
        "| **Task** | **Dataset** | **Metric** | **Best Baseline** | **Paper Result** | **Difference (Delta)** |\n"
        "|---|---|---|---|---|---|\n"
        "| Link prediction | FB15k-237 | MRR | 0.330 | 0.355 | +0.025 |\n"
    )

    augmented = augment_claims_with_assessment_status(
        md,
        summary={"status": "success"},
        alignment=alignment,
    )
    augmented = augment_experiment_with_eval_status(
        augmented,
        summary={"status": "success"},
        alignment=alignment,
    )

    claim_row = next(line for line in augmented.splitlines() if line.startswith("| TinyMethod reaches"))
    assert "Paper=0.355" in claim_row
    assert "Reproduced=0.2" in claim_row
    assert "In conflict" in claim_row
    assert "Paper=15" not in claim_row
    assert "In conflict" in augmented
    assert "Reproduced=0.2" in augmented
    assert "Evaluation Status" in augmented
    assert "In conflict (0.2)" in augmented


def test_experiment_status_uses_alignment_tolerance_not_global_threshold() -> None:
    alignment = {
        "comparisons": [
            {
                "paper_key": "FB15k-237 / CompGCN Corr / ConvE / hits@10",
                "observed_key": "metrics/fb237_conve_test.json / hits@10",
                "dataset": "FB15k-237",
                "metric": "hits@10",
                "paper_value": 0.535,
                "observed_value": 0.500,
                "delta": -0.035,
                "tolerance": 0.02,
                "passed": False,
                "within_tolerance": False,
            }
        ]
    }
    md = (
        "## **5. Experiment**\n"
        "### Main Result\n"
        "| **Task** | **Dataset** | **Metric** | **Best Baseline** | **Paper Result** | **Difference (Delta)** |\n"
        "|---|---|---|---|---|---|\n"
        "| Link prediction | FB15k-237 | H@10 | 0.520 | 0.535 | +0.015 |\n"
    )

    augmented = augment_experiment_with_eval_status(
        md,
        summary={"status": "deviated"},
        alignment=alignment,
    )

    experiment_row = next(line for line in augmented.splitlines() if line.startswith("| Link prediction"))
    assert "Supported" not in experiment_row
    assert "In conflict (0.5)" in experiment_row


def test_claim_status_uses_matching_method_when_multiple_comparisons() -> None:
    alignment = {
        "comparisons": [
            {
                "paper_key": "FB15k-237 / TinyMethod / mrr",
                "observed_key": "metrics/tiny.json / mrr",
                "dataset": "FB15k-237",
                "metric": "mrr",
                "paper_row_label": "TinyMethod",
                "paper_value": 0.355,
                "observed_value": 0.200,
                "passed": False,
            },
            {
                "paper_key": "FB15k-237 / BaselineX / mrr",
                "observed_key": "metrics/baselinex.json / mrr",
                "dataset": "FB15k-237",
                "metric": "mrr",
                "paper_row_label": "BaselineX",
                "paper_value": 0.330,
                "observed_value": 0.329,
                "passed": True,
            },
        ]
    }
    md = (
        "## **3. Claims**\n"
        "| Claim | Evidence | Assessment | Location |\n"
        "|---|---|---|---|\n"
        "| BaselineX reaches MRR 0.330 on FB15k-237. | Table 1 reports BaselineX MRR 0.330. | ok | Table 1 |\n\n"
        "## **5. Experiment**\n"
        "### Main Result\n"
        "| **Task** | **Dataset** | **Metric** | **Best Baseline** | **Paper Result** | **Difference (Delta)** |\n"
        "|---|---|---|---|---|---|\n"
        "| BaselineX | FB15k-237 | MRR | 0.300 | MRR 0.330 on FB15k-237 | +0.030 |\n"
    )

    augmented = augment_claims_with_assessment_status(
        md,
        summary={"status": "success"},
        alignment=alignment,
    )
    augmented = augment_experiment_with_eval_status(
        augmented,
        summary={"status": "success"},
        alignment=alignment,
    )

    claim_row = next(line for line in augmented.splitlines() if line.startswith("| BaselineX"))
    assert "Paper=0.33" in claim_row
    assert "Reproduced=0.329" in claim_row
    assert "Supported" in claim_row
    assert "Reproduced=0.2" not in claim_row
    experiment_row = next(line for line in augmented.splitlines() if line.startswith("| BaselineX | FB15k-237"))
    assert "Supported (0.329)" in experiment_row
    assert "In conflict (0.2)" not in experiment_row


def test_report_stage_inserts_deterministic_execution_reproduction_block() -> None:
    md = (
        "## 2. Technical Positioning\n"
        "| Research domain | Method | A |\n"
        "|---|---|---|\n"
        "| This Work | TinyMethod | √ |\n\n"
        "## 3. Claims\n"
        "| Claim | Evidence | Assessment | Location |\n"
        "|---|---|---|---|\n"
        "| TinyMethod reaches MRR 0.355. | Table 1. | ok | Table 1 |\n\n"
        "## 4. Summary\n"
        "**Weaknesses:**\n- w\n\n"
        "## 5. Experiment\n"
        "### Main Result\n"
        "| Task | Dataset | Metric | Best Baseline | Paper Result | Difference (Delta) |\n"
        "|---|---|---|---|---|---|\n"
        "| Link prediction | FB15k-237 | MRR | 0.330 | 0.355 | +0.025 |\n\n"
        "### Ablation Result\n"
        "| Ablation Dimension | Configuration | Full Model | Paper Result | Difference |\n"
        "|---|---|---|---|---|\n"
        "| Optimal setup | full | 0.355 | 0.355 | 0 |\n"
    )
    alignment = {
        "comparisons": [
            {
                "dataset": "FB15k-237",
                "metric": "mrr",
                "paper_value": 0.355,
                "observed_value": 0.200,
                "delta": -0.155,
                "tolerance": 0.01,
                "passed": False,
            }
        ],
        "unmatched_run_metrics": ["metrics/extra.json"],
    }

    augmented = _upsert_execution_reproduction_block(
        md,
        exec_json={"status": "ok", "summary": {"run_result": {"success": True}}},
        execution_alignment=alignment,
    )
    augmented_twice = _upsert_execution_reproduction_block(
        augmented,
        exec_json={"status": "ok", "summary": {"run_result": {"success": True}}},
        execution_alignment=alignment,
    )

    assert augmented.count("### Execution Reproduction Check") == 1
    assert augmented_twice.count("### Execution Reproduction Check") == 1
    assert "1` metric(s) outside tolerance" in augmented
    assert "| FB15k-237 | mrr | 0.355 | 0.2 | -0.155 | 0.01 | FAIL |" in augmented
    assert "`metrics/extra.json`" in augmented
    assert augmented.index("### Execution Reproduction Check") > augmented.index("### Ablation Result")
    assert validate_final_report_logic(augmented) is None


def test_run_report_stage_writes_execution_reproduction_block_to_final_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    init_full_pipeline_context(run_dir=run_dir)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    paper_pdf = tmp_path / "paper.pdf"
    paper_pdf.write_bytes(b"%PDF-1.4\n")
    final_md = job_dir / "final_report.md"
    final_md.write_text(
        "## 2. Technical Positioning\n"
        "| Research domain | Method | A |\n"
        "|---|---|---|\n"
        "| This Work | TinyMethod | √ |\n\n"
        "## 3. Claims\n"
        "| Claim | Evidence | Assessment | Location |\n"
        "|---|---|---|---|\n"
        "| TinyMethod reaches MRR 0.355. | Table 1. | ok | Table 1 |\n\n"
        "## 4. Summary\n"
        "**Weaknesses:**\n- w\n\n"
        "## 5. Experiment\n"
        "### Main Result\n"
        "| Task | Dataset | Metric | Best Baseline | Paper Result | Difference (Delta) |\n"
        "|---|---|---|---|---|---|\n"
        "| Link prediction | FB15k-237 | MRR | 0.330 | 0.355 | +0.025 |\n\n"
        "### Ablation Result\n"
        "| Ablation Dimension | Configuration | Full Model | Paper Result | Difference |\n"
        "|---|---|---|---|---|\n"
        "| Optimal setup | full | 0.355 | 0.355 | 0 |\n",
        encoding="utf-8",
    )
    job_json = job_dir / "job.json"
    job_json.write_text(
        json.dumps(
            {
                "status": "completed",
                "message": "ok",
                "artifacts": {"final_markdown_path": str(final_md)},
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    save_bridge_state(
        run_dir=run_dir,
        paper_pdf=paper_pdf,
        paper_key="tinymethod",
        own_payload={
            "job_id": "job1",
            "job_dir": str(job_dir),
            "job_json_path": str(job_json),
            "status": "completed",
            "message": "ok",
            "artifacts": {"final_markdown_path": str(final_md)},
        },
    )
    execution_stage_dir(run_dir).mkdir(parents=True, exist_ok=True)
    (execution_stage_dir(run_dir) / "execution.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "summary": {"run_result": {"success": True}},
                "alignment": {
                    "comparisons": [
                        {
                            "dataset": "FB15k-237",
                            "metric": "mrr",
                            "paper_value": 0.355,
                            "observed_value": 0.200,
                            "delta": -0.155,
                            "tolerance": 0.01,
                            "passed": False,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "review.report.stage_runner.audit_review_markdown",
        lambda text, execution_alignment=None: (
            text,
            SimpleNamespace(
                claim_results=[],
                axis_self_selection_ratio=None,
                ablation_components_missing=[],
                extra_weaknesses=[],
                llm_raw={},
            ),
        ),
    )

    def fake_render_pdf(**kwargs: Any) -> tuple[bool, str]:
        kwargs["pdf_path"].write_bytes(b"%PDF-1.4\n")
        return True, ""

    monkeypatch.setattr("review.report.stage_runner._render_review_pdf", fake_render_pdf)

    result = run_report_stage(repo_root=tmp_path, run_dir=run_dir)

    assert result.status == "ok"
    final_review = (run_dir / "stages" / "review" / "report" / "final_review.md").read_text(
        encoding="utf-8"
    )
    assert "### Execution Reproduction Check" in final_review
    assert "[execution] Execution reproduction found 1 of 1 aligned metric(s) outside tolerance" in final_review
    assert "| FB15k-237 | mrr | 0.355 | 0.2 | -0.155 | 0.01 | FAIL |" in final_review
    final_payload = json.loads(
        (run_dir / "stages" / "review" / "report" / "final_review.json").read_text(encoding="utf-8")
    )
    assert "### Execution Reproduction Check" in final_payload["final_markdown"]
    assert final_payload["execution"]["alignment"]["comparisons"][0]["observed_value"] == pytest.approx(0.2)


def test_claim_audit_prompt_includes_execution_comparisons_from_alignment_schema() -> None:
    md = (
        "## 3. Claims\n"
        "| Claim | Evidence | Assessment | Status | Location |\n"
        "|---|---|---|---|---|\n"
        "| TinyMethod reaches MRR 0.355 on FB15k-237. | Table 1 reports MRR 0.355. | ok | Pending | Table 1 |\n"
        "## 4. Summary\n"
        "**Weaknesses:**\n- w\n\n"
        "## 5. Experiment\n"
        "### Ablation Result\n"
        "| Dim | Cfg | Full | Paper | Delta |\n"
        "|---|---|---|---|---|\n"
        "| A | no | 1.0 | 0.5 | -0.5 |\n"
    )
    alignment = {
        "comparisons": [
            {
                "paper_key": "FB15k-237 / TinyMethod / mrr",
                "observed_key": "metrics/eval_metrics.json / mrr",
                "dataset": "FB15k-237",
                "metric": "mrr",
                "paper_value": 0.355,
                "observed_value": 0.300,
                "delta": -0.055,
                "delta_pct": 15.49,
                "passed": False,
            }
        ]
    }
    seen: dict[str, str] = {}

    def capture_prompt(prompt: str) -> dict[str, Any]:
        seen["prompt"] = prompt
        return {"verdicts": [{"id": 0, "verdict": "in_conflict", "reason": "execution is lower"}]}

    audit_review_markdown(md, execution_alignment=alignment, llm_call=capture_prompt)

    prompt = seen["prompt"]
    assert "Execution reproduction results" in prompt
    assert "paper=0.355" in prompt
    assert "reproduced=0.3" in prompt
    assert "status=FAIL" in prompt


def test_claim_audit_prompt_scales_legacy_execution_matches() -> None:
    md = (
        "## 3. Claims\n"
        "| Claim | Evidence | Assessment | Status | Location |\n"
        "|---|---|---|---|---|\n"
        "| Ours reaches 94.2 accuracy on CIFAR-10. | Table 1 reports 94.2 accuracy. | ok | Pending | Table 1 |\n"
        "## 4. Summary\n"
        "**Weaknesses:**\n- w\n\n"
    )
    alignment = {
        "matches": [
            {
                "dataset": "CIFAR-10",
                "paper_row_label": "Ours",
                "run_metrics_file": "metrics/eval_metrics.json",
                "expected": {"accuracy": 94.2},
                "observed": {"accuracy": 0.943},
                "delta": {"accuracy": 0.001},
                "within_tolerance": {"accuracy": True},
                "passed": True,
            }
        ]
    }
    seen: dict[str, str] = {}

    def capture_prompt(prompt: str) -> dict[str, Any]:
        seen["prompt"] = prompt
        return {"verdicts": [{"id": 0, "verdict": "supported", "reason": "execution matches"}]}

    audit_review_markdown(md, execution_alignment=alignment, llm_call=capture_prompt)

    prompt = seen["prompt"]
    assert "paper=0.942" in prompt
    assert "reproduced=0.943" in prompt
    assert "status=PASS" in prompt
