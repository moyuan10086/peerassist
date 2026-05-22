"""Review report sub-stage.

Reads the agent-runner's final report artifacts (markdown + audit) from the
parse-stage snapshot, normalises image paths, optionally appends a
``RefCopilot`` summary, re-renders the PDF, and writes the canonical review
output to ``stages/review/report/``.

A clean copy of the markdown (without the refcheck section) is also written to
``final_review_clean.md`` so the teaser sub-stage can build its prompt from a
report that has not been polluted by reference-check findings.
"""

from __future__ import annotations

import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime.runner import (
    augment_claims_with_assessment_status,
    augment_experiment_with_eval_status,
)
from common.config import get_settings
from common.pipeline_context import (
    ensure_full_pipeline_context,
    execution_stage_dir,
    load_job_state_snapshot,
    load_stage_assets_snapshot,
    read_json_file,
    refcheck_stage_dir,
    report_stage_dir,
    require_bridge_state,
    resolve_artifact_path,
    write_json_file,
)
from fact_generation.refcheck.refcheck import format_reference_check_markdown
from review.report.claim_audit import audit_review_markdown
from review.report.pdf_renderer import build_review_report_pdf
from schemas.stage import StageResult
from util.fs import copy_file_if_exists

_EXECUTION_BLOCK_START = "<!-- execution-reproduction-check:start -->"
_EXECUTION_BLOCK_END = "<!-- execution-reproduction-check:end -->"


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _strip_experiment_eval_status(text: str) -> str:
    """Remove Evaluation Status column and its legend from experiment tables."""
    sec = re.search(
        r"(?ims)(^##\s+(?:\*\*)?5\.\s+Experiment(?:\*\*)?\s*$\n)(.*?)(?=^##\s+|\Z)",
        text,
    )
    if not sec:
        return text

    body = sec.group(2)

    # Remove colored status-legend lines added by _augment_experiment_with_eval_status.
    body = re.sub(r"(?m)^\(Status legend:.*\)\s*$\n?", "", body)

    def _strip_last_col(line: str) -> str:
        stripped = line.rstrip("\n")
        if not (stripped.startswith("|") and stripped.endswith("|")):
            return line
        parts = stripped.split("|")
        # parts: ['', col1, col2, ..., colN, '']
        # Remove the second-to-last non-empty slot (the last column cell).
        if len(parts) >= 4:
            parts = parts[:-2] + parts[-1:]
        return "|".join(parts) + "\n"

    lines = body.splitlines(keepends=True)
    result: list[str] = []
    i = 0
    while i < len(lines):
        header = lines[i].rstrip()
        if i + 1 < len(lines) and header.startswith("|") and header.endswith("|"):
            sep = lines[i + 1].rstrip()
            if re.fullmatch(r"\|[ :\-|]+\|", sep):
                # Check if last header column is "Evaluation Status".
                cols = [c.strip().strip("*") for c in header.strip("|").split("|")]
                if cols and cols[-1].lower() == "evaluation status":
                    j = i + 2
                    while j < len(lines) and lines[j].rstrip().startswith("|"):
                        j += 1
                    result.append(_strip_last_col(lines[i]))
                    result.append(_strip_last_col(lines[i + 1]))
                    for k in range(i + 2, j):
                        result.append(_strip_last_col(lines[k]))
                    i = j
                    continue
        result.append(lines[i])
        i += 1

    new_body = "".join(result)
    return text[: sec.start(2)] + new_body + text[sec.end(2) :]


