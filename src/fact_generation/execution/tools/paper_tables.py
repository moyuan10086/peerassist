from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PaperMetricTarget:
    """
    A best-effort, paper-extracted numeric target.

    This is intentionally "loose": different papers expose different schemas.
    We focus on a small common subset and keep enough provenance to audit.
    """

    paper_table_id: str
    paper_table_md_path: str
    dataset: str
    scoring_function: str  # e.g. "TransE"
    method: str  # e.g. "X + CoMPGCN (Sub)"
    metrics: dict[str, float]  # e.g. {"mrr":0.335, "mr":194, "hits@10":0.514}
    metric_source: str = "table"
    paper_claim: str = ""


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _strip_md(s: str) -> str:
    # Remove simple markdown emphasis/backticks.
    t = (s or "").strip()
    t = t.replace("`", "")
    t = re.sub(r"[*_]+", "", t)
    return _norm_space(t)


def _split_md_row(line: str) -> list[str]:
    # Markdown tables: | a | b | c |
    s = (line or "").strip()
    if not s.startswith("|"):
        return []
    # Remove leading/trailing pipes and split.
    s = s.strip("|")
    return [_strip_md(x) for x in s.split("|")]


def _is_sep_row(cells: list[str]) -> bool:
    if not cells:
        return False
    # e.g. ["---", "---:", "---"]
    return all(re.fullmatch(r":?-{3,}:?", (c or "").strip()) for c in cells)


def _to_float(s: str) -> float | None:
    t = (s or "").strip()
    if not t:
        return None
    # Handle ".294" style.
    if re.fullmatch(r"\.\d+", t):
        t = "0" + t
    # Remove commas and stray symbols.
    t = t.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _metric_key(cell: str) -> str:
    raw = _strip_md(cell)
    c = raw.lower()
    compact = re.sub(r"[\s_\-]+", "", c)
    compact = compact.replace("h@", "hits@")
    compact = compact.replace("hit@", "hits@")
    compact = compact.replace("hitsat", "hits@")
    compact = compact.replace("hit@k", "hits@k")
    if compact in {"mrr", "meanreciprocalrank"}:
        return "mrr"
    if compact in {"mr", "meanrank"}:
        return "mr"
    m = re.search(r"(?:hits?|h)@?(\d+|k)", compact)
    if m:
        return f"hits@{m.group(1)}"
    aliases: list[tuple[str, str]] = [
        ("accuracy", "accuracy"),
        ("acc", "accuracy"),
        ("errorrate", "error_rate"),
        ("error", "error_rate"),
        ("f1score", "f1"),
        ("f1", "f1"),
        ("precision", "precision"),
        ("recall", "recall"),
        ("auc", "auc"),
        ("auroc", "auc"),
        ("bleu", "bleu"),
        ("rougel", "rouge-l"),
        ("rouge-l", "rouge-l"),
        ("rouge1", "rouge-1"),
        ("rouge2", "rouge-2"),
        ("map", "map"),
        ("ndcg", "ndcg"),
        ("mae", "mae"),
        ("rmse", "rmse"),
        ("mse", "mse"),
        ("perplexity", "perplexity"),
        ("ppl", "perplexity"),
        ("loss", "loss"),
        ("fid", "fid"),
        ("inceptionscore", "inception_score"),
        ("is", "inception_score"),
        ("r2", "r2"),
    ]
    for token, key in aliases:
        if compact == token or (len(token) >= 4 and token in compact):
            return key
    return compact


def _is_metric_header(cell: str) -> bool:
    key = _metric_key(cell)
    if not key:
        return False
    return key in {
        "mrr",
        "mr",
        "accuracy",
        "error_rate",
        "f1",
        "precision",
        "recall",
        "auc",
        "bleu",
        "rouge-l",
        "rouge-1",
        "rouge-2",
        "map",
        "ndcg",
        "mae",
        "rmse",
        "mse",
        "perplexity",
        "loss",
        "fid",
        "inception_score",
        "r2",
    } or key.startswith("hits@")


def _table_blocks(md_text: str) -> list[list[list[str]]]:
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []
    for raw in (md_text or "").splitlines():
        if raw.strip().startswith("|"):
            cells = _split_md_row(raw)
            if cells:
                current.append(cells)
            continue
        if current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _header_role(cell: str) -> str:
    c = _metric_key(cell)
    raw = _strip_md(cell).lower()
    if _is_metric_header(cell):
        return "metric"
    if any(tok in raw for tok in ("dataset", "data set", "benchmark", "corpus", "task")):
        return "dataset"
    if any(tok in raw for tok in ("method", "model", "approach", "system", "setting", "configuration", "variant")):
        return "method"
    if any(tok in raw for tok in ("split", "seed", "epoch", "year", "params", "#")):
        return "metadata"
    return "text"


