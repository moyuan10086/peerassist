from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .paper_tables import _metric_key


def _as_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except Exception:
        return None


def _iter_json_objects(text: str) -> list[Any]:
    objects: list[Any] = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s[0] not in "{[":
            continue
        try:
            objects.append(json.loads(s))
        except Exception:
            continue
    return objects


def _collect_metrics_from_obj(obj: Any) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                metrics.update(_collect_metrics_from_obj(v))
                continue
            key = _metric_key(str(k))
            value = _as_float(v)
            if key and value is not None:
                metrics[key] = float(value)
    elif isinstance(obj, list):
        for item in obj:
            metrics.update(_collect_metrics_from_obj(item))
    return metrics


def _metric_aliases(metric: str) -> list[str]:
    key = _metric_key(metric)
    aliases = {
        "accuracy": ["accuracy", "acc", "top1", "top-1", "top 1"],
        "error_rate": ["error", "error rate"],
        "f1": ["f1", "f1 score", "f1-score"],
        "precision": ["precision", "prec"],
        "recall": ["recall"],
        "auc": ["auc", "auroc"],
        "mrr": ["mrr", "mean reciprocal rank"],
        "mr": ["mr", "mean rank"],
        "bleu": ["bleu"],
        "rouge-l": ["rouge-l", "rouge l", "rougel"],
        "rouge-1": ["rouge-1", "rouge 1", "rouge1"],
        "rouge-2": ["rouge-2", "rouge 2", "rouge2"],
        "mae": ["mae"],
        "rmse": ["rmse"],
        "mse": ["mse"],
        "perplexity": ["perplexity", "ppl"],
        "loss": ["loss"],
    }
    if key.startswith("hits@"):
        suffix = key.split("@", 1)[1]
        return [key, f"h@{suffix}", f"hit@{suffix}", f"hits @ {suffix}", f"hits-{suffix}"]
    return aliases.get(key, [key])


def _extract_metric_by_regex(text: str, metric: str) -> float | None:
    number = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
    for alias in _metric_aliases(metric):
        a = re.escape(alias).replace("\\ ", r"\s+")
        patterns = [
            rf"(?i)\b{a}\b\s*(?:=|:|is|of)?\s*(?P<value>{number})\s*(?P<pct>%)?",
            rf"(?i)(?P<value>{number})\s*(?P<pct>%)?\s*\b{a}\b",
        ]
        for pattern in patterns:
            matches = list(re.finditer(pattern, text or ""))
            if not matches:
                continue
            m = matches[-1]
            try:
                value = float(m.group("value"))
            except Exception:
                continue
            return value
    return None


def extract_metrics_from_text(
    text: str,
    *,
    expected_metrics: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Extract machine-readable metrics from stdout/stderr text.

    The execution stage cannot assume every research repo writes JSON metrics.
    This helper first trusts JSON-looking log lines, then falls back to regexes
    for the exact metrics the paper target expects.
    """

    out: dict[str, float] = {}
    for obj in _iter_json_objects(text):
        out.update(_collect_metrics_from_obj(obj))

    expected = expected_metrics or {}
    candidates = list(expected)
    if not candidates:
        # A real eval/reproduction run may not have paper targets wired yet,
        # but common metrics in logs are still useful evidence for alignment.
        candidates = [
            "accuracy",
            "f1",
            "precision",
            "recall",
            "auc",
            "mrr",
            "hits@1",
            "hits@3",
            "hits@10",
            "bleu",
            "rouge-l",
            "rouge-1",
            "rouge-2",
            "mae",
            "rmse",
            "mse",
            "perplexity",
        ]
    for raw_key in candidates:
        key = _metric_key(str(raw_key))
        if key in out:
            continue
        value = _extract_metric_by_regex(text, key)
        if value is not None:
            out[key] = value
    return out


def write_task_metric_artifact(
    *,
    artifacts_dir: Path,
    task_id: str,
    task: dict[str, Any],
    stdout: str,
    stderr: str,
) -> str:
    expected = task.get("expected_metrics") if isinstance(task.get("expected_metrics"), dict) else {}
    text = "\n".join([stdout or "", stderr or ""])
    metrics = extract_metrics_from_text(text, expected_metrics=expected)
    if not metrics and not expected:
        return ""

    payload: dict[str, Any] = {
        "task_id": task_id,
        "dataset": str(task.get("dataset") or ""),
        "split": str(task.get("split") or task.get("eval_split") or ""),
        "method": str(task.get("method") or task.get("model") or task.get("variant") or ""),
        "family": str(task.get("family") or ""),
        "claims": task.get("claims") if isinstance(task.get("claims"), list) else [],
        "expected_metrics": expected,
        "metrics": metrics,
        "found_metrics": sorted(metrics),
    }
    for key, value in metrics.items():
        payload[key] = value

    out_dir = Path(artifacts_dir) / "metrics"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{task_id}_metrics.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(out_path.relative_to(artifacts_dir)).replace("\\", "/")