def _as_comparison_rows(execution_alignment: dict[str, Any]) -> list[dict[str, Any]]:
    rows = execution_alignment.get("comparisons") if isinstance(execution_alignment, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _fmt_metric_value(value: Any) -> str:
    try:
        return f"{float(value):.6g}"
    except Exception:
        return str(value or "")


def _md_escape(value: Any) -> str:
    return str(value or "").replace("|", "&#124;").strip()


def _build_execution_reproduction_block(
    *,
    exec_json: dict[str, Any],
    execution_alignment: dict[str, Any],
) -> str:
    status = str(exec_json.get("status") or "").strip()
    if status == "skipped":
        return ""

    rows = _as_comparison_rows(execution_alignment)
    summary = exec_json.get("summary") if isinstance(exec_json.get("summary"), dict) else {}
    run_result = summary.get("run_result") if isinstance(summary.get("run_result"), dict) else {}
    if not rows and not summary and not execution_alignment:
        return ""

    failed = [row for row in rows if not bool(row.get("passed"))]
    passed = [row for row in rows if bool(row.get("passed"))]
    lines: list[str] = [
        _EXECUTION_BLOCK_START,
        "### Execution Reproduction Check",
        "",
    ]
    if rows:
        if failed:
            lines.append(
                f"- Deterministic execution comparison found `{len(failed)}` metric(s) outside tolerance "
                f"out of `{len(rows)}` aligned paper-vs-run metric(s)."
            )
        else:
            lines.append(
                f"- Deterministic execution comparison found all `{len(rows)}` aligned paper-vs-run metric(s) within tolerance."
            )
        if passed:
            lines.append(f"- Metrics within tolerance: `{len(passed)}`.")
        lines.append(
            "- These rows are generated from execution artifacts after the experiment run; they should override pre-execution narrative claims when they conflict."
        )
        lines.append("")
        lines.append("| Dataset | Metric | Paper | Reproduced | Delta | Tolerance | Result |")
        lines.append("|---|---|---:|---:|---:|---:|---|")
        for row in rows[:40]:
            result = "PASS" if row.get("passed") else "FAIL"
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_escape(row.get("dataset") or "n/a"),
                        _md_escape(row.get("metric")),
                        _fmt_metric_value(row.get("paper_value")),
                        _fmt_metric_value(row.get("observed_value")),
                        _fmt_metric_value(row.get("delta")),
                        _fmt_metric_value(row.get("tolerance")),
                        result,
                    ]
                )
                + " |"
            )
    else:
        run_success = run_result.get("success")
        lines.append(
            "- Execution completed but no aligned paper-vs-run metric comparison rows were produced."
            if run_success
            else "- Execution did not produce aligned paper-vs-run metric comparison rows."
        )
        lines.append(
            "- Treat quantitative reproduction claims as inconclusive unless manual artifact inspection supplies the missing comparison."
        )

    unmatched = execution_alignment.get("unmatched_run_metrics") if isinstance(execution_alignment, dict) else []
    if isinstance(unmatched, list) and unmatched:
        lines.append("")
        lines.append(
            "- Unmatched run metric artifacts: "
            + ", ".join(f"`{_md_escape(item)}`" for item in unmatched[:8])
        )
    lines.extend(["", _EXECUTION_BLOCK_END])
    return "\n".join(lines).strip()


def _execution_weakness_bullets(
    *,
    exec_json: dict[str, Any],
    execution_alignment: dict[str, Any],
) -> list[str]:
    status = str(exec_json.get("status") or "").strip()
    if status == "skipped":
        return []
    rows = _as_comparison_rows(execution_alignment)
    failed = [row for row in rows if not bool(row.get("passed"))]
    if failed:
        def _delta_abs(row: dict[str, Any]) -> float:
            try:
                return abs(float(row.get("delta") or 0.0))
            except Exception:
                return 0.0

        largest = max(failed, key=_delta_abs)
        return [
            "Execution reproduction found "
            f"{len(failed)} of {len(rows)} aligned metric(s) outside tolerance; "
            f"largest observed gap: {_md_escape(largest.get('dataset') or 'n/a')} "
            f"{_md_escape(largest.get('metric'))} paper={_fmt_metric_value(largest.get('paper_value'))}, "
            f"reproduced={_fmt_metric_value(largest.get('observed_value'))}, "
            f"delta={_fmt_metric_value(largest.get('delta'))}."
        ]

    summary = exec_json.get("summary") if isinstance(exec_json.get("summary"), dict) else {}
    run_result = summary.get("run_result") if isinstance(summary.get("run_result"), dict) else {}
    if run_result.get("success") and not rows:
        return [
            "Execution completed but produced no aligned paper-vs-run metric comparison rows; "
            "quantitative reproduction claims remain inconclusive."
        ]
    return []