def _row_label(cells: list[str], metric_cols: set[int], method_col: int | None) -> str:
    if method_col is not None and method_col < len(cells):
        value = cells[method_col]
        if value:
            return value
    parts: list[str] = []
    for i, cell in enumerate(cells):
        if i in metric_cols:
            continue
        if _to_float(cell) is not None:
            continue
        value = _strip_md(cell)
        if value:
            parts.append(value)
        if len(parts) >= 3:
            break
    return " / ".join(parts).strip()


def _extract_generic_markdown_tables(
    md_text: str, *, paper_table_id: str, paper_table_md_path: str
) -> list[PaperMetricTarget]:
    """Parse ordinary markdown result tables with metric columns.

    This is deliberately conservative: it only emits targets when a row has
    at least one metric-looking header and numeric value, and it keeps the raw
    row label/provenance so a human can audit any automatic match.
    """

    targets: list[PaperMetricTarget] = []
    for block_idx, block in enumerate(_table_blocks(md_text)):
        rows = [r for r in block if r and not _is_sep_row(r)]
        if len(rows) < 2:
            continue

        header = rows[0]
        roles = [_header_role(c) for c in header]
        metric_cols = {i for i, role in enumerate(roles) if role == "metric"}
        if not metric_cols:
            continue

        dataset_col = next((i for i, role in enumerate(roles) if role == "dataset"), None)
        method_col = next((i for i, role in enumerate(roles) if role == "method"), None)
        if method_col is None:
            non_metric_text = [
                i
                for i, role in enumerate(roles)
                if i not in metric_cols and role in {"text", "dataset"}
            ]
            method_col = non_metric_text[0] if non_metric_text else None

        for row_idx, row in enumerate(rows[1:], start=1):
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            metrics: dict[str, float] = {}
            for col in metric_cols:
                if col >= len(row) or col >= len(header):
                    continue
                value = _to_float(row[col])
                if value is None:
                    continue
                metrics[_metric_key(header[col])] = value
            if not metrics:
                continue

            method = _row_label(row, metric_cols, method_col)
            if not method:
                continue
            dataset = ""
            if dataset_col is not None and dataset_col < len(row):
                dataset = _strip_md(row[dataset_col])
            target_id = paper_table_id or Path(paper_table_md_path).stem
            targets.append(
                PaperMetricTarget(
                    paper_table_id=f"{target_id}:table{block_idx}:row{row_idx}",
                    paper_table_md_path=paper_table_md_path,
                    dataset=dataset,
                    scoring_function="",
                    method=method,
                    metrics=metrics,
                    metric_source="generic_table",
                    paper_claim=" | ".join(row),
                )
            )
    return targets


def _extract_metric_row_markdown_tables(
    md_text: str, *, paper_table_id: str, paper_table_md_path: str
) -> list[PaperMetricTarget]:
    """Parse result tables where metrics are rows and methods are columns."""

    out: list[PaperMetricTarget] = []
    for block_idx, block in enumerate(_table_blocks(md_text)):
        rows = [r for r in block if r and not _is_sep_row(r)]
        if len(rows) < 2:
            continue
        header = rows[0]
        metric_col = next(
            (
                i
                for i, h in enumerate(header)
                if _strip_md(h).lower() in {"metric", "metrics", "measure", "score"}
            ),
            None,
        )
        if metric_col is None:
            metric_col = 0 if any(_is_metric_header(r[0]) for r in rows[1:] if r) else None
        if metric_col is None:
            continue

        dataset_col = next(
            (i for i, h in enumerate(header) if "dataset" in _strip_md(h).lower()),
            None,
        )
        grouped: dict[tuple[str, str], dict[str, float]] = {}
        claims: dict[tuple[str, str], list[str]] = {}
        for row in rows[1:]:
            if metric_col >= len(row):
                continue
            metric = _metric_key(row[metric_col])
            if not _is_metric_header(row[metric_col]):
                continue
            dataset = ""
            if dataset_col is not None and dataset_col < len(row):
                dataset = _strip_md(row[dataset_col])
            for col, method in enumerate(header):
                if col == metric_col or col == dataset_col:
                    continue
                if col >= len(row):
                    continue
                value = _to_float(row[col])
                if value is None:
                    continue
                label = _strip_md(method)
                if not label or _is_metric_header(label):
                    continue
                key = (dataset, label)
                grouped.setdefault(key, {})[metric] = value
                claims.setdefault(key, []).append(" | ".join(row))

        target_id = paper_table_id or Path(paper_table_md_path).stem
        for row_idx, ((dataset, method), metrics) in enumerate(grouped.items(), start=1):
            if not metrics:
                continue
            out.append(
                PaperMetricTarget(
                    paper_table_id=f"{target_id}:metric_rows{block_idx}:col{row_idx}",
                    paper_table_md_path=paper_table_md_path,
                    dataset=dataset,
                    scoring_function="",
                    method=method,
                    metrics=metrics,
                    metric_source="metric_row_table",
                    paper_claim="; ".join(claims.get((dataset, method), [])[:4]),
                )
            )
    return out