def _inject_execution_weaknesses(
    markdown: str,
    *,
    exec_json: dict[str, Any],
    execution_alignment: dict[str, Any],
) -> str:
    bullets = _execution_weakness_bullets(
        exec_json=exec_json,
        execution_alignment=execution_alignment,
    )
    bullets = [bullet for bullet in bullets if bullet and bullet not in markdown]
    if not bullets:
        return markdown

    text = str(markdown or "")
    sec = re.search(
        r"(?ims)(^##\s+(?:\*\*)?4\.\s+Summary(?:\*\*)?\s*$\n)(?P<body>.*?)(?=^##\s+|\Z)",
        text,
    )
    if not sec:
        return text
    body = sec.group("body")
    additions = "\n".join(f"- [execution] {bullet}" for bullet in bullets)
    label_match = re.search(r"(?i)\*{0,2}Weaknesses\*{0,2}\s*:?", body)
    if label_match is None:
        new_body = body.rstrip() + "\n\n**Weaknesses:**\n" + additions + "\n\n"
        return text[: sec.start("body")] + new_body + text[sec.end("body") :]

    tail = body[label_match.end() :]
    insertion_offset = label_match.end()
    cursor = 0
    saw_bullet = False
    for raw_line in tail.split("\n"):
        line_len = len(raw_line) + 1
        stripped = raw_line.strip()
        is_bullet = stripped.startswith("- ") or stripped.startswith("* ")
        is_label = re.match(
            r"(?i)^\*{0,2}(?:Strengths|Weaknesses)\*{0,2}\s*:",
            stripped,
        )
        if is_bullet:
            saw_bullet = True
            cursor += line_len
            insertion_offset = label_match.end() + cursor
            continue
        if saw_bullet and stripped == "":
            cursor += line_len
            continue
        if saw_bullet or is_label:
            break
        cursor += line_len

    insertion = ("\n" if not body[:insertion_offset].endswith("\n") else "") + additions
    new_body = body[:insertion_offset].rstrip() + "\n" + insertion + body[insertion_offset:]
    return text[: sec.start("body")] + new_body + text[sec.end("body") :]


def _upsert_execution_reproduction_block(
    markdown: str,
    *,
    exec_json: dict[str, Any],
    execution_alignment: dict[str, Any],
) -> str:
    text = str(markdown or "")
    block = _build_execution_reproduction_block(
        exec_json=exec_json,
        execution_alignment=execution_alignment,
    )

    marker_pattern = re.compile(
        rf"(?ims)\n*{re.escape(_EXECUTION_BLOCK_START)}.*?{re.escape(_EXECUTION_BLOCK_END)}\n*"
    )
    text = marker_pattern.sub("\n\n", text)
    if not block:
        return re.sub(r"\n{3,}", "\n\n", text).strip() + ("\n" if text.strip() else "")

    sec = re.search(
        r"(?ims)(^##\s+(?:\*\*)?5\.\s+Experiment(?:\*\*)?\s*$\n)(?P<body>.*?)(?=^##\s+|\Z)",
        text,
    )
    if not sec:
        return text.rstrip() + "\n\n" + block + "\n"

    body = sec.group("body").rstrip()
    new_body = body + "\n\n" + block + "\n\n"
    return text[: sec.start("body")] + new_body + text[sec.end("body") :]


def _sort_claims_by_importance(markdown: str) -> str:
    """Reorder the Section 3 claim table so primary-importance rows precede secondary rows."""
    sec = re.search(
        r"(?ims)(^##\s+(?:\*\*)?3\.\s+Claims(?:\*\*)?\s*$\n)(?P<body>.*?)(?=^##\s+|\Z)",
        markdown,
    )
    if not sec:
        return markdown
    body = sec.group("body")
    lines = body.split("\n")

    header_idx = -1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("|") and "claim" in s.lower() and "evidence" in s.lower() and "importance" in s.lower():
            header_idx = i
            break
    if header_idx < 0:
        return markdown

    headers = [c.strip().lower() for c in lines[header_idx].strip("|").split("|")]
    importance_col = next((i for i, h in enumerate(headers) if "importance" in h), -1)
    if importance_col < 0:
        return markdown

    data_start = header_idx + 2
    data_end = data_start
    while data_end < len(lines):
        s = lines[data_end].strip()
        if not (s.startswith("|") and s.endswith("|")):
            break
        data_end += 1

    if data_end <= data_start:
        return markdown

    primary: list[str] = []
    secondary: list[str] = []
    for row in lines[data_start:data_end]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        val = cells[importance_col].strip().lower() if importance_col < len(cells) else ""
        (primary if val == "primary" else secondary).append(row)

    new_lines = lines[:data_start] + primary + secondary + lines[data_end:]
    new_body = "\n".join(new_lines)
    return markdown[: sec.start("body")] + new_body + markdown[sec.end("body") :]


def _strip_claim_columns(markdown: str, columns: list[str]) -> str:
    """Remove named columns from the Section 3 claim table (case-insensitive)."""
    if not columns:
        return markdown
    col_set = {c.lower() for c in columns}
    sec = re.search(
        r"(?ims)(^##\s+(?:\*\*)?3\.\s+Claims(?:\*\*)?\s*$\n)(?P<body>.*?)(?=^##\s+|\Z)",
        markdown,
    )
    if not sec:
        return markdown
    body = sec.group("body")
    lines = body.split("\n")

    header_idx = -1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("|") and "claim" in s.lower() and "evidence" in s.lower():
            header_idx = i
            break
    if header_idx < 0:
        return markdown

    headers_raw = [c.strip() for c in lines[header_idx].strip("|").split("|")]
    # Strip markdown formatting when matching column names
    col_indices = sorted(
        [i for i, h in enumerate(headers_raw) if re.sub(r"[*`_ ]", "", h).lower() in col_set],
        reverse=True,
    )
    if not col_indices:
        return markdown

    data_start = header_idx + 2
    data_end = data_start
    while data_end < len(lines):
        s = lines[data_end].strip()
        if not (s.startswith("|") and s.endswith("|")):
            break
        data_end += 1

    def _drop_cols(line: str) -> str:
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            return line
        cells = s.strip("|").split("|")
        for idx in col_indices:
            if idx < len(cells):
                cells.pop(idx)
        return "|" + "|".join(cells) + "|"

    new_lines = lines[:]
    for i in range(header_idx, data_end):
        new_lines[i] = _drop_cols(lines[i])

    new_body = "\n".join(new_lines)
    return markdown[: sec.start("body")] + new_body + markdown[sec.end("body") :]


def _render_review_pdf(
    *, markdown_path: Path, pdf_path: Path, workspace_title: str, source_pdf_name: str
) -> tuple[bool, str]:
    """Render the review PDF. Returns ``(ok, error)``; ``error`` is empty on success."""
    if not markdown_path.exists() or not markdown_path.is_file():
        return False, f"markdown source not found at {markdown_path}"
    md_text = markdown_path.read_text(encoding="utf-8", errors="ignore")
    overview_path = markdown_path.parent / "overview_figure.jpg"
    if overview_path.exists() and overview_path.is_file():
        md_text = md_text.replace("./overview_figure.jpg", str(overview_path.resolve()))
    try:
        settings = get_settings()
        pdf_bytes = build_review_report_pdf(
            workspace_title=workspace_title,
            source_pdf_name=source_pdf_name,
            run_id=markdown_path.parents[3].name,
            status="completed",
            decision=None,
            estimated_cost=0,
            actual_cost=None,
            exported_at=datetime.now(UTC),
            meta_review={},
            reviewers=[],
            raw_output=None,
            final_report_markdown=md_text,
            source_pdf_bytes=None,
            source_annotations=[],
            review_display_id=None,
            owner_email=None,
            token_usage={},
            agent_model=str(settings.agent_model or "").strip() or "factreview-review",
        )
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(pdf_bytes)
        return True, ""
    except Exception as exc:
        # PDF rendering is best-effort; the markdown is the canonical artifact.
        # Route the message to stderr so single-stage CLI stdout stays clean,
        # and bubble it up to the caller so it can be surfaced in StageResult.
        message = f"{type(exc).__name__}: {exc}"
        print(f"[report] PDF render failed: {message}", file=sys.stderr)
        return False, message