_CLAIM_METRIC_RE = re.compile(
    r"(?P<metric>MRR|MR|H@ ?\d+|Hits?@ ?\d+|Accuracy|Acc\.?|F1|BLEU|ROUGE-?L|ROUGE-?1|ROUGE-?2|AUC|"
    r"MAE|RMSE|MSE|Perplexity|PPL|Loss|FID)"
    r"\s*(?:=|:|of|is|are)?\s*"
    r"(?P<value>-?\d+(?:\.\d+)?|\.\d+)\s*%?",
    flags=re.IGNORECASE,
)


def _sentence_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = _norm_space(raw)
        if not s:
            continue
        if len(s) > 600:
            continue
        chunks.append(s)
    return chunks


def _extract_dataset_hint(text: str) -> str:
    known = [
        "FB15k-237",
        "WN18RR",
        "CIFAR-10",
        "CIFAR-100",
        "ImageNet",
        "MNIST",
        "SST-2",
        "CoLA",
        "MRPC",
        "QQP",
        "QNLI",
        "RTE",
        "WNLI",
        "SQuAD",
        "ZINC",
        "MUTAG",
    ]
    low = text.lower()
    for name in known:
        if name.lower() in low:
            return name
    m = re.search(r"\b[A-Z][A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+\b", text)
    return m.group(0) if m else ""


def extract_claim_metric_targets(md_text: str, *, source_path: str = "") -> list[PaperMetricTarget]:
    targets: list[PaperMetricTarget] = []
    for idx, sent in enumerate(_sentence_chunks(md_text)):
        matches = list(_CLAIM_METRIC_RE.finditer(sent))
        if not matches:
            continue
        metrics: dict[str, float] = {}
        for m in matches:
            key = _metric_key(m.group("metric"))
            value = _to_float(m.group("value"))
            if key and value is not None:
                metrics[key] = value
        if not metrics:
            continue
        dataset = _extract_dataset_hint(sent)
        targets.append(
            PaperMetricTarget(
                paper_table_id=f"claim:{idx}",
                paper_table_md_path=source_path,
                dataset=dataset,
                scoring_function="",
                method=sent[:160],
                metrics=metrics,
                metric_source="claim_text",
                paper_claim=sent,
            )
        )
    return targets