def _absolutize_markdown_image_refs(*, markdown_path: Path, source_base_dirs: list[Path]) -> None:
    if not markdown_path.exists() or not markdown_path.is_file():
        return
    text = markdown_path.read_text(encoding="utf-8", errors="ignore")

    def _replace(match: re.Match[str]) -> str:
        whole = match.group(0) or ""
        src = (match.group(1) or "").strip()
        if not src:
            return whole
        src_path = Path(src).expanduser()
        if src_path.is_absolute():
            return whole
        for base_dir in source_base_dirs:
            resolved = (base_dir / src).resolve()
            if resolved.exists() and resolved.is_file():
                target = resolved
                if resolved.name.lower() == "overview_figure.jpg":
                    alias = resolved.with_name("technical_positioning_image.jpg")
                    try:
                        if not alias.exists() or not alias.is_file():
                            shutil.copy2(resolved, alias)
                        target = alias
                    except OSError:
                        target = resolved
                return whole.replace(src, str(target))
        return whole

    updated = re.sub(r"!\[[^\]]*\]\(([^)]+)\)", _replace, text)
    if updated != text:
        markdown_path.write_text(updated, encoding="utf-8")


def _load_reference_check_payload(run_dir: Path) -> dict[str, Any]:
    return read_json_file(refcheck_stage_dir(run_dir) / "reference_check.json")


def _append_reference_check_section(
    *,
    markdown_path: Path,
    reference_check: dict[str, Any],
    max_issues: int,
) -> str:
    if not reference_check.get("enabled"):
        return ""
    section = format_reference_check_markdown(reference_check, max_issues=max_issues).strip()
    if not section:
        return ""
    current = _read_text(markdown_path).rstrip()
    markdown_path.write_text(current + "\n\n" + section + "\n", encoding="utf-8")
    return section + "\n"


def run_report_stage(
    *,
    repo_root: Path,
    run_dir: Path,
) -> StageResult:
    ensure_full_pipeline_context(run_dir=run_dir, allow_standalone=True, stage="report")
    # Report is a pure consumer of the parse-stage snapshot. If the bridge
    # state is missing we fail clean rather than silently re-running the agent
    # runtime — keeping the documented invariant that every stage except
    # ``parse`` works from the run dir alone.
    bridge = require_bridge_state(run_dir=run_dir)

    job_state = load_job_state_snapshot(run_dir) or read_json_file(bridge.job_json_path)
    stage_assets = load_stage_assets_snapshot(run_dir)
    artifacts = job_state.get("artifacts") if isinstance(job_state.get("artifacts"), dict) else {}
    metadata = job_state.get("metadata") if isinstance(job_state.get("metadata"), dict) else {}
    final_md_raw = str(artifacts.get("final_markdown_path") or "").strip()
    final_audit_raw = str(artifacts.get("final_report_audit_path") or "").strip()
    final_pdf_raw = str(artifacts.get("report_pdf_path") or "").strip()

    final_md_snapshot_raw = str(stage_assets.get("final_markdown_snapshot_path") or "").strip()
    final_pdf_snapshot_raw = str(stage_assets.get("report_pdf_snapshot_path") or "").strip()
    final_md_snapshot = Path(final_md_snapshot_raw).resolve() if final_md_snapshot_raw else None
    final_pdf_snapshot = Path(final_pdf_snapshot_raw).resolve() if final_pdf_snapshot_raw else None
    final_md = (
        final_md_snapshot
        if (final_md_snapshot is not None and final_md_snapshot.exists())
        else resolve_artifact_path(repo_root, final_md_raw)
    )
    final_audit = resolve_artifact_path(repo_root, final_audit_raw) if final_audit_raw else None
    final_pdf = (
        final_pdf_snapshot
        if (final_pdf_snapshot is not None and final_pdf_snapshot.exists())
        else resolve_artifact_path(repo_root, final_pdf_raw)
    )

    out_dir = report_stage_dir(run_dir)
    review_json = out_dir / "final_review.json"
    review_md = out_dir / "final_review.md"
    review_md_clean = out_dir / "final_review_clean.md"
    review_audit = out_dir / "final_review_audit.json"
    pdf_path = out_dir / "final_review.pdf"

    md_ok = copy_file_if_exists(final_md, review_md)
    audit_ok = copy_file_if_exists(final_audit, review_audit)
    pdf_ok = copy_file_if_exists(final_pdf, pdf_path)

    exec_json = read_json_file(execution_stage_dir(run_dir) / "execution.json")
    exec_alignment = exec_json.get("alignment") if isinstance(exec_json, dict) else {}

    # Re-normalize the claims table after the runtime job. The first pass inside
    # the agent runtime ran before execution, so this stage can add real
    # execution alignment when available and keep markdown-table escaping stable
    # before the mandatory claim audit.
    if md_ok:
        current_md = review_md.read_text(encoding="utf-8", errors="ignore")
        augmented_md = augment_claims_with_assessment_status(
            current_md,
            summary=exec_json.get("summary") or {},
            alignment=exec_alignment if isinstance(exec_alignment, dict) else {},
        )
        augmented_md = augment_experiment_with_eval_status(
            augmented_md,
            summary=exec_json.get("summary") or {},
            alignment=exec_alignment if isinstance(exec_alignment, dict) else {},
        )
        augmented_md = _upsert_execution_reproduction_block(
            augmented_md,
            exec_json=exec_json if isinstance(exec_json, dict) else {},
            execution_alignment=exec_alignment if isinstance(exec_alignment, dict) else {},
        )
        augmented_md = _inject_execution_weaknesses(
            augmented_md,
            exec_json=exec_json if isinstance(exec_json, dict) else {},
            execution_alignment=exec_alignment if isinstance(exec_alignment, dict) else {},
        )
        if augmented_md != current_md:
            review_md.write_text(augmented_md, encoding="utf-8")

    settings = get_settings()
    reference_check_payload = _load_reference_check_payload(run_dir)
    reference_check_markdown = ""
    reference_check_appended = False
    pdf_render_error = ""
    claim_audit_payload: dict[str, Any] = {}

    if md_ok:
        if final_md is not None and final_md.exists():
            _absolutize_markdown_image_refs(
                markdown_path=review_md,
                source_base_dirs=[final_md.parent, bridge.job_dir],
            )
        # Always snapshot the clean (no-refcheck) version for the teaser sub-stage.
        shutil.copy2(review_md, review_md_clean)

        # When execution was skipped, strip the Evaluation Status column so the
        # report and the teaser figure do not display all-Inconclusive placeholders.
        if exec_json.get("status") == "skipped":
            for md_path in (review_md, review_md_clean):
                stripped = _strip_experiment_eval_status(
                    md_path.read_text(encoding="utf-8", errors="ignore")
                )
                md_path.write_text(stripped, encoding="utf-8")

        # Post-report claim audit. review_md and review_md_clean are identical
        # at this point (refcheck is appended later only to review_md), so run
        # the mandatory LLM audit once and copy the audited canonical markdown
        # into the clean teaser source.
        if review_md.exists():
            source_text = review_md.read_text(encoding="utf-8", errors="ignore")
            audited_text, outcome = audit_review_markdown(
                source_text,
                execution_alignment=exec_alignment if isinstance(exec_alignment, dict) else None,
            )
            if audited_text != source_text:
                review_md.write_text(audited_text, encoding="utf-8")

            # Sort claim table by importance (primary first) then produce two versions:
            # review_md_clean keeps Type+Importance for teaser selection;
            # review_md strips them for the public-facing report/PDF.
            current_text = review_md.read_text(encoding="utf-8", errors="ignore")
            sorted_text = _sort_claims_by_importance(current_text)
            review_md_clean.write_text(sorted_text, encoding="utf-8")
            public_text = _strip_claim_columns(sorted_text, ["type", "importance"])
            review_md.write_text(public_text, encoding="utf-8")
            claim_audit_payload = {
                "claim_results": [
                    {
                        "original_status": c.original_status,
                        "final_status": c.final_status,
                        "llm_verdict": c.llm_verdict,
                        "llm_reason": c.llm_reason,
                        "agent_self_verdict": c.agent_self_verdict,
                        "agent_self_reason": c.agent_self_reason,
                        "notes": c.notes,
                    }
                    for c in outcome.claim_results
                ],
                "axis_self_selection_ratio": outcome.axis_self_selection_ratio,
                "ablation_components_missing": outcome.ablation_components_missing,
                "extra_weaknesses": outcome.extra_weaknesses,
                "llm_raw": outcome.llm_raw,
            }

        if reference_check_payload.get("enabled"):
            reference_check_markdown = _append_reference_check_section(
                markdown_path=review_md,
                reference_check=reference_check_payload,
                max_issues=max(1, int(settings.reference_check_report_max_issues)),
            )
            reference_check_appended = bool(reference_check_markdown.strip())

        source_name = bridge.paper_pdf.name if bridge.paper_pdf else "paper.pdf"
        rendered_pdf_ok, pdf_render_error = _render_review_pdf(
            markdown_path=review_md,
            pdf_path=pdf_path,
            workspace_title=bridge.paper_key,
            source_pdf_name=source_name,
        )
        if reference_check_appended:
            pdf_ok = rendered_pdf_ok
            if not pdf_ok and pdf_path.exists():
                try:
                    pdf_path.unlink()
                except OSError:
                    pass
        else:
            pdf_ok = rendered_pdf_ok or pdf_ok

    execution_payload = exec_json

    if claim_audit_payload:
        write_json_file(out_dir / "claim_audit.json", claim_audit_payload)

    write_json_file(
        review_json,
        {
            "paper_key": bridge.paper_key,
            "run_id": run_dir.name,
            "job_id": bridge.job_id,
            "status": bridge.own_payload.get("status"),
            "message": bridge.own_payload.get("message"),
            "error": bridge.own_payload.get("error"),
            "usage": bridge.own_payload.get("usage") or {},
            "metadata": metadata,
            "execution": execution_payload,
            "reference_check": reference_check_payload,
            "reference_check_markdown": reference_check_markdown,
            "claim_audit": claim_audit_payload,
            "final_markdown": _read_text(review_md) if md_ok else "",
            "final_audit_path": str(final_audit_raw)
            if (final_audit is not None and final_audit.exists())
            else "",
            "final_audit": read_json_file(review_audit) if audit_ok else {},
            "final_markdown_path": final_md_raw if (final_md is not None and final_md.exists()) else "",
            "final_pdf_path": final_pdf_raw if (final_pdf is not None and final_pdf.exists()) else "",
        },
    )

    # ``main`` is the canonical user-facing artifact (the rendered review
    # markdown). Only populate keys for files that actually exist on disk so
    # callers don't dereference paths to nothing. ``json`` is always written
    # because we just produced ``review_json`` above. Tie the overall stage
    # status to ``md_ok`` so the contract ``status == "ok" ⟹ outputs["main"]
    # exists`` holds.
    outputs: dict[str, str] = {"json": str(review_json)}
    if md_ok:
        outputs["main"] = str(review_md)
        outputs["markdown"] = str(review_md)
    if review_md_clean.exists():
        outputs["markdown_clean"] = str(review_md_clean)
    if audit_ok:
        outputs["audit_json"] = str(review_audit)
    if pdf_ok:
        outputs["pdf"] = str(pdf_path)
    error = ""
    if not md_ok:
        if final_md is None:
            error = "agent runner produced no final_markdown_path"
        elif not final_md.exists():
            error = f"final review markdown not found at {final_md}"
        else:
            error = f"failed to copy final review markdown from {final_md} to {review_md}"

    extra: dict[str, Any] = {}
    if pdf_render_error:
        # Markdown is the canonical artifact, so we keep status="ok" when it's
        # written even if the PDF render failed; surface the cause in extra so
        # the run summary records why no PDF exists.
        extra["pdf_render_error"] = pdf_render_error

    return StageResult(
        status="ok" if md_ok else "failed",
        outputs=outputs,
        extra=extra,
        error=error,
    )


if __name__ == "__main__":
    raise SystemExit("Internal stage module. Use scripts/execute_review_pipeline.py.")