def _dedupe_targets(targets: list[PaperMetricTarget]) -> list[PaperMetricTarget]:
    out: list[PaperMetricTarget] = []
    seen: set[tuple[str, str, str, tuple[tuple[str, float], ...]]] = set()
    for target in targets:
        key = (
            _norm_space(target.dataset).lower(),
            _norm_space(target.method).lower(),
            _norm_space(target.scoring_function).lower(),
            tuple(sorted((k, round(float(v), 8)) for k, v in target.metrics.items())),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(target)
    return out


def _extract_compgcn_table_004(
    md_text: str, *, paper_table_id: str, paper_table_md_path: str
) -> list[PaperMetricTarget]:
    """
    Extract targets from COMPGCN Table 4-like markdown (multi-header layout).

    Expected shape (from mineru extraction):
    - header row: "Scoring Function (=X) → | TransE | DistMult | ConvE | ..."
    - header row: "Methods ↓ | MRR | MR | H@ 10 | MRR | MR | H@10 | ..."
    - rows: "X + CoMPGCN (Sub) | 0.335 | 194 | 0.514 | ..."
    """
    lines = [ln.rstrip("\n") for ln in (md_text or "").splitlines()]
    # Find a markdown table block that includes "Scoring Function" and "Methods".
    start = None
    for i, ln in enumerate(lines):
        if "Scoring Function" in ln and ln.strip().startswith("|"):
            start = i
            break
    if start is None:
        return []

    # Collect contiguous table lines.
    block: list[str] = []
    for ln in lines[start:]:
        if not ln.strip().startswith("|"):
            break
        block.append(ln)
    if len(block) < 4:
        return []

    header1 = _split_md_row(block[0])
    # block[1] is usually separator row
    header2 = []
    # Find header2: first non-separator after header1
    for ln in block[1:3]:
        c = _split_md_row(ln)
        if c and not _is_sep_row(c):
            header2 = c
            break
    if not header1 or not header2:
        return []

    # Scoring functions are in header1 after first cell; ignore empties.
    scoring_funcs = [c for c in header1[1:] if c]
    if not scoring_funcs:
        return []

    # Metrics are in header2 after first cell (repeated).
    metric_cells = [c for c in header2[1:] if c]
    if not metric_cells:
        return []
    metrics = [_metric_key(c) for c in metric_cells]

    # We expect a repeating group like [mrr, mr, hits@10] for each scoring func.
    group_size = 3
    max_cols = min(len(scoring_funcs) * group_size, len(metrics))

    targets: list[PaperMetricTarget] = []
    for ln in block:
        row = _split_md_row(ln)
        if not row or _is_sep_row(row):
            continue
        if len(row) < 2:
            continue
        method = row[0]
        if not method or method.lower().startswith("methods"):
            continue

        vals = row[1:]
        # Trim/pad to max cols
        vals = vals[:max_cols]
        if len(vals) < max_cols:
            # pad missing with empty
            vals = vals + [""] * (max_cols - len(vals))

        for j in range(0, max_cols, group_size):
            sf_idx = j // group_size
            sf = scoring_funcs[sf_idx] if sf_idx < len(scoring_funcs) else ""
            if not sf:
                continue
            m: dict[str, float] = {}
            for k in range(group_size):
                key = metrics[j + k] if (j + k) < len(metrics) else ""
                v = _to_float(vals[j + k])
                if key and v is not None:
                    m[key] = v
            if not m:
                continue
            targets.append(
                PaperMetricTarget(
                    paper_table_id=paper_table_id,
                    paper_table_md_path=paper_table_md_path,
                    dataset="FB15k-237",
                    scoring_function=sf,
                    method=method,
                    metrics=m,
                )
            )
    return targets


def extract_paper_metric_targets(
    paper_extracted_tables_dir: Path | None = None,
    *,
    paper_markdown_path: Path | None = None,
    paper_markdown_text: str = "",
) -> list[PaperMetricTarget]:
    """
    Best-effort extraction of numeric targets from paper_extracted tables.

    This is designed to be conservative:
    - if we can't parse, we return an empty list rather than guessing.
    - we keep provenance (table id + md path) for audit.
    """
    out: list[PaperMetricTarget] = []

    if paper_extracted_tables_dir is None:
        tables_dir = None
    else:
        tables_dir = Path(paper_extracted_tables_dir)

    if tables_dir is not None and tables_dir.exists():
        idx = tables_dir / "index.json"
    else:
        idx = Path("")

    if tables_dir is None or not tables_dir.exists() or not idx.exists():
        items: Any = []
    else:
        try:
            items = json.loads(_read_text(idx) or "[]")
        except Exception:
            items = []
    if not isinstance(items, list):
        items = []

    for it in items:
        if not isinstance(it, dict):
            continue
        table_id = str(it.get("id") or "").strip()
        md_path = str(it.get("path_md") or it.get("md_path") or "").strip()
        if not md_path or tables_dir is None:
            continue
        p = Path(md_path)
        if not p.is_absolute():
            # Some index.json uses relative paths; treat them as relative to tables_dir.
            p = tables_dir / md_path
        if not p.exists():
            continue
        md_text = _read_text(p)

        # Specialized parser first; generic parser below handles ordinary
        # result tables across domains.
        if "Scoring Function" in md_text and "CoMPGCN" in md_text:
            out.extend(
                _extract_compgcn_table_004(
                    md_text, paper_table_id=table_id or p.stem, paper_table_md_path=str(p)
                )
            )
        out.extend(
            _extract_generic_markdown_tables(
                md_text, paper_table_id=table_id or p.stem, paper_table_md_path=str(p)
            )
        )
        out.extend(
            _extract_metric_row_markdown_tables(
                md_text, paper_table_id=table_id or p.stem, paper_table_md_path=str(p)
            )
        )

    md_for_claims = str(paper_markdown_text or "")
    md_source = ""
    if not md_for_claims and paper_markdown_path is not None:
        md_source = str(paper_markdown_path)
        md_for_claims = _read_text(Path(paper_markdown_path))
    elif paper_markdown_path is not None:
        md_source = str(paper_markdown_path)
    if md_for_claims:
        out.extend(extract_claim_metric_targets(md_for_claims, source_path=md_source))

    return _dedupe_targets(out)
