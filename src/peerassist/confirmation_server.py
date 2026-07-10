"""Local PeerAssist human confirmation review server."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from common.pipeline_context import peerassist_stage_dir, write_json_file
from peerassist.confirmation_workflow import apply_confirmation_decision, load_confirmation_state
from peerassist.confirmations import build_confirmation_bundle, build_confirmation_review_queue
from schemas.peerassist import Concern, ConcernLevel, ConcernStatus


def render_confirmation_page(*, run_dir: Path, paper_id: str) -> str:
    state = load_confirmation_state(run_dir=run_dir)
    source_pdf_path = _discover_source_pdf(run_dir=run_dir, paper_id=paper_id)
    model_config = _resolve_model_config()
    items = state.get("queue", {}).get("items", [])
    rows = "\n".join(_render_item(item) for item in items if isinstance(item, dict))
    if not rows:
        rows = '<section class="empty">暂无需要人工确认的审稿关注点。</section>'
    runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
    mode = str(runtime.get("mode") or "unknown")
    pending_count = int(state.get("pending_count") or 0)
    actions_count = int(state.get("actions_count") or 0)
    agent_runs = state.get("agent_runs") if isinstance(state.get("agent_runs"), list) else []
    tool_trace = state.get("tool_trace") if isinstance(state.get("tool_trace"), dict) else {}
    events = tool_trace.get("events") if isinstance(tool_trace.get("events"), list) else []
    invocations = (
        state.get("capability_invocations")
        if isinstance(state.get("capability_invocations"), list)
        else []
    )
    paths = state.get("paths") if isinstance(state.get("paths"), dict) else {}
    if source_pdf_path is not None:
        paths = {**paths, "source_pdf": str(source_pdf_path)}
        state = {**state, "paths": paths}
    state_json = html.escape(json.dumps(state, ensure_ascii=False), quote=False)
    evidence_preview = (
        state.get("evidence_preview") if isinstance(state.get("evidence_preview"), list) else []
    )
    evidence_count = _evidence_count(items)
    completed_event_count = _event_status_count(events, "completed")
    failed_event_count = _event_status_count(events, "failed")
    review_progress = _review_progress(pending_count=pending_count, actions_count=actions_count)
    runtime_health = _runtime_health_label(failed_event_count=failed_event_count)
    stage_lifecycle = _review_stage_lifecycle(
        queue_items=len(items) if isinstance(items, list) else 0,
        evidence_preview_count=len(evidence_preview),
        agent_runs=agent_runs,
        events=events,
        invocations=invocations,
        pending_count=pending_count,
        actions_count=actions_count,
        failed_event_count=failed_event_count,
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PeerAssist 论文审核辅助台</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #18201f;
      --muted: #6b7472;
      --line: #d9dfdc;
      --paper: #f4f7f7;
      --panel: #ffffff;
      --panel-soft: #f9faf6;
      --panel-tint: #eef6ff;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --blue: #2458a6;
      --amber: #9a6500;
      --danger: #a33a3a;
      --violet: #5f4b8b;
      --shadow: 0 18px 42px rgba(20, 35, 35, 0.09);
      --mono-panel: #202724;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.5 "Microsoft YaHei", "PingFang SC", "Segoe UI", ui-sans-serif, sans-serif;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(15, 118, 110, 0.035) 1px, transparent 1px),
        linear-gradient(180deg, rgba(36, 88, 166, 0.03) 1px, transparent 1px),
        var(--paper);
      background-size: 42px 42px;
    }}
    code {{
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 12px;
    }}
    .topbar {{
      position: sticky;
      top: 0;
      z-index: 2;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 20px;
      align-items: center;
      padding: 16px 24px;
      border-bottom: 1px solid rgba(198, 220, 215, 0.24);
      background: rgba(18, 30, 29, 0.96);
      backdrop-filter: blur(16px);
      color: #f4fbf8;
    }}
    .topbar[data-stream-state="live"] .stream-dot {{ background: #14a076; box-shadow: 0 0 0 5px rgba(20, 160, 118, 0.15); }}
    .topbar[data-stream-state="polling"] .stream-dot {{ background: #c38b22; box-shadow: 0 0 0 5px rgba(195, 139, 34, 0.16); }}
    .brand {{
      display: flex;
      gap: 12px;
      align-items: center;
    }}
    .mark {{
      width: 34px;
      height: 34px;
      border: 1px solid #b8d8cf;
      border-radius: 8px;
      display: grid;
      place-items: center;
      color: #d8fff5;
      background: #163f3b;
      font-weight: 800;
    }}
    h1 {{ margin: 0; font-size: 19px; line-height: 1.15; font-weight: 760; letter-spacing: 0; }}
    .subtitle {{ margin-top: 3px; color: #a9bbb7; font-size: 12px; }}
    .subtitle code {{ color: #eef8f4; }}
    .meta {{ color: #a9bbb7; font-size: 13px; }}
    .runtime-strip {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
    }}
    .ops-strip {{
      width: min(1560px, calc(100% - 40px));
      margin: 10px auto 0;
      display: grid;
      grid-template-columns: 1.2fr repeat(4, minmax(120px, 1fr));
      gap: 10px;
      align-items: stretch;
    }}
    .workflow-band {{
      width: min(1560px, calc(100% - 40px));
      margin: 12px auto 0;
      display: grid;
      gap: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.9);
      box-shadow: 0 12px 30px rgba(20, 35, 35, 0.06);
      padding: 14px;
    }}
    .workflow-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(180px, 260px);
      gap: 14px;
      align-items: center;
      margin-bottom: 12px;
    }}
    .workflow-kicker {{
      margin: 0;
      font-size: 12px;
      font-weight: 850;
      color: #394340;
    }}
    .workflow-copy {{
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
    }}
    .workflow-meter {{
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 760;
    }}
    .workflow-meter strong {{
      color: var(--ink);
      font-size: 18px;
      line-height: 1;
    }}
    .workflow-track, .compact-meter {{
      height: 8px;
      border-radius: 999px;
      background: #e7ecea;
      overflow: hidden;
    }}
    .workflow-track span, .compact-meter span {{
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), var(--blue), var(--violet));
    }}
    .workflow-steps {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }}
    .workflow-step {{
      display: grid;
      grid-template-columns: 38px minmax(0, 1fr);
      gap: 10px;
      align-items: start;
      min-height: 64px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: inherit;
      text-decoration: none;
      background: linear-gradient(180deg, #ffffff, #f8fbfa);
    }}
    .workflow-step:hover {{ border-color: #b8d8cf; transform: translateY(-1px); }}
    .workflow-index {{
      width: 38px;
      height: 38px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      background: #132320;
      color: #d8fff5;
      font-size: 12px;
      font-weight: 900;
    }}
    .workflow-title {{ font-weight: 820; font-size: 13px; }}
    .workflow-detail {{ color: var(--muted); font-size: 12px; margin-top: 2px; }}
    .agent-stage-board {{
      display: grid;
      grid-template-columns: repeat(8, minmax(118px, 1fr));
      gap: 8px;
    }}
    .agent-stage-card {{
      position: relative;
      min-height: 92px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 10px;
    }}
    .agent-stage-card::before {{
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: #cbd8d4;
    }}
    .agent-stage-card[data-stage-status="completed"]::before {{ background: var(--accent); }}
    .agent-stage-card[data-stage-status="active"]::before {{ background: var(--blue); }}
    .agent-stage-card[data-stage-status="waiting"]::before {{ background: #b7bfc0; }}
    .agent-stage-card[data-stage-status="blocked"]::before {{ background: var(--danger); }}
    .agent-stage-top {{
      display: flex;
      gap: 6px;
      align-items: center;
      justify-content: space-between;
    }}
    .agent-stage-index {{
      color: #7a8581;
      font-size: 10px;
      font-weight: 900;
    }}
    .agent-stage-status {{
      border-radius: 999px;
      padding: 2px 7px;
      background: #eef3f2;
      color: #4b5a56;
      font-size: 10px;
      font-weight: 850;
      white-space: nowrap;
    }}
    .agent-stage-card[data-stage-status="completed"] .agent-stage-status {{ color: var(--accent-strong); background: #e8f5f2; }}
    .agent-stage-card[data-stage-status="active"] .agent-stage-status {{ color: var(--blue); background: #edf4ff; }}
    .agent-stage-card[data-stage-status="blocked"] .agent-stage-status {{ color: var(--danger); background: #fff0f0; }}
    .agent-stage-title {{
      margin-top: 8px;
      color: #22302d;
      font-size: 12px;
      font-weight: 840;
    }}
    .agent-stage-copy {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
    }}
    .ops-card {{
      min-height: 76px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.86);
      box-shadow: 0 10px 28px rgba(24, 32, 31, 0.08);
      padding: 12px;
    }}
    .ops-card.primary {{
      background: #132320;
      color: #eef8f4;
      border-color: #132320;
    }}
    .ops-kicker {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
    }}
    .ops-card.primary .ops-kicker {{ color: #98d6c8; }}
    .ops-value {{
      margin-top: 4px;
      font-size: 22px;
      font-weight: 820;
      line-height: 1.1;
    }}
    .ops-label {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
    }}
    .ops-card.primary .ops-label {{ color: #c7d8d4; }}
    .stream-chip {{
      display: inline-flex;
      align-items: center;
      gap: 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
      background: #fff;
      color: #394340;
      font-weight: 700;
    }}
    .stream-dot {{
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #b8a15a;
      box-shadow: 0 0 0 5px rgba(184, 161, 90, 0.14);
    }}
    .console-shell {{
      width: min(1760px, 100%);
      margin: 0 auto;
      padding: 12px 18px 20px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 390px;
      gap: 16px;
      min-height: calc(100vh - 68px);
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
    }}
    .panel-header {{
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }}
    .panel-title {{
      margin: 0;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0;
      color: #394340;
      font-weight: 780;
    }}
    .panel-subtitle {{
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
    }}
    .rail {{
      display: none;
      gap: 12px;
      align-content: start;
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      padding: 12px;
    }}
    .metric {{
      min-height: 74px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
    }}
    .metric-value {{ font-size: 24px; font-weight: 800; line-height: 1.1; }}
    .metric-label {{ margin-top: 6px; color: var(--muted); font-size: 11px; }}
    .run-map {{
      padding: 0 12px 12px;
      display: grid;
      gap: 7px;
    }}
    .run-map-row {{
      display: grid;
      grid-template-columns: 92px minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      min-height: 28px;
      color: var(--muted);
      font-size: 12px;
    }}
    .run-map-bar {{
      height: 8px;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--accent), #4f7fbd);
    }}
    .mode-badge {{
      display: inline-flex;
      align-items: center;
      border: 1px solid #b8d8cf;
      border-radius: 999px;
      padding: 3px 8px;
      color: var(--accent-strong);
      background: #eef8f4;
      font-size: 12px;
      font-weight: 700;
    }}
    .agent-timeline {{
      padding: 12px 14px 16px;
      display: grid;
      gap: 10px;
    }}
    .agent-row {{
      display: grid;
      grid-template-columns: 10px minmax(0, 1fr);
      gap: 10px;
      align-items: start;
    }}
    .agent-dot {{
      width: 10px;
      height: 10px;
      margin-top: 5px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 0 3px #e3f2ee;
    }}
    .agent-name {{ font-weight: 760; font-size: 13px; }}
    .agent-detail {{ color: var(--muted); font-size: 12px; }}
    .agent-meta {{
      margin-top: 4px;
      color: #7b8582;
      font-size: 11px;
      overflow-wrap: anywhere;
    }}
    .session-nav {{
      display: grid;
      gap: 8px;
      padding: 12px 14px 16px;
    }}
    .nav-step {{
      display: grid;
      grid-template-columns: 24px minmax(0, 1fr);
      gap: 9px;
      align-items: start;
      min-height: 34px;
      color: inherit;
      text-decoration: none;
    }}
    .step-index {{
      width: 24px;
      height: 24px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      background: #edf4ff;
      color: var(--blue);
      font-size: 11px;
      font-weight: 850;
      border: 1px solid #c7d7ef;
    }}
    .step-title {{ font-weight: 780; font-size: 12px; }}
    .step-copy {{ color: var(--muted); font-size: 11px; margin-top: 1px; }}
    .queue {{
      min-height: 100%;
      overflow: hidden;
    }}
    .paper-review-stage {{
      display: grid;
      gap: 16px;
      align-content: start;
      min-width: 0;
    }}
    .paper-viewer {{
      overflow: hidden;
    }}
    .paper-toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(90deg, #f6fbfa, #f8f8ff);
    }}
    .paper-chip {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
      color: #394340;
      background: #fff;
      font-size: 11px;
      font-weight: 780;
    }}
    .paper-chip.focus-review-button {{
      cursor: pointer;
      color: #ffffff;
      border-color: #22302d;
      background: #22302d;
    }}
    .paper-chip.focus-review-button[aria-pressed="true"] {{
      border-color: #b8d8cf;
      color: var(--accent-strong);
      background: #eef8f4;
    }}
    .paper-canvas {{
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 0;
      padding: 14px;
      background: #eef3f2;
    }}
    .source-pdf-shell {{
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-height: 720px;
      border: 1px solid #d8dedc;
      border-radius: 6px;
      background: #dde4e1;
      box-shadow: 0 16px 32px rgba(20, 35, 35, 0.12);
      overflow: hidden;
    }}
    .pdf-reader-bar {{
      display: grid;
      grid-template-columns: auto minmax(120px, 1fr) auto;
      gap: 8px;
      align-items: center;
      padding: 9px 10px;
      border-bottom: 1px solid #c7d0cc;
      background: rgba(249, 251, 250, 0.95);
    }}
    .pdf-control-group {{
      display: inline-flex;
      gap: 4px;
      align-items: center;
    }}
    .pdf-icon-button {{
      width: 30px;
      height: 30px;
      border: 1px solid #cbd6d2;
      border-radius: 8px;
      background: #fff;
      color: #27322f;
      font-weight: 900;
      line-height: 1;
      cursor: pointer;
    }}
    .pdf-icon-button:hover {{
      border-color: #9bc9bd;
      color: var(--accent-strong);
    }}
    .pdf-icon-button:disabled {{
      cursor: not-allowed;
      opacity: 0.45;
    }}
    .pdf-page-status {{
      color: #4e5d59;
      font-size: 12px;
      font-weight: 820;
      text-align: center;
      white-space: nowrap;
    }}
    .pdf-page-rail {{
      display: flex;
      gap: 6px;
      align-items: center;
      overflow-x: auto;
      padding: 8px 10px;
      border-bottom: 1px solid #c7d0cc;
      background: rgba(242, 247, 245, 0.96);
      scrollbar-width: thin;
    }}
    .pdf-page-rail-label {{
      flex: 0 0 auto;
      color: #60706c;
      font-size: 11px;
      font-weight: 850;
      padding: 0 4px;
    }}
    .pdf-reading-map {{
      display: grid;
      grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.75fr);
      gap: 8px;
      padding: 9px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #f7faf9;
    }}
    .pdf-reading-panel {{
      min-width: 0;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #fff;
      padding: 8px;
    }}
    .pdf-reading-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: #25322f;
      font-size: 11px;
      font-weight: 920;
    }}
    .pdf-reading-subtitle {{
      color: #66736f;
      font-size: 10px;
      font-weight: 800;
      text-align: right;
    }}
    .pdf-outline-list {{
      display: flex;
      gap: 6px;
      overflow-x: auto;
      margin-top: 7px;
      padding-bottom: 2px;
      scrollbar-width: thin;
    }}
    .pdf-outline-item {{
      flex: 0 0 auto;
      max-width: 220px;
      min-height: 30px;
      border: 1px solid #d7e4df;
      border-radius: 999px;
      background: #f8fbfa;
      color: #25322f;
      padding: 5px 9px;
      font-size: 11px;
      font-weight: 850;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      cursor: pointer;
    }}
    .pdf-outline-item[data-outline-depth="1"] {{ margin-left: 8px; }}
    .pdf-outline-item[data-outline-depth="2"] {{ margin-left: 16px; }}
    .pdf-outline-item:hover {{
      border-color: #0f766e;
      color: #0f766e;
      background: #eef8f4;
    }}
    .pdf-outline-empty {{
      margin-top: 7px;
      color: #66736f;
      font-size: 11px;
      font-weight: 800;
    }}
    .pdf-review-route {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
      margin-top: 7px;
    }}
    .pdf-route-chip {{
      min-width: 0;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #f8fbfa;
      padding: 7px;
    }}
    .pdf-route-label {{
      color: #66736f;
      font-size: 9px;
      font-weight: 900;
    }}
    .pdf-route-value {{
      margin-top: 2px;
      color: #25322f;
      font-size: 11px;
      font-weight: 900;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .pdf-reading-route-list {{
      display: grid;
      gap: 6px;
      margin-top: 8px;
    }}
    .pdf-reading-route-card {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      width: 100%;
      min-height: 42px;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #fff;
      color: #25322f;
      padding: 7px 8px;
      text-align: left;
      cursor: pointer;
    }}
    .pdf-reading-route-card[data-route-state="pending"] {{
      border-color: #e4c783;
      background: #fff8e6;
    }}
    .pdf-reading-route-card[data-route-state="major"] {{
      border-color: #d39c3d;
      background: #fff8ea;
    }}
    .pdf-reading-route-card[data-active="true"] {{
      border-color: #0f766e;
      background: #12302b;
      color: #fff;
    }}
    .pdf-reading-route-page {{
      min-width: 42px;
      border-radius: 8px;
      background: rgba(15, 118, 110, 0.1);
      color: #0f766e;
      padding: 6px 7px;
      font-size: 10px;
      font-weight: 920;
      text-align: center;
      white-space: nowrap;
    }}
    .pdf-reading-route-card[data-active="true"] .pdf-reading-route-page {{
      background: rgba(216, 255, 245, 0.16);
      color: #9df2de;
    }}
    .pdf-reading-route-title {{
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 11px;
      font-weight: 900;
    }}
    .pdf-reading-route-meta {{
      margin-top: 2px;
      color: #66736f;
      font-size: 9px;
      font-weight: 850;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .pdf-reading-route-card[data-active="true"] .pdf-reading-route-meta {{
      color: #d8fff5;
    }}
    .pdf-reading-route-rank {{
      color: #66736f;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 10px;
      font-weight: 900;
    }}
    .pdf-reading-route-card[data-active="true"] .pdf-reading-route-rank {{
      color: #9df2de;
    }}
    .pdf-reading-route-empty {{
      margin-top: 8px;
      border: 1px dashed #cfdbd7;
      border-radius: 8px;
      padding: 8px;
      color: #66736f;
      background: #f8fbfa;
      font-size: 11px;
      font-weight: 800;
      text-align: center;
    }}
    .pdf-search-strip {{
      display: grid;
      grid-template-columns: minmax(220px, 0.8fr) minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      padding: 9px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #fbfdfc;
    }}
    .pdf-search-box {{
      display: flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      border: 1px solid #cbdcd7;
      border-radius: 8px;
      background: #fff;
      padding: 5px 7px;
    }}
    .pdf-search-label {{
      flex: 0 0 auto;
      color: #0f766e;
      font-size: 10px;
      font-weight: 920;
      white-space: nowrap;
    }}
    .pdf-search-input {{
      width: 100%;
      min-width: 0;
      border: 0;
      outline: 0;
      color: #25322f;
      background: transparent;
      font-size: 12px;
      font-weight: 760;
    }}
    .pdf-search-status {{
      min-width: 0;
      color: #53635f;
      font-size: 11px;
      font-weight: 820;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .pdf-search-actions {{
      display: inline-flex;
      gap: 5px;
      align-items: center;
    }}
    .pdf-search-results {{
      grid-column: 1 / -1;
      display: flex;
      gap: 6px;
      overflow-x: auto;
      min-height: 30px;
      scrollbar-width: thin;
    }}
    .pdf-search-hit {{
      flex: 0 0 auto;
      max-width: 220px;
      min-height: 28px;
      border: 1px solid #d7e4df;
      border-radius: 999px;
      background: #fff;
      color: #25322f;
      padding: 5px 9px;
      font-size: 11px;
      font-weight: 850;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      cursor: pointer;
    }}
    .pdf-search-hit[data-active="true"] {{
      border-color: #0f766e;
      color: #fff;
      background: #0f766e;
    }}
    .pdf-search-empty {{
      align-self: center;
      color: var(--muted);
      font-size: 11px;
    }}
    .pdf-review-command-strip {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #132320;
      color: #eef8f4;
    }}
    .pdf-command-label {{
      color: #98d6c8;
      font-size: 11px;
      font-weight: 900;
      white-space: nowrap;
    }}
    .pdf-command-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      justify-content: flex-end;
    }}
    .pdf-command-button {{
      width: auto;
      min-height: 30px;
      border-color: rgba(216, 255, 245, 0.24);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.08);
      color: #eef8f4;
      padding: 5px 10px;
      font-size: 11px;
      white-space: nowrap;
    }}
    .pdf-command-button.primary {{
      border-color: #8bd8c8;
      background: #0f766e;
      color: #fff;
    }}
    .pdf-command-button:hover {{
      border-color: #d8fff5;
      background: rgba(255, 255, 255, 0.14);
    }}
    .pdf-agent-dock {{
      display: grid;
      grid-template-columns: minmax(180px, 0.9fr) minmax(240px, 1.1fr) minmax(250px, 1fr);
      gap: 10px;
      align-items: stretch;
      padding: 10px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #edf5f2;
    }}
    .pdf-agent-card {{
      min-width: 0;
      border: 1px solid #cbdcd7;
      border-radius: 8px;
      background: #fff;
      padding: 9px 10px;
    }}
    .pdf-agent-kicker {{
      color: #0f766e;
      font-size: 10px;
      font-weight: 920;
    }}
    .pdf-agent-title {{
      margin-top: 3px;
      color: #182723;
      font-size: 14px;
      font-weight: 900;
    }}
    .pdf-agent-copy {{
      margin-top: 3px;
      color: #66736f;
      font-size: 11px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }}
    .pdf-agent-mode-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
      margin-top: 8px;
    }}
    .pdf-agent-mode {{
      min-height: 30px;
      border: 1px solid #cbdcd7;
      border-radius: 8px;
      background: #f8fbfa;
      color: #25322f;
      font-size: 11px;
      font-weight: 860;
      cursor: pointer;
    }}
    .pdf-agent-mode[aria-pressed="true"] {{
      border-color: #0f766e;
      background: #0f766e;
      color: #fff;
    }}
    .pdf-agent-action-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
      margin-top: 8px;
    }}
    .pdf-agent-action {{
      min-height: 32px;
      border: 1px solid #cbdcd7;
      border-radius: 8px;
      background: #fff;
      color: #25322f;
      font-size: 11px;
      font-weight: 880;
      cursor: pointer;
    }}
    .pdf-agent-action.primary {{
      border-color: #0f766e;
      background: #0f766e;
      color: #fff;
    }}
    .pdf-agent-run-controls {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 6px;
      align-items: center;
      margin-top: 8px;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #f8fbfa;
      padding: 6px;
    }}
    .pdf-agent-run-state {{
      min-width: 0;
      color: #53635f;
      font-size: 10px;
      font-weight: 850;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .pdf-agent-run-controls[data-run-state="running"] {{
      border-color: #b8d8cf;
      background: #eef8f4;
    }}
    .pdf-agent-run-controls[data-run-state="failed"],
    .pdf-agent-run-controls[data-run-state="cancelled"] {{
      border-color: #f0d2a6;
      background: #fff8ea;
    }}
    .pdf-agent-status-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
      margin-top: 8px;
    }}
    .pdf-agent-status-chip {{
      min-height: 34px;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #f8fbfa;
      padding: 6px 7px;
    }}
    .pdf-agent-status-label {{
      color: #66736f;
      font-size: 9px;
      font-weight: 900;
    }}
    .pdf-agent-status-value {{
      margin-top: 2px;
      color: #25322f;
      font-size: 11px;
      font-weight: 900;
      overflow-wrap: anywhere;
    }}
    .pdf-agent-context-manifest {{
      grid-column: 1 / -1;
      border: 1px solid #cbdcd7;
      border-radius: 8px;
      background:
        linear-gradient(90deg, rgba(18, 48, 43, 0.035), transparent 42%),
        #fff;
      padding: 9px 10px;
    }}
    .pdf-agent-context-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      color: #182723;
      font-size: 11px;
      font-weight: 920;
    }}
    .pdf-agent-context-subtitle {{
      color: #66736f;
      font-size: 10px;
      font-weight: 800;
      text-align: right;
    }}
    .pdf-agent-context-list {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 6px;
      margin-top: 8px;
    }}
    .pdf-agent-context-row {{
      min-width: 0;
      min-height: 58px;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #f8fbfa;
      padding: 7px;
    }}
    .pdf-agent-context-row[data-context-state="ready"] {{
      border-color: #b8d8cf;
      background: #eef8f4;
    }}
    .pdf-agent-context-row[data-context-state="active"] {{
      border-color: #0f766e;
      background: #12302b;
      color: #fff;
    }}
    .pdf-agent-context-row[data-context-state="waiting"] {{
      border-color: #e4c783;
      background: #fff8e6;
    }}
    .pdf-agent-context-label {{
      color: #66736f;
      font-size: 9px;
      font-weight: 920;
    }}
    .pdf-agent-context-row[data-context-state="active"] .pdf-agent-context-label {{
      color: #9df2de;
    }}
    .pdf-agent-context-value {{
      margin-top: 3px;
      color: inherit;
      font-size: 11px;
      font-weight: 900;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }}
    .pdf-agent-context-status {{
      margin-top: 3px;
      color: #66736f;
      font-size: 9px;
      font-weight: 850;
    }}
    .pdf-agent-context-row[data-context-state="active"] .pdf-agent-context-status {{
      color: #d8fff5;
    }}
    .pdf-agent-phase-rail {{
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 6px;
      border: 1px solid #cbdcd7;
      border-radius: 8px;
      background: #fff;
      padding: 8px;
    }}
    .pdf-agent-phase-step {{
      min-width: 0;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #f8fbfa;
      padding: 7px 8px;
    }}
    .pdf-agent-phase-step[data-phase-state="completed"] {{
      border-color: #b8d8cf;
      background: #eef8f4;
    }}
    .pdf-agent-phase-step[data-phase-state="active"] {{
      border-color: #0f766e;
      background: #12302b;
      color: #fff;
    }}
    .pdf-agent-phase-index {{
      color: #0f766e;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 9px;
      font-weight: 900;
    }}
    .pdf-agent-phase-step[data-phase-state="active"] .pdf-agent-phase-index {{
      color: #9df2de;
    }}
    .pdf-agent-phase-title {{
      margin-top: 3px;
      color: inherit;
      font-size: 11px;
      font-weight: 900;
      white-space: nowrap;
    }}
    .pdf-agent-phase-copy {{
      margin-top: 2px;
      color: #66736f;
      font-size: 10px;
      line-height: 1.28;
      overflow-wrap: anywhere;
    }}
    .pdf-agent-phase-step[data-phase-state="active"] .pdf-agent-phase-copy {{
      color: #d8fff5;
    }}
    .pdf-runtime-pulse {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      padding: 9px 10px;
      border-bottom: 1px solid #c7d0cc;
      background: #f7faf9;
    }}
    .pdf-runtime-chip {{
      min-height: 42px;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #fff;
      padding: 7px 8px;
    }}
    .pdf-runtime-label {{
      color: #66736f;
      font-size: 10px;
      font-weight: 900;
    }}
    .pdf-runtime-value {{
      margin-top: 3px;
      color: #25322f;
      font-size: 12px;
      font-weight: 860;
      overflow-wrap: anywhere;
    }}
    .pdf-runtime-pulse[data-stream-source="stream"] .pdf-runtime-chip:first-child {{
      border-color: #b8d8cf;
      background: #eef8f4;
    }}
    .pdf-runtime-pulse[data-stream-source="poll"] .pdf-runtime-chip:first-child {{
      border-color: #e4c783;
      background: #fff8e6;
    }}
    .pdf-human-gate {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #12302b;
      color: #eef8f4;
    }}
    .pdf-human-gate-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 7px;
    }}
    .pdf-human-gate-title {{
      color: #ffffff;
      font-size: 12px;
      font-weight: 920;
    }}
    .pdf-human-gate-copy {{
      color: #bcefe2;
      font-size: 10px;
      font-weight: 820;
      text-align: right;
    }}
    .pdf-human-gate-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
    }}
    .pdf-human-gate-chip {{
      min-width: 0;
      border: 1px solid rgba(216, 255, 245, 0.18);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.08);
      padding: 7px 8px;
    }}
    .pdf-human-gate-label {{
      color: #bcefe2;
      font-size: 9px;
      font-weight: 900;
    }}
    .pdf-human-gate-value {{
      margin-top: 2px;
      color: #fff;
      font-size: 13px;
      font-weight: 920;
      white-space: nowrap;
    }}
    .pdf-human-gate-actions {{
      display: flex;
      gap: 6px;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
      min-width: 0;
    }}
    .pdf-human-gate-actions .inline-button {{
      border-color: rgba(216, 255, 245, 0.26);
      background: rgba(255, 255, 255, 0.1);
      color: #eef8f4;
    }}
    .pdf-human-gate-actions .inline-button.primary {{
      border-color: #8bd8c8;
      background: #0f766e;
      color: #fff;
    }}
    .pdf-human-gate-meter {{
      grid-column: 1 / -1;
      height: 5px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(255, 255, 255, 0.14);
    }}
    .pdf-human-gate-meter span {{
      display: block;
      width: 0%;
      height: 100%;
      border-radius: inherit;
      background: #8bd8c8;
      transition: width 180ms ease;
    }}
    .pdf-evidence-gate {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #fbfdfc;
    }}
    .pdf-evidence-gate[data-evidence-gate-state="review"] {{
      background: #fff8ea;
    }}
    .pdf-evidence-gate-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 7px;
    }}
    .pdf-evidence-gate-title {{
      color: #25322f;
      font-size: 12px;
      font-weight: 920;
    }}
    .pdf-evidence-gate-copy {{
      color: #66736f;
      font-size: 10px;
      font-weight: 820;
      text-align: right;
    }}
    .pdf-evidence-gate-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
    }}
    .pdf-evidence-gate-chip {{
      min-width: 0;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #fff;
      padding: 7px 8px;
    }}
    .pdf-evidence-gate-label {{
      color: #66736f;
      font-size: 9px;
      font-weight: 900;
    }}
    .pdf-evidence-gate-value {{
      margin-top: 2px;
      color: #25322f;
      font-size: 13px;
      font-weight: 920;
      white-space: nowrap;
    }}
    .pdf-evidence-gate-actions {{
      display: flex;
      gap: 6px;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
      min-width: 0;
    }}
    .pdf-evidence-gate-meter {{
      grid-column: 1 / -1;
      height: 5px;
      border-radius: 999px;
      overflow: hidden;
      background: #e4ece8;
    }}
    .pdf-evidence-gate-meter span {{
      display: block;
      width: 0%;
      height: 100%;
      border-radius: inherit;
      background: var(--accent);
      transition: width 180ms ease;
    }}
    .pdf-activity-feed {{
      display: grid;
      gap: 6px;
      padding: 9px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #fbfdfc;
    }}
    .pdf-activity-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: #25322f;
      font-size: 11px;
      font-weight: 900;
    }}
    .pdf-activity-list {{
      display: grid;
      gap: 5px;
      max-height: 86px;
      overflow: auto;
    }}
    .pdf-activity-row {{
      display: grid;
      grid-template-columns: 74px minmax(0, 1fr);
      gap: 8px;
      align-items: start;
      border: 1px solid #e0e7e4;
      border-radius: 8px;
      background: #fff;
      padding: 6px 8px;
      font-size: 11px;
    }}
    .pdf-activity-kind {{
      color: var(--accent-strong);
      font-weight: 900;
      overflow-wrap: anywhere;
    }}
    .pdf-activity-copy {{
      color: #3c4946;
      overflow-wrap: anywhere;
    }}
    .pdf-activity-empty {{
      color: var(--muted);
      font-size: 11px;
    }}
    .pdf-activity-empty[hidden] {{ display: none; }}
    .pdf-tool-trace-strip {{
      display: grid;
      gap: 7px;
      padding: 9px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #f7faf9;
    }}
    .pdf-tool-trace-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: #25322f;
      font-size: 11px;
      font-weight: 900;
    }}
    .pdf-tool-trace-list {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
    }}
    .pdf-tool-trace-card {{
      min-width: 0;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #fff;
      padding: 8px;
    }}
    .pdf-tool-trace-card[data-status="completed"] {{
      border-color: #b8d8cf;
      background: #eef8f4;
    }}
    .pdf-tool-trace-card[data-status="failed"] {{
      border-color: #f0b9b9;
      background: #fff5f5;
    }}
    .pdf-tool-trace-top {{
      display: flex;
      gap: 6px;
      align-items: center;
      justify-content: space-between;
      min-width: 0;
    }}
    .pdf-tool-trace-name {{
      min-width: 0;
      color: #25322f;
      font-size: 11px;
      font-weight: 900;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .pdf-tool-trace-status {{
      flex: 0 0 auto;
      border: 1px solid #cbdcd7;
      border-radius: 999px;
      padding: 2px 6px;
      color: #0f766e;
      background: #fff;
      font-size: 9px;
      font-weight: 900;
    }}
    .pdf-tool-trace-copy {{
      margin-top: 5px;
      color: #66736f;
      font-size: 10px;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }}
    .pdf-tool-trace-empty {{
      color: var(--muted);
      font-size: 11px;
    }}
    .pdf-recovery-strip {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) repeat(3, minmax(120px, 0.55fr)) auto;
      gap: 8px;
      align-items: stretch;
      padding: 9px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #fbfdfc;
    }}
    .pdf-recovery-cell {{
      min-width: 0;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #fff;
      padding: 7px 8px;
    }}
    .pdf-recovery-label {{
      color: #66736f;
      font-size: 9px;
      font-weight: 920;
    }}
    .pdf-recovery-value {{
      margin-top: 2px;
      color: #25322f;
      font-size: 11px;
      font-weight: 900;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .pdf-recovery-strip[data-recovery-state="failed"] .pdf-recovery-cell:first-child {{
      border-color: #e6b4b4;
      background: #fff0f0;
    }}
    .pdf-recovery-strip[data-recovery-state="active"] .pdf-recovery-cell:first-child {{
      border-color: #e4c783;
      background: #fff8e6;
    }}
    .pdf-recovery-strip[data-recovery-state="completed"] .pdf-recovery-cell:first-child {{
      border-color: #b8d8cf;
      background: #eef8f4;
    }}
    .pdf-recovery-actions {{
      display: flex;
      gap: 6px;
      align-items: center;
      justify-content: flex-end;
      min-width: 0;
    }}
    .pdf-page-button {{
      position: relative;
      width: auto;
      flex: 0 0 auto;
      min-width: 32px;
      min-height: 28px;
      border-radius: 999px;
      padding: 4px 9px;
      font-size: 11px;
      font-weight: 850;
      background: #fff;
    }}
    .pdf-page-button[data-has-concern="true"] {{
      border-color: #d39c3d;
      background: #fff8ea;
      color: #7a4a0b;
      padding-right: 18px;
    }}
    .pdf-page-button[aria-current="page"] {{
      color: #fff;
      border-color: var(--accent);
      background: var(--accent);
    }}
    .pdf-page-badge {{
      position: absolute;
      top: -5px;
      right: -4px;
      display: inline-flex;
      min-width: 15px;
      height: 15px;
      align-items: center;
      justify-content: center;
      border: 1px solid #fff;
      border-radius: 999px;
      background: #cf5d1f;
      color: #fff;
      font-size: 9px;
      font-weight: 900;
      line-height: 1;
      box-shadow: 0 2px 6px rgba(29, 31, 29, 0.2);
      pointer-events: none;
    }}
    .pdf-page-button[aria-current="page"] .pdf-page-badge {{
      background: #fff;
      color: var(--accent-strong);
    }}
    .pdf-page-button[data-has-pending="true"] {{
      border-color: #cf5d1f;
      box-shadow: inset 0 0 0 1px rgba(207, 93, 31, 0.28);
    }}
    .pdf-page-button[data-has-pending="true"]::after {{
      content: "";
      position: absolute;
      right: 5px;
      bottom: 5px;
      width: 5px;
      height: 5px;
      border-radius: 999px;
      background: #cf5d1f;
    }}
    .pdf-page-button[aria-current="page"][data-has-pending="true"]::after {{
      background: #fff;
    }}
    .pdf-page-context {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 9px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #fbfdfc;
    }}
    .pdf-page-context-title {{
      color: #25322f;
      font-size: 12px;
      font-weight: 880;
    }}
    .pdf-page-context-copy {{
      margin-top: 2px;
      color: var(--muted);
      font-size: 11px;
    }}
    .pdf-page-context-actions {{
      display: inline-flex;
      gap: 6px;
      align-items: center;
    }}
    .pdf-page-annotations {{
      display: grid;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: #f7faf9;
    }}
    .pdf-annotation-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      color: #25322f;
      font-size: 12px;
      font-weight: 880;
    }}
    .pdf-annotation-count {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 820;
      white-space: nowrap;
    }}
    .pdf-annotation-head-actions {{
      display: inline-flex;
      gap: 6px;
      align-items: center;
    }}
    .pdf-annotation-progress {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
      align-items: stretch;
    }}
    .pdf-annotation-progress-chip {{
      border: 1px solid #d7e4df;
      border-radius: 8px;
      padding: 7px 8px;
      background: #fff;
    }}
    .pdf-annotation-progress-label {{
      color: var(--muted);
      font-size: 10px;
      font-weight: 850;
    }}
    .pdf-annotation-progress-value {{
      margin-top: 2px;
      color: #24312e;
      font-size: 12px;
      font-weight: 900;
    }}
    .pdf-annotation-meter {{
      grid-column: 1 / -1;
      height: 5px;
      border-radius: 999px;
      overflow: hidden;
      background: #e4ece8;
    }}
    .pdf-annotation-meter span {{
      display: block;
      height: 100%;
      width: 0%;
      border-radius: inherit;
      background: var(--accent);
      transition: width 180ms ease;
    }}
    .pdf-annotation-list {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 8px;
    }}
    .pdf-annotation-list[data-density="compact"] {{
      grid-template-columns: 1fr;
      gap: 5px;
    }}
    .pdf-annotation-card {{
      min-width: 0;
      border: 1px solid #d7e4df;
      border-radius: 8px;
      background: #fff;
      padding: 9px;
      box-shadow: 0 8px 18px rgba(24, 32, 31, 0.06);
    }}
    .pdf-annotation-card[data-active="true"] {{
      border-color: #d39c3d;
      background: #fff8ea;
    }}
    .pdf-annotation-list[data-density="compact"] .pdf-annotation-card {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      padding: 7px 8px;
    }}
    .pdf-annotation-title {{
      color: #25322f;
      font-size: 12px;
      font-weight: 850;
      line-height: 1.3;
    }}
    .pdf-annotation-meta {{
      margin-top: 5px;
      color: var(--muted);
      font-size: 11px;
      overflow-wrap: anywhere;
    }}
    .pdf-annotation-list[data-density="compact"] .pdf-annotation-meta {{
      margin-top: 2px;
    }}
    .pdf-annotation-actions {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 5px;
      margin-top: 8px;
    }}
    .pdf-annotation-list[data-density="compact"] .pdf-annotation-actions {{
      grid-template-columns: repeat(4, 54px);
      margin-top: 0;
    }}
    .pdf-annotation-action {{
      min-height: 28px;
      padding: 5px 7px;
      font-size: 11px;
      font-weight: 850;
    }}
    .pdf-annotation-action.primary {{
      border-color: var(--accent);
      color: #fff;
      background: var(--accent);
    }}
    .pdf-annotation-empty {{
      border: 1px dashed #cfdbd7;
      border-radius: 8px;
      padding: 9px;
      color: var(--muted);
      background: #fbfdfc;
      font-size: 12px;
      text-align: center;
    }}
    .pdf-annotation-empty[hidden] {{ display: none; }}
    .pdf-selection-tray {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-bottom: 1px solid #c7d0cc;
      background: linear-gradient(90deg, #fff8ea, #f4fbf8);
    }}
    .pdf-selection-tray[hidden] {{ display: none; }}
    .pdf-selection-meta {{
      color: #7a4a0b;
      font-size: 11px;
      font-weight: 900;
    }}
    .pdf-selection-quote {{
      margin-top: 3px;
      color: #25322f;
      font-size: 12px;
      line-height: 1.35;
      max-height: 44px;
      overflow: hidden;
    }}
    .pdf-selection-actions {{
      display: inline-flex;
      gap: 6px;
      align-items: center;
    }}
    .pdf-selection-popover {{
      position: absolute;
      z-index: 8;
      display: none;
      width: min(340px, calc(100% - 28px));
      border: 1px solid rgba(15, 118, 110, 0.38);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.98);
      box-shadow: 0 18px 36px rgba(20, 35, 35, 0.22);
      padding: 10px;
    }}
    .pdf-selection-popover[data-visible="true"] {{
      display: grid;
      gap: 8px;
    }}
    .pdf-selection-popover::before {{
      content: "";
      position: absolute;
      left: 18px;
      top: -7px;
      width: 12px;
      height: 12px;
      border-left: 1px solid rgba(15, 118, 110, 0.38);
      border-top: 1px solid rgba(15, 118, 110, 0.38);
      background: rgba(255, 255, 255, 0.98);
      transform: rotate(45deg);
    }}
    .pdf-selection-popover-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: #0f766e;
      font-size: 10px;
      font-weight: 920;
    }}
    .pdf-selection-popover-meta {{
      color: #7a4a0b;
      font-size: 10px;
      font-weight: 900;
      white-space: nowrap;
    }}
    .pdf-selection-popover-quote {{
      color: #25322f;
      font-size: 12px;
      line-height: 1.35;
      max-height: 52px;
      overflow: hidden;
    }}
    .pdf-selection-popover-actions {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 6px;
    }}
    .pdf-reader-stage {{
      position: relative;
      min-height: 780px;
      height: calc(100vh - 218px);
      overflow: auto;
      padding: 22px;
      background:
        linear-gradient(90deg, rgba(23, 33, 31, 0.04) 1px, transparent 1px),
        linear-gradient(180deg, rgba(23, 33, 31, 0.04) 1px, transparent 1px),
        #dfe7e4;
      background-size: 30px 30px;
    }}
    .pdf-page-wrap {{
      position: relative;
      width: max-content;
      max-width: 100%;
      min-height: 360px;
      margin: 0 auto;
      border-radius: 4px;
      background: #fff;
      box-shadow: 0 20px 42px rgba(24, 32, 31, 0.24);
      overflow: hidden;
    }}
    .pdf-page-canvas {{
      display: block;
      max-width: 100%;
      height: auto;
      background: #fff;
    }}
    .pdf-text-layer {{
      position: absolute;
      inset: 0;
      z-index: 2;
      overflow: hidden;
      line-height: 1;
      text-align: initial;
      transform-origin: 0 0;
      opacity: 1;
    }}
    .pdf-text-layer :is(span, br) {{
      position: absolute;
      color: transparent;
      white-space: pre;
      cursor: text;
      transform-origin: 0% 0%;
    }}
    .pdf-text-layer span::selection {{
      background: rgba(37, 99, 235, 0.32);
    }}
    .pdf-text-layer span.pdf-text-match {{
      border-radius: 2px;
      background: rgba(245, 158, 11, 0.34);
      box-shadow: 0 0 0 1px rgba(180, 83, 9, 0.18);
    }}
    .pdf-text-layer .markedContent {{
      position: absolute;
      inset: 0;
    }}
    .source-pdf-fallback {{
      display: grid;
      place-items: center;
      min-height: 260px;
      padding: 28px;
      color: #34413e;
      text-align: center;
    }}
    .source-pdf-fallback a {{
      color: var(--accent-strong);
      font-weight: 850;
    }}
    .pdf-loading {{
      position: absolute;
      inset: 22px;
      display: grid;
      place-items: center;
      color: #52605d;
      font-weight: 820;
      background: rgba(239, 244, 242, 0.72);
      border: 1px dashed #bbc8c4;
      border-radius: 6px;
    }}
    .pdf-loading[hidden], .source-pdf-fallback[hidden] {{
      display: none;
    }}
    .paper-sheet {{
      min-height: 520px;
      border: 1px solid #d8dedc;
      border-radius: 6px;
      background: #fffefb;
      box-shadow: 0 16px 32px rgba(20, 35, 35, 0.10);
      padding: 34px 42px;
    }}
    .paper-title {{
      margin: 0;
      color: #17211f;
      font-size: 20px;
      line-height: 1.25;
      font-weight: 850;
    }}
    .paper-authors {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .paper-section-title {{
      margin: 28px 0 8px;
      font-size: 13px;
      font-weight: 850;
      color: #27322f;
    }}
    .paper-line {{
      position: relative;
      margin: 10px 0;
      color: #303a37;
      font-family: Georgia, "Times New Roman", serif;
      font-size: 14px;
      line-height: 1.72;
    }}
    .paper-line::before {{
      content: attr(data-line);
      position: absolute;
      left: -31px;
      top: 1px;
      color: #9aa5a2;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 10px;
    }}
    .paper-highlight {{
      border-radius: 4px;
      background: linear-gradient(180deg, rgba(255, 232, 117, 0.52), rgba(255, 232, 117, 0.24));
      box-shadow: inset 0 -2px rgba(15, 118, 110, 0.26);
    }}
    .paper-highlight[data-active="true"] {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
      background: linear-gradient(180deg, rgba(255, 220, 72, 0.78), rgba(255, 220, 72, 0.36));
    }}
    .paper-comments {{
      display: grid;
      gap: 10px;
      align-content: start;
      padding: 12px 14px 16px;
      max-height: min(520px, calc(100vh - 420px));
      overflow: auto;
    }}
    .paper-comment {{
      border: 1px solid #c8e1d9;
      border-left: 4px solid var(--accent);
      border-radius: 8px;
      background: linear-gradient(180deg, #ffffff, #f4fbf8);
      padding: 10px;
    }}
    .paper-comment[data-active="true"], .item[data-active="true"] {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.13);
    }}
    .comment-anchor {{
      color: var(--accent-strong);
      font-size: 11px;
      font-weight: 850;
    }}
    .comment-title {{
      margin-top: 4px;
      color: #24302d;
      font-size: 12px;
      font-weight: 820;
    }}
    .comment-copy {{
      margin-top: 5px;
      color: var(--muted);
      font-size: 11px;
      overflow-wrap: anywhere;
    }}
    .comment-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }}
    .queue-toolbar {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 180px;
      gap: 14px;
      align-items: center;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(90deg, #f6fbfa, #f8f8ff);
    }}
    .queue-filterbar {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(180px, 260px) auto;
      gap: 10px;
      align-items: center;
      padding: 10px 16px;
      border-bottom: 1px solid var(--line);
      background: #fbfdfc;
    }}
    .queue-segmented {{
      display: inline-flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
    }}
    .queue-filter-button {{
      width: auto;
      min-height: 30px;
      border-radius: 999px;
      padding: 5px 10px;
      color: #40504b;
      background: #fff;
      font-size: 12px;
      white-space: nowrap;
    }}
    .queue-filter-button[aria-pressed="true"] {{
      border-color: var(--accent);
      color: #fff;
      background: var(--accent);
      box-shadow: 0 8px 18px rgba(15, 118, 110, 0.14);
    }}
    .queue-search {{
      min-height: 32px;
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 7px 10px;
      color: var(--ink);
      background: #fffefb;
      font: inherit;
      font-size: 12px;
    }}
    .queue-result-count {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 820;
      white-space: nowrap;
    }}
    .queue-progress-line {{
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
    }}
    .queue-body {{
      padding: 6px 16px 18px;
    }}
    .item {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 184px;
      gap: 14px;
      padding: 16px 0;
      border-bottom: 1px solid var(--line);
    }}
    .item[hidden] {{ display: none; }}
    .queue-empty {{
      margin: 10px 16px 18px;
      border: 1px dashed #cbd6d2;
      border-radius: 8px;
      padding: 14px;
      color: var(--muted);
      background: #fbfdfc;
      font-size: 12px;
      text-align: center;
    }}
    .queue-empty[hidden] {{ display: none; }}
    .item:last-child {{ border-bottom: 0; }}
    .item h2 {{ margin: 0 0 10px; font-size: 18px; line-height: 1.25; letter-spacing: 0; }}
    .evidence-chain {{
      overflow: hidden;
    }}
    .evidence-chain-head {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 14px 16px 10px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(90deg, #fbfdfc, #f6fbff);
    }}
    .evidence-chain-title {{
      margin: 0;
      color: #172320;
      font-size: 15px;
      font-weight: 900;
    }}
    .evidence-chain-copy {{
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
    }}
    .evidence-chain-count {{
      border: 1px solid #c9ddd8;
      border-radius: 999px;
      padding: 5px 10px;
      color: var(--accent-strong);
      background: #eef8f4;
      font-size: 11px;
      font-weight: 900;
      white-space: nowrap;
    }}
    .evidence-chain-grid {{
      display: grid;
      padding: 0 16px 14px;
      background: #fff;
    }}
    .evidence-chain-row {{
      display: grid;
      grid-template-columns: minmax(180px, 1.35fr) 76px 84px minmax(120px, 0.8fr) 92px 92px 116px;
      gap: 10px;
      align-items: center;
      min-height: 58px;
      border-bottom: 1px solid #edf1ef;
      color: #24312e;
      font-size: 12px;
    }}
    .evidence-chain-row:last-child {{ border-bottom: 0; }}
    .evidence-chain-row[data-active="true"] {{
      background: #eef8f4;
      box-shadow: inset 3px 0 0 var(--accent);
    }}
    .evidence-chain-row.header {{
      min-height: 34px;
      color: #6a7572;
      font-size: 11px;
      font-weight: 900;
    }}
    .chain-title {{
      min-width: 0;
      font-weight: 850;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .chain-muted {{ color: var(--muted); font-size: 11px; }}
    .chain-pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: fit-content;
      max-width: 100%;
      min-height: 24px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      background: #fafcfb;
      color: #3d4845;
      font-size: 11px;
      font-weight: 850;
      overflow-wrap: anywhere;
    }}
    .chain-pill.ok {{ border-color: #bee0d7; color: var(--accent-strong); background: #eef8f4; }}
    .chain-pill.warn {{ border-color: #eed9a9; color: var(--amber); background: #fff8e6; }}
    .chain-pill.danger {{ border-color: #f0c4c4; color: var(--danger); background: #fff3f3; }}
    .chain-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      justify-content: flex-end;
    }}
    .chain-action {{
      min-height: 26px;
      border: 1px solid #cbd6d2;
      border-radius: 8px;
      padding: 4px 8px;
      background: #fff;
      color: #26322f;
      font-size: 11px;
      font-weight: 850;
      cursor: pointer;
      white-space: nowrap;
    }}
    .chain-action:hover {{
      border-color: #9bc9bd;
      color: var(--accent-strong);
      background: #f4fbf8;
    }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }}
    .tag {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 11px;
      background: var(--panel-soft);
      color: #3e4744;
      font-weight: 650;
    }}
    .tag.level {{ border-color: #d6c9ee; color: var(--violet); background: #f5f1ff; }}
    .evidence {{
      margin: 12px 0;
      padding: 10px 12px;
      border: 1px solid #c8e1d9;
      border-left: 4px solid var(--accent);
      border-radius: 8px;
      background: #f4fbf8;
    }}
    .evidence-row {{ display: flex; gap: 8px; align-items: baseline; margin-top: 6px; }}
    .label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0; font-weight: 800; }}
    .copy-block {{ margin: 10px 0; color: #2b3431; }}
    .actions {{ display: grid; gap: 8px; align-content: start; }}
    button {{
      width: 100%;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      border-radius: 8px;
      padding: 9px 10px;
      text-align: center;
      cursor: pointer;
      font: inherit;
      font-weight: 730;
    }}
    button:hover {{ transform: translateY(-1px); }}
    button[data-action="confirm"] {{ border-color: var(--accent); color: #fff; background: var(--accent); }}
    button[data-action="delete"] {{ border-color: var(--danger); color: var(--danger); }}
    button[data-action="rewrite"], button[data-action="downgrade"] {{ border-color: #caa465; color: var(--amber); background: #fff9ec; }}
    textarea {{
      width: 100%;
      min-height: 84px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px 10px;
      font: inherit;
      resize: vertical;
      background: #fffefb;
    }}
    .right-stack {{
      display: grid;
      gap: 12px;
      align-content: start;
      max-height: calc(100vh - 96px);
      overflow: auto;
    }}
    body[data-review-focus="true"] .ops-strip,
    body[data-review-focus="true"] .workflow-band,
    body[data-review-focus="true"] .right-stack {{
      display: none;
    }}
    body[data-review-focus="true"] .console-shell {{
      width: min(1920px, 100%);
      grid-template-columns: minmax(0, 1fr);
      padding-top: 8px;
    }}
    body[data-review-focus="true"] .paper-review-stage {{
      gap: 12px;
    }}
    body[data-review-focus="true"] .pdf-reader-stage {{
      min-height: 820px;
      height: calc(100vh - 176px);
    }}
    body[data-review-focus="true"] .source-pdf-shell {{
      min-height: 820px;
    }}
    .review-inspector {{
      border-color: #b8d8cf;
      background: linear-gradient(180deg, #ffffff, #f8fcfb);
    }}
    .model-entry {{
      border-color: #b8d8cf;
      background: linear-gradient(180deg, #ffffff, #f6fbfa);
    }}
    .model-config-list {{
      display: grid;
      gap: 8px;
      padding: 12px 14px 0;
    }}
    .model-config-row {{
      display: grid;
      grid-template-columns: 84px minmax(0, 1fr);
      gap: 8px;
      align-items: baseline;
      font-size: 12px;
    }}
    .model-config-row span:first-child {{
      color: var(--muted);
      font-weight: 820;
    }}
    .model-config-row code {{
      overflow-wrap: anywhere;
      color: #24302d;
    }}
    .agent-review-box {{
      margin: 12px 14px 14px;
      display: grid;
      gap: 8px;
    }}
    .agent-flow {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
      padding: 12px 14px 0;
    }}
    .agent-flow-step {{
      min-height: 54px;
      border: 1px solid #d4ddd9;
      border-radius: 8px;
      background: #fff;
      padding: 8px;
    }}
    .agent-flow-index {{
      color: var(--accent-strong);
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 10px;
      font-weight: 900;
    }}
    .agent-flow-title {{
      margin-top: 3px;
      color: #24302d;
      font-size: 11px;
      font-weight: 850;
      line-height: 1.25;
    }}
    .agent-primary-button {{
      color: #fff;
      border-color: var(--accent);
      background: var(--accent);
    }}
    .agent-stream-box {{
      min-height: 112px;
      border: 1px solid #d4ddd9;
      border-radius: 8px;
      background: #132320;
      color: #dcece8;
      padding: 10px;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 11px;
      overflow: auto;
    }}
    .trace-list, .invocation-list {{
      padding: 12px 14px 16px;
      display: grid;
      gap: 10px;
      max-height: 360px;
      overflow: auto;
    }}
    .trace-event {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
    }}
    .trace-event[open] {{
      border-color: #b8d8cf;
      box-shadow: 0 12px 28px rgba(20, 35, 35, 0.08);
    }}
    .trace-event summary {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: start;
      padding: 10px;
      cursor: pointer;
      list-style: none;
    }}
    .trace-event summary > span {{ min-width: 0; }}
    .trace-event summary::-webkit-details-marker {{ display: none; }}
    .trace-event summary::after {{
      content: "展开";
      border: 1px solid #c8e1d9;
      border-radius: 999px;
      padding: 2px 7px;
      color: var(--accent-strong);
      background: #f4fbf8;
      font-size: 10px;
      font-weight: 850;
    }}
    .trace-event[open] summary::after {{ content: "收起"; }}
    .trace-audit-body {{
      display: grid;
      gap: 8px;
      padding: 0 10px 10px;
    }}
    .trace-audit-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }}
    .trace-audit-field {{
      border: 1px solid #e0e7e4;
      border-radius: 8px;
      background: #f8fbfa;
      padding: 8px;
      min-width: 0;
    }}
    .trace-audit-label {{
      color: #66736f;
      font-size: 10px;
      font-weight: 900;
    }}
    .trace-audit-value {{
      margin-top: 3px;
      color: #26322f;
      font-size: 11px;
      overflow-wrap: anywhere;
    }}
    .trace-chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin-top: 4px;
    }}
    .trace-chip {{
      border: 1px solid #c8e1d9;
      border-radius: 999px;
      padding: 2px 7px;
      background: #fff;
      color: var(--accent-strong);
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 10px;
      font-weight: 800;
    }}
    .evidence-focus {{
      padding: 12px 14px 16px;
      display: grid;
      gap: 10px;
    }}
    .focus-row {{
      border: 1px solid #c8e1d9;
      border-left: 4px solid var(--accent);
      border-radius: 8px;
      background: #f4fbf8;
      padding: 10px;
    }}
    .focus-title {{ font-weight: 780; font-size: 12px; }}
    .focus-meta {{ color: var(--muted); font-size: 11px; margin-top: 4px; overflow-wrap: anywhere; }}
    .artifact-list, .next-action-list {{
      padding: 12px 14px 16px;
      display: grid;
      gap: 10px;
    }}
    .artifact-row, .next-action-row {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      padding: 10px;
    }}
    .artifact-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: start;
    }}
    .artifact-name, .next-action-title {{ font-size: 12px; font-weight: 800; }}
    .artifact-path, .next-action-copy {{
      margin-top: 5px;
      color: var(--muted);
      font-size: 11px;
      overflow-wrap: anywhere;
    }}
    .inline-button, .trace-filter {{
      width: auto;
      min-height: 28px;
      border-radius: 999px;
      padding: 5px 9px;
      font-size: 11px;
      white-space: nowrap;
      background: #fff;
    }}
    .inline-button {{
      color: var(--accent-strong);
      border-color: #b8d8cf;
    }}
    .inline-button.primary {{
      color: #fff;
      border-color: var(--accent);
      background: var(--accent);
    }}
    .trace-toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 12px 14px 0;
    }}
    .trace-filter[aria-pressed="true"] {{
      color: #fff;
      border-color: var(--accent);
      background: var(--accent);
    }}
    .trace-empty {{
      display: none;
      margin: 0 14px 14px;
      padding: 10px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      color: var(--muted);
      font-size: 12px;
      background: #fff;
    }}
    .trace-empty[data-visible="true"] {{ display: block; }}
    .trace-event[data-status="started"], .trace-event[data-status="queued"] {{
      border-color: #c7d7ef;
      background: #f5f8fe;
    }}
    .trace-event[data-status="completed"] {{
      border-color: #c8e1d9;
      background: #f4fbf8;
    }}
    .trace-event[data-status="failed"] {{
      border-color: #e6b4b4;
      background: #fff5f5;
    }}
    .trace-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }}
    .trace-tool {{ font-weight: 760; font-size: 12px; overflow-wrap: anywhere; }}
    .status-pill {{
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 11px;
      font-weight: 800;
      white-space: nowrap;
      background: #fff;
      color: #46504d;
    }}
    .status-completed {{ border-color: #b8d8cf; color: var(--accent-strong); background: #eef8f4; }}
    .status-failed {{ border-color: #e6b4b4; color: var(--danger); background: #fff0f0; }}
    .status-approval_required {{ border-color: #e4c783; color: var(--amber); background: #fff8e6; }}
    .trace-summary {{ display: block; margin-top: 6px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }}
    .confirmation-note {{
      padding: 14px 16px 16px;
      color: #2f3835;
      font-size: 13px;
    }}
    .stream-console {{
      margin: 0 16px 16px;
      border-radius: 8px;
      background: var(--mono-panel);
      color: #e8ede9;
      padding: 12px;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      max-height: 190px;
      overflow: auto;
    }}
    .stream-console div + div {{ margin-top: 5px; }}
    .stream-key {{ color: #9ad0c4; }}
    .agent-toast {{
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 5;
      max-width: min(360px, calc(100vw - 36px));
      border: 1px solid #b8d8cf;
      border-radius: 8px;
      background: #132320;
      color: #eef8f4;
      padding: 10px 12px;
      box-shadow: 0 18px 42px rgba(20, 35, 35, 0.24);
      font-size: 12px;
      opacity: 0;
      pointer-events: none;
      transform: translateY(8px);
      transition: opacity 160ms ease, transform 160ms ease;
    }}
    .agent-toast[data-visible="true"] {{
      opacity: 1;
      transform: translateY(0);
    }}
    .empty {{ padding: 40px 0; color: var(--muted); }}
    @media (max-width: 1100px) {{
      .ops-strip {{ grid-template-columns: 1fr 1fr; }}
      .workflow-head {{ grid-template-columns: 1fr; }}
      .workflow-steps {{ grid-template-columns: 1fr; }}
      .agent-stage-board {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
      .console-shell {{ grid-template-columns: 1fr; }}
      .paper-canvas {{ grid-template-columns: 1fr; }}
      .paper-comments {{ grid-template-columns: 1fr 1fr; }}
      .source-pdf-shell {{ min-height: 620px; }}
      .pdf-reader-stage {{ min-height: 580px; height: 70vh; }}
      .pdf-reading-map {{ grid-template-columns: 1fr; }}
      .pdf-agent-dock {{ grid-template-columns: 1fr 1fr; }}
      .pdf-agent-card:first-child {{ grid-column: 1 / -1; }}
      .pdf-agent-context-list {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .right-stack {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 760px) {{
      .topbar {{ grid-template-columns: 1fr; }}
      .ops-strip {{ width: calc(100% - 24px); grid-template-columns: 1fr; }}
      .workflow-band {{ width: calc(100% - 24px); }}
      .console-shell {{ padding: 12px; }}
      .paper-canvas {{ padding: 12px; }}
      .paper-sheet {{ min-height: 420px; padding: 28px 28px 28px 38px; }}
      .source-pdf-shell {{ min-height: 520px; }}
      .pdf-reader-bar {{ grid-template-columns: 1fr; justify-items: center; }}
      .pdf-page-rail {{ padding: 8px; }}
      .pdf-reading-map {{ grid-template-columns: 1fr; }}
      .pdf-review-route {{ grid-template-columns: 1fr; }}
      .pdf-search-strip {{ grid-template-columns: 1fr; }}
      .pdf-search-actions {{ justify-content: flex-start; }}
      .pdf-review-command-strip {{ grid-template-columns: 1fr; }}
      .pdf-command-actions {{ justify-content: flex-start; }}
      .pdf-agent-dock {{ grid-template-columns: 1fr; }}
      .pdf-agent-phase-rail {{ grid-template-columns: 1fr 1fr; }}
      .pdf-agent-context-list {{ grid-template-columns: 1fr 1fr; }}
      .pdf-tool-trace-list {{ grid-template-columns: 1fr; }}
      .pdf-recovery-strip {{ grid-template-columns: 1fr; }}
      .pdf-recovery-actions {{ justify-content: flex-start; flex-wrap: wrap; }}
      .pdf-runtime-pulse {{ grid-template-columns: 1fr 1fr; }}
      .pdf-human-gate {{ grid-template-columns: 1fr; }}
      .pdf-human-gate-grid {{ grid-template-columns: 1fr 1fr; }}
      .pdf-human-gate-actions {{ justify-content: flex-start; }}
      .pdf-evidence-gate {{ grid-template-columns: 1fr; }}
      .pdf-evidence-gate-grid {{ grid-template-columns: 1fr 1fr; }}
      .pdf-evidence-gate-actions {{ justify-content: flex-start; }}
      .pdf-reader-stage {{ min-height: 480px; height: 68vh; padding: 14px; }}
      .agent-stage-board {{ grid-template-columns: 1fr 1fr; }}
      .paper-comments {{ grid-template-columns: 1fr; }}
      .agent-flow {{ grid-template-columns: 1fr 1fr; }}
      .item {{ grid-template-columns: 1fr; }}
      .queue-toolbar {{ grid-template-columns: 1fr; }}
      .queue-filterbar {{ grid-template-columns: 1fr; }}
      .queue-result-count {{ white-space: normal; }}
      .evidence-chain-row {{
        grid-template-columns: minmax(0, 1fr) 68px 72px 86px;
      }}
      .evidence-chain-row.header span:nth-child(n+5),
      .evidence-chain-row:not(.header) > span:nth-child(n+5),
      .evidence-chain-row:not(.header) > div:nth-child(n+5) {{
        display: none;
      }}
      .right-stack {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body data-peerassist-agent-console data-review-focus="false">
  <header class="topbar" data-stream-state="connecting">
    <div class="brand">
      <div class="mark">PA</div>
      <div>
        <h1>PeerAssist 论文审核辅助台</h1>
        <div class="subtitle">面向 <code>{html.escape(paper_id)}</code> 的智能体证据流、人工确认与可追溯审稿工作台</div>
      </div>
    </div>
    <div class="meta runtime-strip">
      <span class="mode-badge">{_localized_mode(mode)}模式</span>
      <span class="stream-chip"><span class="stream-dot"></span><span id="stream-status">正在连接事件流</span></span>
      <span id="runtime-status">{len(events)} 条追踪事件</span>
    </div>
  </header>
  <section class="ops-strip" data-agent-ops-strip>
    <div class="ops-card primary">
      <div class="ops-kicker">运行态指挥条</div>
      <div class="ops-value">{html.escape(_localized_mode(mode))}审稿</div>
      <div class="ops-label">事件流、证据队列与人工闸门同步展示 · {html.escape(runtime_health)}</div>
    </div>
    <div class="ops-card">
      <div class="ops-kicker">待人工确认</div>
      <div class="ops-value">{pending_count}</div>
      <div class="ops-label">逐条处理</div>
    </div>
    <div class="ops-card">
      <div class="ops-kicker">证据焦点</div>
      <div class="ops-value">{evidence_count}</div>
      <div class="ops-label">绑定证据条目</div>
    </div>
    <div class="ops-card">
      <div class="ops-kicker">完成调用</div>
      <div class="ops-value">{completed_event_count}</div>
      <div class="ops-label">工具生命周期</div>
    </div>
    <div class="ops-card">
      <div class="ops-kicker">失败调用</div>
      <div class="ops-value">{failed_event_count}</div>
      <div class="ops-label">需要复核</div>
    </div>
  </section>
  <section class="workflow-band" data-panel="session-navigator" data-agent-workflow>
    <div class="workflow-head">
      <div>
        <p class="workflow-kicker">审稿流程</p>
        <div class="workflow-copy">从证据队列进入，沿工具追踪核对来源，最后由审稿人逐条确认或改写。</div>
      </div>
      <div class="workflow-meter" data-review-progress>
        <span>人工确认完成度</span>
        <strong>{review_progress}%</strong>
        <div class="workflow-track"><span style="width: {review_progress}%"></span></div>
      </div>
    </div>
    <nav class="workflow-steps" aria-label="PeerAssist 审稿流程">
      <a class="workflow-step" href="#review-queue"><span class="workflow-index">01</span><span><span class="workflow-title">证据队列</span><span class="workflow-detail">定位关注点、证据与良性解释</span></span></a>
      <a class="workflow-step" href="#tool-trace"><span class="workflow-index">02</span><span><span class="workflow-title">调用追踪</span><span class="workflow-detail">核对 MCP、Skills 与内置能力生命周期</span></span></a>
      <a class="workflow-step" href="#human-confirmation"><span class="workflow-index">03</span><span><span class="workflow-title">人工闸门</span><span class="workflow-detail">确认、改写、降级、删除或暂挂</span></span></a>
    </nav>
    {_render_agent_stage_board(stage_lifecycle)}
  </section>
  <main class="console-shell">
    <aside class="rail">
      <section class="panel" data-panel="run-summary">
        <div class="panel-header">
          <p class="panel-title">运行摘要</p>
          <div class="panel-subtitle">从 PeerAssist 产物恢复的当前状态</div>
        </div>
        <div class="metric-grid">
          <div class="metric"><div class="metric-value">{pending_count}</div><div class="metric-label">待确认项</div></div>
          <div class="metric"><div class="metric-value">{actions_count}</div><div class="metric-label">已记录动作</div></div>
          <div class="metric"><div class="metric-value">{len(agent_runs)}</div><div class="metric-label">代理运行</div></div>
          <div class="metric"><div class="metric-value">{len(events)}</div><div class="metric-label">追踪事件</div></div>
        </div>
        <div class="run-map" data-agent-runtime-strip>
          <div class="run-map-row"><span>证据链</span><span class="run-map-bar" style="width: {min(100, max(18, len(events) * 8))}%"></span></div>
          <div class="run-map-row"><span>代理层</span><span class="run-map-bar" style="width: {min(100, max(18, len(agent_runs) * 18))}%"></span></div>
          <div class="run-map-row"><span>人工队列</span><span class="run-map-bar" style="width: {min(100, max(18, pending_count * 24))}%"></span></div>
        </div>
      </section>
      <section class="panel" data-panel="next-actions">
        <div class="panel-header">
          <p class="panel-title">下一步动作</p>
          <div class="panel-subtitle">按当前运行态给出审稿人操作提示</div>
        </div>
        {_render_next_actions(pending_count=pending_count, failed_event_count=failed_event_count, actions_count=actions_count)}
      </section>
      <section class="panel" data-panel="agent-runs">
        <div class="panel-header">
          <p class="panel-title">代理时间线</p>
          <div class="panel-subtitle">草稿、警告与未完成核查</div>
        </div>
        {_render_agent_timeline(agent_runs)}
      </section>
    </aside>
    <section class="paper-review-stage" data-panel="paper-review-stage">
      {_render_paper_review_surface(items, paper_id=paper_id, evidence_preview=evidence_preview, has_source_pdf=source_pdf_path is not None, events=events)}
      {_render_evidence_chain_matrix(items, events=events, invocations=invocations)}
      <section class="panel queue" data-panel="review-queue" id="review-queue">
        <div class="panel-header">
          <p class="panel-title">证据审稿队列</p>
          <div class="panel-subtitle">逐条确认、改写、降级、删除或暂挂关注点</div>
        </div>
        <div class="queue-toolbar" data-review-progress>
          <div>
            <span class="label">审核进度</span>
            <div class="queue-progress-line">已记录 {actions_count} 个动作，当前仍有 {pending_count} 条待审稿人处理。</div>
          </div>
          <div class="compact-meter" aria-label="人工确认完成度"><span style="width: {review_progress}%"></span></div>
        </div>
        <div class="queue-filterbar" data-queue-filterbar>
          <div class="queue-segmented" aria-label="审稿队列筛选">
            <button class="queue-filter-button" type="button" data-queue-filter="all" aria-pressed="true">全部</button>
            <button class="queue-filter-button" type="button" data-queue-filter="current-page" aria-pressed="false">当前页</button>
            <button class="queue-filter-button" type="button" data-queue-filter="major" aria-pressed="false">主要问题</button>
            <button class="queue-filter-button" type="button" data-queue-filter="clarification" aria-pressed="false">需澄清</button>
            <button class="queue-filter-button" type="button" data-queue-filter="pdf" aria-pressed="false">有 PDF 证据</button>
          </div>
          <input class="queue-search" type="search" data-queue-search placeholder="搜索队列" aria-label="搜索审稿队列">
          <div class="queue-result-count" data-queue-result-count>{len(items)} 条关注点</div>
        </div>
        <div class="queue-body">{rows}</div>
        <div class="queue-empty" data-queue-empty hidden>当前筛选条件下暂无审稿关注点。</div>
      </section>
    </section>
    <aside class="right-stack">
      {_render_model_entry(model_config)}
      <section class="panel review-inspector" data-panel="review-inspector">
        <div class="panel-header">
          <p class="panel-title">页边审稿意见</p>
          <div class="panel-subtitle">模型与确定性线索生成的待确认批注，点击可定位队列或原文证据</div>
        </div>
        <div class="paper-comments" aria-label="页边审稿意见">
          {_render_margin_comments([item for item in items if isinstance(item, dict)], evidence_preview=evidence_preview)}
        </div>
      </section>
      <section class="panel" data-panel="evidence-focus">
        <div class="panel-header">
          <p class="panel-title">证据焦点</p>
          <div class="panel-subtitle">优先核对当前队列绑定的证据位置</div>
        </div>
        {_render_evidence_focus(items)}
      </section>
      <section class="panel" data-panel="artifact-workspace">
        <div class="panel-header">
          <p class="panel-title">产物工作区</p>
          <div class="panel-subtitle">队列、人工确认、代理结果与 trace 文件</div>
        </div>
        {_render_artifact_workspace(paths)}
      </section>
      <section class="panel" data-panel="tool-trace" id="tool-trace">
        <div class="panel-header">
          <p class="panel-title">工具追踪</p>
          <div class="panel-subtitle">MCP、Skills 与内置能力调用生命周期</div>
        </div>
        {_render_trace_filters(events)}
        {_render_trace_events(events)}
        <div class="trace-empty" data-trace-empty>当前过滤条件下暂无工具事件。</div>
      </section>
      <section class="panel" data-panel="human-confirmation" id="human-confirmation">
        <div class="panel-header">
          <p class="panel-title">人工确认</p>
          <div class="panel-subtitle">没有证据的关注点不会进入确认报告</div>
        </div>
        <div class="confirmation-note">
          审稿人的每次编辑都会写入 <code>human_confirmations.json</code>，随后重新生成中文与英文报告。刷新页面时会从磁盘恢复队列、动作、路径和工具追踪状态。
        </div>
        {_render_stream_console(events_count=len(events), actions_count=actions_count)}
        {_render_invocations(invocations)}
      </section>
    </aside>
  </main>
  <div class="agent-toast" id="agent-toast" role="status" aria-live="polite"></div>
  <script type="application/json" id="peerassist-state">{state_json}</script>
  <script>
    async function submitDecision(button) {{
      const directConcernId = button.dataset.concernId || '';
      const item = button.closest('.queue-body .item[data-concern-id]')
        || (directConcernId ? document.querySelector(`.queue-body .item[data-concern-id="${{CSS.escape(directConcernId)}}"]`) : null);
      if (!item) throw new Error('未找到对应审稿队列项');
      const text = item.querySelector('textarea').value;
      const action = button.dataset.action;
      const payload = {{
        concern_id: item.dataset.concernId,
        action,
        reviewer_id: 'local-reviewer',
        timestamp: new Date().toISOString(),
        previous_text: item.dataset.previousText || '',
        new_text: text,
        reason: action === 'rewrite' || action === 'downgrade' ? '在 PeerAssist 论文审核辅助台中编辑。' : ''
      }};
      const response = await fetch('/api/decision', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(payload)
      }});
      if (!response.ok) throw new Error(await response.text());
      window.location.reload();
    }}
    function openConcernEditor(concernId, pdfPage) {{
      focusAnnotation(concernId, '', 'queue', pdfPage || '');
      function markPdfAnnotationActive() {{
        const pdfAnnotation = concernId ? document.querySelector(`[data-pdf-annotation-card="${{CSS.escape(concernId)}}"]`) : null;
        if (pdfAnnotation) pdfAnnotation.dataset.active = 'true';
      }}
      markPdfAnnotationActive();
      window.setTimeout(markPdfAnnotationActive, 900);
      window.setTimeout(() => {{
        const item = concernId ? document.querySelector(`.queue-body .item[data-concern-id="${{CSS.escape(concernId)}}"]`) : null;
        const editor = item?.querySelector('textarea');
        if (editor) {{
          editor.focus();
          editor.select();
          showToast('已打开该条审稿意见编辑框');
        }}
      }}, 450);
    }}
    async function refreshRuntime() {{
      const response = await fetch('/api/state');
      if (!response.ok) return;
      const state = await response.json();
      updateRuntime(state, 'poll');
    }}
    function escapeHtml(value) {{
      return String(value).replace(/[&<>"']/g, (char) => ({{
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }}[char]));
    }}
    function appendPdfActivityLine(kind, copy) {{
      const feed = document.querySelector('[data-pdf-activity-list]');
      const empty = document.querySelector('[data-pdf-activity-empty]');
      if (!feed) return;
      const row = document.createElement('div');
      row.className = 'pdf-activity-row';
      row.innerHTML = `<span class="pdf-activity-kind">${{escapeHtml(kind)}}</span><span class="pdf-activity-copy">${{escapeHtml(copy)}}</span>`;
      feed.prepend(row);
      if (empty) empty.hidden = true;
      while (feed.children.length > 4) {{
        feed.removeChild(feed.lastElementChild);
      }}
    }}
    function appendStreamLine(kind, copy) {{
      const consoleEl = document.querySelector('[data-stream-log]');
      appendPdfActivityLine(kind, copy);
      if (consoleEl) {{
        const row = document.createElement('div');
        row.innerHTML = `<span class="stream-key">${{escapeHtml(kind)}}：</span>${{escapeHtml(copy)}}`;
        consoleEl.prepend(row);
        while (consoleEl.children.length > 6) {{
          consoleEl.removeChild(consoleEl.lastElementChild);
        }}
      }}
    }}
    let toastTimer = null;
    function showToast(message) {{
      const toast = document.getElementById('agent-toast');
      if (!toast) return;
      toast.textContent = message;
      toast.dataset.visible = 'true';
      window.clearTimeout(toastTimer);
      toastTimer = window.setTimeout(() => {{
        toast.dataset.visible = 'false';
      }}, 2200);
    }}
    async function copyText(value) {{
      if (navigator.clipboard && window.isSecureContext) {{
        await navigator.clipboard.writeText(value);
        return;
      }}
      const textarea = document.createElement('textarea');
      textarea.value = value;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      textarea.remove();
    }}
    window.peerassistSelectedEvidence = {{text: '', page: null}};
    function setPdfSelectionEvidence(payload) {{
      const text = String(payload?.text || '').replace(/\\s+/g, ' ').trim();
      const page = Number(payload?.page || 0);
      const rect = payload?.rect || null;
      const tray = document.querySelector('[data-pdf-selection-tray]');
      const quote = document.querySelector('[data-pdf-selection-quote]');
      const meta = document.querySelector('[data-pdf-selection-meta]');
      const stage = document.querySelector('[data-pdf-stage]');
      const popover = document.querySelector('[data-pdf-selection-popover]');
      const popoverQuote = document.querySelector('[data-pdf-selection-popover-quote]');
      const popoverMeta = document.querySelector('[data-pdf-selection-popover-meta]');
      window.peerassistSelectedEvidence = {{text, page: page > 0 ? page : null}};
      if (!text) {{
        if (tray) tray.hidden = true;
        if (quote) quote.textContent = '';
        if (meta) meta.textContent = '尚未选择 PDF 文字';
        if (popover) {{
          popover.dataset.visible = 'false';
          popover.removeAttribute('style');
        }}
        updatePdfAgentContext();
        return;
      }}
      const clippedTrayText = text.length > 260 ? `${{text.slice(0, 260)}}...` : text;
      const clippedPopoverText = text.length > 180 ? `${{text.slice(0, 180)}}...` : text;
      const metaCopy = page > 0 ? `PDF 第 ${{page}} 页选区 · ${{text.length}} 字` : `PDF 选区 · ${{text.length}} 字`;
      if (tray && quote && meta) {{
        tray.hidden = false;
        quote.textContent = clippedTrayText;
        meta.textContent = metaCopy;
      }}
      if (popover && popoverQuote && popoverMeta) {{
        popoverQuote.textContent = clippedPopoverText;
        popoverMeta.textContent = metaCopy;
        popover.dataset.visible = 'true';
        if (stage && rect) {{
          const stageRect = stage.getBoundingClientRect();
          const popoverWidth = 340;
          const left = Math.max(
            12,
            Math.min(
              stage.clientWidth - Math.min(popoverWidth, stage.clientWidth - 28) - 12,
              Number(rect.left || 0) - stageRect.left + stage.scrollLeft
            )
          );
          const top = Math.max(
            12,
            Number(rect.bottom || 0) - stageRect.top + stage.scrollTop + 10
          );
          popover.style.left = `${{Math.round(left)}}px`;
          popover.style.top = `${{Math.round(top)}}px`;
        }}
      }}
      updatePdfAgentContext();
    }}
    window.peerassistSetPdfSelection = setPdfSelectionEvidence;
    function setReviewFocusMode(enabled) {{
      const isEnabled = Boolean(enabled);
      document.body.dataset.reviewFocus = String(isEnabled);
      document.querySelectorAll('[data-focus-review-toggle]').forEach((button) => {{
        button.setAttribute('aria-pressed', String(isEnabled));
        button.textContent = isEnabled ? '退出专注' : '专注审稿';
      }});
      window.setTimeout(() => {{
        window.peerassistPdfRenderCurrentPage?.();
      }}, 180);
      showToast(isEnabled ? '已进入专注审稿模式' : '已退出专注审稿模式');
    }}
    window.peerassistSetReviewFocusMode = setReviewFocusMode;
    function localizedTraceStatus(status) {{
      const labels = {{
        queued: '已排队',
        started: '运行中',
        completed: '已完成',
        failed: '失败',
        approval_required: '需审批',
        cancelled: '已取消',
        unknown: '未知'
      }};
      return labels[String(status || '').toLowerCase()] || String(status || '未知');
    }}
    function renderPdfToolTraceCard(event) {{
      const status = String(event?.status || 'unknown').replace(/\\s+/g, '_').toLowerCase();
      const tool = String(event?.tool || event?.call_id || 'tool');
      const agent = String(event?.agent_id || 'peerassist');
      const summary = String(event?.output_summary || event?.input_summary || event?.error_code || '等待输出摘要').slice(0, 120);
      const article = document.createElement('article');
      article.className = 'pdf-tool-trace-card';
      article.dataset.pdfToolTraceCard = 'true';
      article.dataset.status = status;
      article.innerHTML = `
        <div class="pdf-tool-trace-top">
          <span class="pdf-tool-trace-name">${{escapeHtml(tool)}}</span>
          <span class="pdf-tool-trace-status">${{escapeHtml(localizedTraceStatus(status))}}</span>
        </div>
        <div class="pdf-tool-trace-copy">${{escapeHtml(agent)}} · ${{escapeHtml(summary)}}</div>
      `;
      return article;
    }}
    function updatePdfToolTrace(state) {{
      const list = document.querySelector('[data-pdf-tool-trace-list]');
      if (!list) return;
      const events = Array.isArray(state?.tool_trace?.events) ? state.tool_trace.events : [];
      const latest = events.filter((event) => event && typeof event === 'object').slice(-3).reverse();
      list.replaceChildren();
      if (latest.length === 0) {{
        const empty = document.createElement('div');
        empty.className = 'pdf-tool-trace-empty';
        empty.dataset.pdfToolTraceEmpty = 'true';
        empty.textContent = '等待工具调用事件。';
        list.appendChild(empty);
        return;
      }}
      latest.forEach((event) => list.appendChild(renderPdfToolTraceCard(event)));
    }}
    window.peerassistUpdatePdfToolTrace = updatePdfToolTrace;
    function compactRecoveryValue(value, fallback = '未记录') {{
      const copy = String(value || '').trim();
      return copy ? (copy.length > 48 ? `${{copy.slice(0, 48)}}...` : copy) : fallback;
    }}
    function recoveryStateFromStatus(status) {{
      const normalized = String(status || '').replace(/\\s+/g, '_').toLowerCase();
      if (normalized === 'failed') return 'failed';
      if (['queued', 'started', 'running', 'progress', 'approval_required'].includes(normalized)) return 'active';
      if (normalized === 'completed') return 'completed';
      if (normalized === 'cancelled') return 'cancelled';
      return 'idle';
    }}
    function updatePdfRecoveryCheckpoint(state = null) {{
      const strip = document.querySelector('[data-pdf-recovery-strip]');
      if (!strip) return;
      const events = Array.isArray(state?.tool_trace?.events)
        ? state.tool_trace.events.filter((event) => event && typeof event === 'object')
        : [];
      const latest = events[events.length - 1] || null;
      const failedCount = events.filter((event) => {{
        return String(event?.status || '').replace(/\\s+/g, '_').toLowerCase() === 'failed';
      }}).length;
      const artifacts = events.flatMap((event) => Array.isArray(event?.artifact_ids) ? event.artifact_ids : []);
      const status = latest?.status || (events.length > 0 ? 'completed' : 'idle');
      strip.dataset.recoveryState = recoveryStateFromStatus(status);
      const setValue = (key, value) => {{
        const node = strip.querySelector(`[data-pdf-recovery-value="${{CSS.escape(key)}}"]`);
        if (node) node.textContent = value;
      }};
      setValue('status', latest ? `${{localizedTraceStatus(status)}} · ${{compactRecoveryValue(latest.tool || latest.call_id || 'tool')}}` : '等待首个检查点');
      setValue('call', latest ? compactRecoveryValue(latest.call_id || latest.task_id || latest.agent_id) : '尚无调用');
      setValue('failed', `${{failedCount}} 个失败事件`);
      setValue('artifact', compactRecoveryValue(artifacts[artifacts.length - 1], '暂无产物'));
      strip.querySelectorAll('[data-pdf-recovery-failed]').forEach((button) => {{
        button.disabled = failedCount === 0;
      }});
    }}
    window.peerassistUpdatePdfRecoveryCheckpoint = updatePdfRecoveryCheckpoint;
    function setPdfAgentContextItem(key, value, status, state) {{
      const row = document.querySelector(`[data-pdf-agent-context-item="${{CSS.escape(key)}}"]`);
      const valueEl = document.querySelector(`[data-pdf-agent-context-value="${{CSS.escape(key)}}"]`);
      const statusEl = document.querySelector(`[data-pdf-agent-context-status="${{CSS.escape(key)}}"]`);
      if (row && state) row.dataset.contextState = state;
      if (valueEl && value) valueEl.textContent = value;
      if (statusEl && status) statusEl.textContent = status;
    }}
    function isHumanResolvedQueueStatus(status) {{
      return ['confirmed', 'rewritten', 'downgraded', 'deleted'].includes(String(status || '').toLowerCase());
    }}
    function updatePdfAgentContext(state = null) {{
      const queueItems = Array.from(document.querySelectorAll('.queue-body .item[data-concern-id]'));
      const queueCount = queueItems.length;
      const pendingFromDom = queueItems.filter((item) => !isHumanResolvedQueueStatus(item.dataset.concernStatus)).length;
      const pendingCount = Number(state?.pending_count ?? pendingFromDom);
      const evidenceAnchors = document.querySelectorAll('[data-evidence-anchor]').length;
      const runtime = state?.runtime || {{}};
      const traceEvents = Array.isArray(state?.tool_trace?.events) ? state.tool_trace.events.length : 0;
      const toolEventCount = Number(runtime.tool_event_count ?? traceEvents);
      const selected = window.peerassistSelectedEvidence || {{}};
      const selectedText = String(selected.text || '');
      const selectedPage = selected.page ? `第 ${{selected.page}} 页` : 'PDF 选区';
      const currentPage = Number(window.peerassistCurrentPdfPage || 0);
      setPdfAgentContextItem(
        'pdf',
        currentPage > 0 ? `正在核对第 ${{currentPage}} 页` : '已导入，可浏览与选区',
        '主阅读面',
        'ready'
      );
      setPdfAgentContextItem(
        'evidence',
        `${{evidenceAnchors || queueCount}} 个证据锚点 · ${{queueCount}} 条关注`,
        '进入模型前绑定位置',
        queueCount > 0 || evidenceAnchors > 0 ? 'ready' : 'waiting'
      );
      setPdfAgentContextItem(
        'checks',
        '数值/统计/引用线索',
        '先规则后模型',
        'ready'
      );
      setPdfAgentContextItem(
        'tools',
        `${{toolEventCount || 0}} 条可追溯事件`,
        toolEventCount > 0 ? 'MCP/Skills 审计可展开' : '等待工具调用',
        toolEventCount > 0 ? 'ready' : 'waiting'
      );
      setPdfAgentContextItem(
        'selection',
        selectedText ? `${{selectedPage}} · ${{selectedText.length}} 字` : '未选择 PDF 文本',
        selectedText ? '选区将作为额外关注点' : '可选关注点',
        selectedText ? 'active' : 'waiting'
      );
      setPdfAgentContextItem(
        'human',
        `${{pendingCount}} 条待确认 · ${{queueCount}} 条队列`,
        pendingCount > 0 ? '等待审稿人逐条处理' : '当前无待确认项',
        pendingCount > 0 ? 'active' : 'ready'
      );
    }}
    window.peerassistUpdatePdfAgentContext = updatePdfAgentContext;
    function updatePdfHumanGate(state = null) {{
      const gate = document.querySelector('[data-pdf-human-gate]');
      if (!gate) return;
      const queueItems = Array.from(document.querySelectorAll('.queue-body .item[data-concern-id]'));
      const total = queueItems.length;
      const resolved = queueItems.filter((item) => isHumanResolvedQueueStatus(item.dataset.concernStatus)).length;
      const pendingFromDom = Math.max(0, total - resolved);
      const pending = Number(state?.pending_count ?? pendingFromDom);
      const majorPending = queueItems.filter((item) => {{
        return !isHumanResolvedQueueStatus(item.dataset.concernStatus)
          && String(item.dataset.concernLevel || '').toLowerCase().includes('major');
      }}).length;
      const completion = total > 0 ? Math.round(resolved / total * 100) : 0;
      const setValue = (key, value) => {{
        const node = gate.querySelector(`[data-pdf-human-gate-value="${{CSS.escape(key)}}"]`);
        if (node) node.textContent = value;
      }};
      setValue('pending', `${{pending}} 条`);
      setValue('resolved', `${{resolved}} 条`);
      setValue('major', `${{majorPending}} 条`);
      setValue('total', `${{total}} 条`);
      const copy = gate.querySelector('[data-pdf-human-gate-copy]');
      if (copy) {{
        copy.textContent = total > 0
          ? `人工确认进度 ${{completion}}% · 每条意见需确认、改写、降级、删除或暂挂`
          : '等待队列同步';
      }}
      const meter = gate.querySelector('[data-pdf-human-gate-meter]');
      if (meter) meter.style.width = `${{completion}}%`;
      gate.dataset.humanGateState = pending > 0 ? 'active' : total > 0 ? 'completed' : 'empty';
      gate.querySelectorAll('[data-pdf-human-gate-next]').forEach((button) => {{
        button.disabled = pending <= 0;
      }});
    }}
    window.peerassistUpdatePdfHumanGate = updatePdfHumanGate;
    function updatePdfEvidenceGate() {{
      const gate = document.querySelector('[data-pdf-evidence-gate]');
      if (!gate) return;
      const queueItems = Array.from(document.querySelectorAll('.queue-body .item[data-concern-id]'));
      const total = queueItems.length;
      const bound = queueItems.filter((item) => Number(item.dataset.pdfPage || 0) > 0).length;
      const unbound = Math.max(0, total - bound);
      const anchors = document.querySelectorAll('[data-evidence-anchor]').length;
      const ratio = total > 0 ? Math.round(bound / total * 100) : 0;
      const setValue = (key, value) => {{
        const node = gate.querySelector(`[data-pdf-evidence-gate-value="${{CSS.escape(key)}}"]`);
        if (node) node.textContent = value;
      }};
      setValue('total', `${{total}} 条`);
      setValue('bound', `${{bound}} 条`);
      setValue('unbound', `${{unbound}} 条`);
      setValue('anchors', `${{anchors}} 个`);
      const copy = gate.querySelector('[data-pdf-evidence-gate-copy]');
      if (copy) {{
        copy.textContent = unbound > 0
          ? `${{unbound}} 条缺少 PDF 页码绑定，进入确定性结论前需人工核查`
          : total > 0
            ? `证据绑定率 ${{ratio}}% · 可从证据链矩阵复核来源`
            : '等待证据台账同步';
      }}
      const meter = gate.querySelector('[data-pdf-evidence-gate-meter]');
      if (meter) meter.style.width = `${{ratio}}%`;
      gate.dataset.evidenceGateState = unbound > 0 ? 'review' : total > 0 ? 'ready' : 'empty';
      gate.querySelectorAll('[data-pdf-evidence-gate-unbound]').forEach((button) => {{
        button.disabled = unbound <= 0;
      }});
    }}
    window.peerassistUpdatePdfEvidenceGate = updatePdfEvidenceGate;
    function focusFirstUnboundEvidenceConcern() {{
      const item = Array.from(document.querySelectorAll('.queue-body .item[data-concern-id]')).find((node) => {{
        return Number(node.dataset.pdfPage || 0) <= 0;
      }});
      if (!item) {{
        showToast('暂无缺少 PDF 证据绑定的关注点');
        return;
      }}
      applyQueueFilter('all');
      focusAnnotation(item.dataset.concernId || '', '', 'queue', '');
      showToast('已定位到待人工核查的无页码绑定关注点');
    }}
    window.peerassistFocusFirstUnboundEvidenceConcern = focusFirstUnboundEvidenceConcern;
    function updatePdfRuntimePulse(state, source) {{
      const pulse = document.querySelector('[data-pdf-runtime-pulse]');
      if (!pulse) return;
      const runtime = state.runtime || {{}};
      pulse.dataset.streamSource = source === 'stream' ? 'stream' : 'poll';
      const stateEl = pulse.querySelector('[data-pdf-runtime-state]');
      const pendingEl = pulse.querySelector('[data-pdf-runtime-pending]');
      const eventsEl = pulse.querySelector('[data-pdf-runtime-events]');
      if (stateEl) stateEl.textContent = source === 'stream' ? '事件流已连接' : '轮询兜底中';
      if (pendingEl) pendingEl.textContent = `${{state.pending_count || 0}} 条`;
      if (eventsEl) eventsEl.textContent = `${{runtime.tool_event_count || 0}} 条`;
      updatePdfAgentContext(state);
      updatePdfRecoveryCheckpoint(state);
      updatePdfHumanGate(state);
      updatePdfEvidenceGate();
    }}
    window.peerassistUpdatePdfRuntimePulse = updatePdfRuntimePulse;
    function reviewModeLabel(mode) {{
      const labels = {{fast: '快速', standard: '标准', deep: '深度'}};
      return labels[mode] || labels.fast;
    }}
    function setPdfAgentPhase(phase, detail, kind = '') {{
      const phases = ['prepare', 'evidence', 'model', 'queue', 'human'];
      const activePhase = phases.includes(phase) ? phase : 'prepare';
      const activeIndex = phases.indexOf(activePhase);
      document.querySelectorAll('[data-pdf-agent-phase-rail]').forEach((rail) => {{
        rail.dataset.activePhase = activePhase;
      }});
      document.querySelectorAll('[data-pdf-agent-phase]').forEach((step) => {{
        const index = phases.indexOf(step.dataset.pdfAgentPhase || '');
        const state = index < activeIndex ? 'completed' : index === activeIndex ? 'active' : 'pending';
        step.dataset.phaseState = state;
        if (index === activeIndex && detail) {{
          const copy = step.querySelector('[data-pdf-agent-phase-copy]');
          if (copy) copy.textContent = detail;
        }}
      }});
      if (kind && detail) {{
        appendPdfActivityLine(kind, detail);
      }}
    }}
    window.peerassistSetPdfAgentPhase = setPdfAgentPhase;
    function setPdfAgentReviewMode(mode, quiet = false) {{
      const nextMode = ['fast', 'standard', 'deep'].includes(mode) ? mode : 'fast';
      window.peerassistReviewMode = nextMode;
      document.querySelectorAll('[data-pdf-agent-dock]').forEach((dock) => {{
        dock.dataset.reviewMode = nextMode;
      }});
      document.querySelectorAll('[data-pdf-agent-mode]').forEach((button) => {{
        button.setAttribute('aria-pressed', String(button.dataset.pdfAgentMode === nextMode));
      }});
      document.querySelectorAll('[data-pdf-agent-mode-label]').forEach((label) => {{
        label.textContent = reviewModeLabel(nextMode);
      }});
      setPdfAgentPhase('prepare', `已选择${{reviewModeLabel(nextMode)}}模式`, quiet ? '' : 'mode');
    }}
    window.peerassistSetPdfAgentReviewMode = setPdfAgentReviewMode;
    function handlePdfReviewCommand(command) {{
      if (command === 'agent-review') {{
        setPdfAgentPhase('evidence', '正在组装全篇审稿上下文', 'agent');
        document.querySelector('[data-agent-review-start]')?.click();
        document.querySelector('[data-panel="model-entry"]')?.scrollIntoView({{behavior: 'smooth', block: 'center'}});
        showToast('已从 PDF 命令条启动全篇审稿');
        return;
      }}
      if (command === 'selection-review') {{
        setPdfAgentPhase('prepare', '等待 PDF 选区进入审稿上下文', 'agent');
        document.querySelector('[data-pdf-selection-use]')?.click();
        return;
      }}
      if (command === 'current-page') {{
        setPdfAgentPhase('human', '正在聚焦当前页人工确认队列', 'agent');
        document.querySelector('[data-pdf-page-filter-current]')?.click();
        return;
      }}
      if (command === 'next-pending') {{
        document.querySelector('[data-pdf-page-next-pending]')?.click();
        return;
      }}
      if (command === 'next-concern') {{
        document.querySelector('[data-pdf-page-next-concern]')?.click();
        return;
      }}
      if (command === 'focus') {{
        setReviewFocusMode(document.body.dataset.reviewFocus !== 'true');
        return;
      }}
      showToast('未知 PDF 审稿命令');
    }}
    function wirePdfReviewCommands() {{
      document.querySelectorAll('[data-pdf-review-command]').forEach((button) => {{
        button.addEventListener('click', () => handlePdfReviewCommand(button.dataset.pdfReviewCommand || ''));
      }});
      document.querySelectorAll('[data-pdf-agent-action]').forEach((button) => {{
        button.addEventListener('click', () => handlePdfReviewCommand(button.dataset.pdfAgentAction || ''));
      }});
      document.querySelectorAll('[data-pdf-agent-mode]').forEach((button) => {{
        button.addEventListener('click', () => {{
          setPdfAgentReviewMode(button.dataset.pdfAgentMode || 'fast');
          showToast(`已切换为${{reviewModeLabel(window.peerassistReviewMode)}}审稿模式`);
        }});
      }});
    }}
    window.peerassistHandlePdfReviewCommand = handlePdfReviewCommand;
    function applyTraceFilter(status) {{
      const events = Array.from(document.querySelectorAll('.trace-list .trace-event'));
      let visibleCount = 0;
      events.forEach((event) => {{
        const shown = status === 'all' || event.dataset.status === status;
        event.hidden = !shown;
        if (shown) visibleCount += 1;
      }});
      document.querySelectorAll('[data-trace-filter]').forEach((button) => {{
        button.setAttribute('aria-pressed', String(button.dataset.traceFilter === status));
      }});
      const empty = document.querySelector('[data-trace-empty]');
      if (empty) empty.dataset.visible = String(visibleCount === 0);
    }}
    function applyQueueFilter(nextFilter) {{
      const activeFilter = nextFilter || document.querySelector('[data-queue-filter][aria-pressed="true"]')?.dataset.queueFilter || 'all';
      const query = String(document.querySelector('[data-queue-search]')?.value || '').trim().toLowerCase();
      const items = Array.from(document.querySelectorAll('.queue-body .item[data-concern-id]'));
      const currentPdfPage = Number(window.peerassistCurrentPdfPage || 0);
      let visibleCount = 0;
      items.forEach((item) => {{
        const level = String(item.dataset.concernLevel || '').toLowerCase();
        const itemPdfPage = Number(item.dataset.pdfPage || 0);
        const hasPdfPage = itemPdfPage > 0;
        const searchable = String(item.dataset.concernSearch || item.textContent || '').toLowerCase();
        const matchesFilter =
          activeFilter === 'all' ||
          (activeFilter === 'current-page' && currentPdfPage > 0 && itemPdfPage === currentPdfPage) ||
          (activeFilter === 'major' && level.includes('major')) ||
          (activeFilter === 'clarification' && level.includes('clarification')) ||
          (activeFilter === 'pdf' && hasPdfPage);
        const matchesQuery = !query || searchable.includes(query);
        const shown = matchesFilter && matchesQuery;
        item.hidden = !shown;
        if (shown) visibleCount += 1;
      }});
      document.querySelectorAll('[data-queue-filter]').forEach((button) => {{
        button.setAttribute('aria-pressed', String(button.dataset.queueFilter === activeFilter));
      }});
      const count = document.querySelector('[data-queue-result-count]');
      if (count) {{
        const pageSuffix = activeFilter === 'current-page' && currentPdfPage > 0 ? ` · PDF 第 ${{currentPdfPage}} 页` : '';
        count.textContent = `${{visibleCount}} / ${{items.length}} 条关注点${{pageSuffix}}`;
      }}
      const empty = document.querySelector('[data-queue-empty]');
      if (empty) empty.hidden = visibleCount > 0;
    }}
    window.peerassistApplyQueueFilter = applyQueueFilter;
    function clearAnnotationActiveState() {{
      document.querySelectorAll('[data-active="true"]').forEach((node) => {{
        delete node.dataset.active;
      }});
    }}
    function focusAnnotation(concernId, evidenceId, target, pdfPage) {{
      clearAnnotationActiveState();
      const item = concernId ? document.querySelector(`.queue-body .item[data-concern-id="${{CSS.escape(concernId)}}"]`) : null;
      const highlight = evidenceId ? document.querySelector(`[data-evidence-anchor="${{CSS.escape(evidenceId)}}"]`) : null;
      const comment = concernId ? document.querySelector(`[data-paper-concern="${{CSS.escape(concernId)}}"].paper-comment`) : null;
      const pdfAnnotation = concernId ? document.querySelector(`[data-pdf-annotation-card="${{CSS.escape(concernId)}}"]`) : null;
      const chainRow = concernId ? document.querySelector(`[data-evidence-chain-row][data-concern-id="${{CSS.escape(concernId)}}"]`) : null;
      if (item) item.dataset.active = 'true';
      if (highlight) highlight.dataset.active = 'true';
      if (comment) comment.dataset.active = 'true';
      if (pdfAnnotation) pdfAnnotation.dataset.active = 'true';
      if (chainRow) chainRow.dataset.active = 'true';
      const scrollTarget = target === 'queue' ? item : highlight || comment;
      if (scrollTarget) {{
        scrollTarget.scrollIntoView({{behavior: 'smooth', block: 'center'}});
      }}
      const targetPage = Number(pdfPage || item?.dataset.pdfPage || comment?.dataset.pdfPage || 0);
      if (targetPage > 0 && window.peerassistPdfGoToPage) {{
        window.peerassistPdfGoToPage(targetPage)
          .then(() => showToast(`已跳转到 PDF 第 ${{targetPage}} 页`))
          .catch(() => showToast('PDF 跳页未完成'));
        return;
      }}
      showToast(target === 'queue' ? '已定位到审稿队列' : '已定位到论文高亮');
    }}
    window.peerassistFocusAnnotation = focusAnnotation;
    function updateRuntime(state, source) {{
      const runtime = state.runtime || {{}};
      const status = document.getElementById('runtime-status');
      if (status) {{
        status.textContent = `${{runtime.tool_event_count || 0}} 条追踪事件 · ${{state.actions_count || 0}} 个动作`;
      }}
      const streamStatus = document.getElementById('stream-status');
      if (streamStatus) {{
        streamStatus.textContent = source === 'stream' ? '事件流已连接' : '轮询兜底中';
      }}
      const topbar = document.querySelector('.topbar');
      if (topbar) {{
        topbar.dataset.streamState = source === 'stream' ? 'live' : 'polling';
      }}
      updatePdfRuntimePulse(state, source);
      updatePdfToolTrace(state);
      if (source === 'stream') {{
        appendStreamLine('state', `${{runtime.tool_event_count || 0}} 条追踪事件 · ${{state.pending_count || 0}} 条待确认`);
      }}
    }}
    window.peerassistAgentReviewController = null;
    window.peerassistLastAgentReviewPayload = null;
    function setAgentReviewRunState(state, copy = '') {{
      const nextState = state || 'idle';
      const labels = {{
        idle: '任务空闲，可启动审稿',
        running: '智能审稿运行中，可取消当前请求',
        completed: '上次智能审稿已完成，可重试',
        failed: '上次智能审稿失败，可重试',
        cancelled: '上次智能审稿已取消，可重试'
      }};
      document.querySelectorAll('[data-pdf-agent-run-controls]').forEach((node) => {{
        node.dataset.runState = nextState;
      }});
      document.querySelectorAll('[data-pdf-agent-run-state]').forEach((node) => {{
        node.textContent = copy || labels[nextState] || labels.idle;
      }});
      document.querySelectorAll('[data-pdf-agent-cancel]').forEach((button) => {{
        button.disabled = nextState !== 'running';
      }});
      document.querySelectorAll('[data-pdf-agent-retry]').forEach((button) => {{
        button.disabled = nextState === 'running' || !window.peerassistLastAgentReviewPayload;
      }});
    }}
    window.peerassistSetAgentReviewRunState = setAgentReviewRunState;
    function buildAgentReviewPayload() {{
      const selectedEvidence = window.peerassistSelectedEvidence || {{}};
      const selectedText = String(
        selectedEvidence.text
          ? `[PDF 第 ${{selectedEvidence.page || '未知'}} 页选区]\\n${{selectedEvidence.text}}`
          : window.getSelection()?.toString() || ''
      ).trim();
      const reviewMode = window.peerassistReviewMode || 'fast';
      return {{
        selected_text: selectedText,
        review_mode: reviewMode,
        page_copy: selectedEvidence.page ? `PDF 第 ${{selectedEvidence.page}} 页选区` : 'PDF 选区',
        has_selection: Boolean(selectedEvidence.text)
      }};
    }}
    async function runAgentReview(button, payload = null) {{
      const stream = document.querySelector('[data-agent-review-stream]');
      const requestPayload = payload || buildAgentReviewPayload();
      if (!stream) return;
      if (window.peerassistAgentReviewController) {{
        showToast('已有智能审稿任务运行中');
        return;
      }}
      const controller = new AbortController();
      window.peerassistAgentReviewController = controller;
      window.peerassistLastAgentReviewPayload = requestPayload;
      setAgentReviewRunState('running');
      if (button) button.disabled = true;
      const reviewModeCopy = reviewModeLabel(requestPayload.review_mode);
      stream.textContent = requestPayload.has_selection
        ? `正在以${{reviewModeCopy}}模式基于${{requestPayload.page_copy}}启动智能审稿：读取论文证据台账、确定性核查、本地代理结果，并优先核对当前选中文字...`
        : `正在以${{reviewModeCopy}}模式启动全篇智能审稿：读取证据台账、确定性核查、本地代理结果与现有确认队列...`;
      setPdfAgentPhase('evidence', requestPayload.has_selection ? `读取${{requestPayload.page_copy}}与证据台账` : '读取全篇证据台账与确认队列', 'agent');
      try {{
        setPdfAgentPhase('model', `以${{reviewModeCopy}}模式调用审稿模型`, 'agent');
        const response = await fetch('/api/agent-review', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{selected_text: requestPayload.selected_text, review_mode: requestPayload.review_mode}}),
          signal: controller.signal
        }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || '智能审稿失败');
        const structuredCount = Number(payload.structured_concern_count || 0);
        const queueItems = Number(payload.queue_items || 0);
        setPdfAgentPhase('queue', `写回 ${{structuredCount}} 条结构化意见，队列 ${{queueItems}} 条`, 'agent');
        stream.textContent = `${{payload.suggestion}}\\n\\n已结构化入队：${{structuredCount}} 条 · 当前待确认：${{queueItems}} 条\\n草稿路径：${{payload.draft_path}}`;
        setAgentReviewRunState('completed', `已完成：${{structuredCount}} 条结构化意见`);
        showToast(structuredCount > 0 ? `已写入 ${{structuredCount}} 条待确认意见` : '全篇智能审稿草稿已生成');
        if (structuredCount > 0) {{
          setPdfAgentPhase('human', '等待审稿人逐条确认新增意见', 'agent');
          window.setTimeout(() => window.location.reload(), 1200);
        }}
      }} catch (error) {{
        if (error?.name === 'AbortError') {{
          setPdfAgentPhase('prepare', '智能审稿已由审稿人取消', 'cancel');
          stream.textContent = '智能审稿已取消。可调整选区、模式或点击重试继续。';
          setAgentReviewRunState('cancelled');
          showToast('已取消智能审稿请求');
        }} else {{
          setPdfAgentPhase('prepare', `智能审稿未完成：${{error.message}}`, 'error');
          stream.textContent = `智能审稿未完成：${{error.message}}`;
          setAgentReviewRunState('failed', `未完成：${{error.message}}`);
          showToast('智能审稿未完成');
        }}
      }} finally {{
        if (window.peerassistAgentReviewController === controller) {{
          window.peerassistAgentReviewController = null;
        }}
        if (button) button.disabled = false;
        setAgentReviewRunState(
          document.querySelector('[data-pdf-agent-run-controls]')?.dataset.runState || 'idle'
        );
      }}
    }}
    window.peerassistRunAgentReview = runAgentReview;
    function cancelAgentReview() {{
      if (!window.peerassistAgentReviewController) {{
        showToast('当前没有运行中的智能审稿任务');
        return;
      }}
      window.peerassistAgentReviewController.abort();
    }}
    window.peerassistCancelAgentReview = cancelAgentReview;
    function retryAgentReview() {{
      if (!window.peerassistLastAgentReviewPayload) {{
        showToast('暂无可重试的智能审稿请求');
        return;
      }}
      runAgentReview(document.querySelector('[data-agent-review-start]'), window.peerassistLastAgentReviewPayload);
    }}
    window.peerassistRetryAgentReview = retryAgentReview;
    function connectEventStream() {{
      if (!window.EventSource) {{
        refreshRuntime().catch(() => {{}});
        return;
      }}
      const events = new EventSource('/api/events');
      events.addEventListener('state', (event) => {{
        updateRuntime(JSON.parse(event.data), 'stream');
      }});
      events.addEventListener('heartbeat', (event) => {{
        const streamStatus = document.getElementById('stream-status');
        if (streamStatus) streamStatus.textContent = '事件流心跳正常';
        appendStreamLine('heartbeat', '通道存活，继续监听 PeerAssist 运行态');
      }});
      events.addEventListener('done', () => {{
        appendStreamLine('done', '本次快照已发送完成，进入轮询兜底');
        events.close();
      }});
      events.onerror = () => {{
        appendStreamLine('error', '事件流中断，切换到 /api/state 轮询');
        events.close();
        refreshRuntime().catch(() => {{}});
      }};
    }}
    document.addEventListener('click', (event) => {{
      const actionButton = event.target.closest('button[data-action]');
      if (!actionButton) return;
      submitDecision(actionButton).catch((error) => alert(error.message));
    }});
    document.addEventListener('click', (event) => {{
      const editButton = event.target.closest('[data-pdf-annotation-edit]');
      if (!editButton) return;
      openConcernEditor(editButton.dataset.concernId || '', editButton.dataset.pdfPage || '');
    }});
    document.querySelectorAll('[data-copy-path]').forEach((button) => {{
      button.addEventListener('click', () => {{
        copyText(button.dataset.copyPath || '')
          .then(() => showToast('产物路径已复制'))
          .catch((error) => showToast(`复制失败：${{error.message}}`));
      }});
    }});
    document.querySelectorAll('[data-pdf-selection-copy]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const selected = window.peerassistSelectedEvidence || {{}};
        const prefix = selected.page ? `PDF 第 ${{selected.page}} 页选区\\n` : 'PDF 选区\\n';
        copyText(`${{prefix}}${{selected.text || ''}}`)
          .then(() => showToast('PDF 选区已复制'))
          .catch((error) => showToast(`复制失败：${{error.message}}`));
      }});
    }});
    document.querySelectorAll('[data-pdf-selection-use]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const selected = window.peerassistSelectedEvidence || {{}};
        if (!selected.text) {{
          showToast('请先在 PDF 中选择文字');
          return;
        }}
        document.querySelector('[data-panel="model-entry"]')?.scrollIntoView({{behavior: 'smooth', block: 'center'}});
        const startButton = document.querySelector('[data-agent-review-start]');
        if (startButton) {{
          startButton.click();
          showToast('正在基于 PDF 选区启动智能审稿');
          return;
        }}
        showToast('已作为智能审稿关注文本');
      }});
    }});
    document.querySelectorAll('[data-pdf-selection-clear]').forEach((button) => {{
      button.addEventListener('click', () => {{
        setPdfSelectionEvidence({{text: '', page: null}});
        showToast('已清空 PDF 选区');
      }});
    }});
    document.querySelectorAll('[data-trace-filter]').forEach((button) => {{
      button.addEventListener('click', () => applyTraceFilter(button.dataset.traceFilter || 'all'));
    }});
    document.querySelectorAll('[data-queue-filter]').forEach((button) => {{
      button.addEventListener('click', () => applyQueueFilter(button.dataset.queueFilter || 'all'));
    }});
    document.querySelectorAll('[data-queue-search]').forEach((input) => {{
      input.addEventListener('input', () => applyQueueFilter());
    }});
    setPdfAgentReviewMode('fast', true);
    wirePdfReviewCommands();
    document.querySelectorAll('[data-focus-review-toggle]').forEach((button) => {{
      button.addEventListener('click', () => {{
        setReviewFocusMode(document.body.dataset.reviewFocus !== 'true');
      }});
    }});
    document.querySelectorAll('[data-pdf-page-filter-current]').forEach((button) => {{
      button.addEventListener('click', () => {{
        document.querySelector('[data-queue-filter="current-page"]')?.click();
        document.querySelector('[data-panel="review-queue"]')?.scrollIntoView({{behavior: 'smooth', block: 'start'}});
        showToast('已切换到当前 PDF 页队列');
      }});
    }});
    document.querySelectorAll('[data-pdf-page-next-pending]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const nextPage = window.peerassistPdfNextPendingConcernPage?.();
        if (!nextPage) {{
          showToast('暂无未处理页码批注');
          return;
        }}
        window.peerassistPdfGoToPage?.(nextPage)
          .then(() => showToast(`已跳转到第 ${{nextPage}} 页未处理批注`))
          .catch(() => showToast('PDF 跳页未完成'));
      }});
    }});
    document.querySelectorAll('[data-pdf-page-next-concern]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const nextPage = window.peerassistPdfNextConcernPage?.();
        if (!nextPage) {{
          showToast('暂无页码级审稿关注');
          return;
        }}
        window.peerassistPdfGoToPage?.(nextPage)
          .then(() => showToast(`已跳转到第 ${{nextPage}} 页关注`))
          .catch(() => showToast('PDF 跳页未完成'));
      }});
    }});
    document.querySelectorAll('[data-pdf-agent-cancel]').forEach((button) => {{
      button.addEventListener('click', () => cancelAgentReview());
    }});
    document.querySelectorAll('[data-pdf-agent-retry]').forEach((button) => {{
      button.addEventListener('click', () => retryAgentReview());
    }});
    document.querySelectorAll('[data-pdf-recovery-trace]').forEach((button) => {{
      button.addEventListener('click', () => {{
        document.querySelector('[data-panel="tool-trace"]')?.scrollIntoView({{behavior: 'smooth', block: 'start'}});
        showToast('已定位到工具追踪');
      }});
    }});
    document.querySelectorAll('[data-pdf-recovery-failed]').forEach((button) => {{
      button.addEventListener('click', () => {{
        applyTraceFilter('failed');
        document.querySelector('[data-panel="tool-trace"]')?.scrollIntoView({{behavior: 'smooth', block: 'start'}});
        showToast('已筛选失败工具事件');
      }});
    }});
    document.querySelectorAll('[data-pdf-recovery-artifacts]').forEach((button) => {{
      button.addEventListener('click', () => {{
        document.querySelector('[data-panel="artifact-workspace"]')?.scrollIntoView({{behavior: 'smooth', block: 'start'}});
        showToast('已定位到产物工作区');
      }});
    }});
    document.querySelectorAll('[data-pdf-human-gate-open]').forEach((button) => {{
      button.addEventListener('click', () => {{
        document.querySelector('[data-panel="human-confirmation"]')?.scrollIntoView({{behavior: 'smooth', block: 'start'}});
        showToast('已打开人工确认闸门');
      }});
    }});
    document.querySelectorAll('[data-pdf-human-gate-next]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const nextPage = window.peerassistPdfNextPendingConcernPage?.();
        if (!nextPage) {{
          showToast('暂无未处理审稿意见');
          return;
        }}
        window.peerassistPdfGoToPage?.(nextPage)
          .then(() => showToast(`已跳转到第 ${{nextPage}} 页待确认意见`))
          .catch(() => showToast('PDF 跳页未完成'));
      }});
    }});
    document.querySelectorAll('[data-pdf-human-gate-queue]').forEach((button) => {{
      button.addEventListener('click', () => {{
        document.querySelector('[data-queue-filter="all"]')?.click();
        document.querySelector('[data-panel="review-queue"]')?.scrollIntoView({{behavior: 'smooth', block: 'start'}});
        showToast('已定位到审稿队列清单');
      }});
    }});
    document.querySelectorAll('[data-pdf-evidence-gate-chain]').forEach((button) => {{
      button.addEventListener('click', () => {{
        document.querySelector('[data-panel="evidence-chain-matrix"]')?.scrollIntoView({{behavior: 'smooth', block: 'start'}});
        showToast('已定位到证据链矩阵');
      }});
    }});
    document.querySelectorAll('[data-pdf-evidence-gate-pdf]').forEach((button) => {{
      button.addEventListener('click', () => {{
        document.querySelector('[data-queue-filter="pdf"]')?.click();
        document.querySelector('[data-panel="review-queue"]')?.scrollIntoView({{behavior: 'smooth', block: 'start'}});
        showToast('已筛选有 PDF 证据的关注点');
      }});
    }});
    document.querySelectorAll('[data-pdf-evidence-gate-unbound]').forEach((button) => {{
      button.addEventListener('click', () => focusFirstUnboundEvidenceConcern());
    }});
    document.querySelectorAll('[data-jump-concern]').forEach((button) => {{
      button.addEventListener('click', () => {{
        focusAnnotation(button.dataset.jumpConcern || '', button.dataset.paperTarget || '', 'queue', button.dataset.pdfPage || '');
      }});
    }});
    document.querySelectorAll('.paper-comment').forEach((comment) => {{
      comment.addEventListener('click', (event) => {{
        if (event.target.closest('button')) return;
        focusAnnotation(comment.dataset.paperConcern || '', comment.dataset.marginComment || '', 'queue', comment.dataset.pdfPage || '');
      }});
    }});
    document.querySelectorAll('[data-paper-target]:not([data-jump-concern])').forEach((button) => {{
      button.addEventListener('click', () => {{
        focusAnnotation(button.dataset.paperConcern || '', button.dataset.paperTarget || '', 'paper', button.dataset.pdfPage || '');
      }});
    }});
    document.querySelectorAll('[data-agent-review-start]').forEach((button) => {{
      button.addEventListener('click', async () => {{
        runAgentReview(button);
      }});
    }});
    setAgentReviewRunState('idle');
    updatePdfAgentContext();
    updatePdfRecoveryCheckpoint();
    updatePdfHumanGate();
    updatePdfEvidenceGate();
    applyTraceFilter('all');
    applyQueueFilter('all');
    connectEventStream();
    window.setInterval(() => refreshRuntime().catch(() => {{}}), 15000);
  </script>
  <script type="module">
    import * as pdfjsLib from 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.min.mjs';

    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.worker.min.mjs';

    const reader = document.querySelector('[data-pdf-reader]');
    if (reader) {{
      const stage = reader.querySelector('[data-pdf-stage]');
      const canvas = reader.querySelector('[data-pdf-canvas]');
      const textLayer = reader.querySelector('[data-pdf-text-layer]');
      const status = reader.querySelector('[data-pdf-status]');
      const loading = reader.querySelector('[data-pdf-loading]');
      const fallback = reader.querySelector('[data-pdf-fallback]');
      const pageRail = reader.querySelector('[data-pdf-page-rail]');
      const outlineList = reader.querySelector('[data-pdf-outline-list]');
      const outlineEmpty = reader.querySelector('[data-pdf-outline-empty]');
      const outlineStatus = reader.querySelector('[data-pdf-outline-status]');
      const routeCurrent = reader.querySelector('[data-pdf-route-current]');
      const routePending = reader.querySelector('[data-pdf-route-pending]');
      const routeConcern = reader.querySelector('[data-pdf-route-concern]');
      const readingRouteList = reader.querySelector('[data-pdf-reading-route-list]');
      const readingRouteEmpty = reader.querySelector('[data-pdf-reading-route-empty]');
      const pageContextTitle = reader.querySelector('[data-pdf-page-context-title]');
      const pageContextCopy = reader.querySelector('[data-pdf-page-context-copy]');
      const annotationList = reader.querySelector('[data-pdf-annotation-list]');
      const annotationCount = reader.querySelector('[data-pdf-annotation-count]');
      const annotationEmpty = reader.querySelector('[data-pdf-annotation-empty]');
      const annotationPending = reader.querySelector('[data-pdf-annotation-pending]');
      const annotationDone = reader.querySelector('[data-pdf-annotation-done]');
      const annotationTotal = reader.querySelector('[data-pdf-annotation-total]');
      const annotationMeter = reader.querySelector('[data-pdf-annotation-meter]');
      const annotationDensityToggle = reader.querySelector('[data-pdf-annotation-density-toggle]');
      const runtimePage = reader.querySelector('[data-pdf-runtime-page]');
      const searchInput = reader.querySelector('[data-pdf-search-input]');
      const searchStatus = reader.querySelector('[data-pdf-search-status]');
      const searchResults = reader.querySelector('[data-pdf-search-results]');
      const searchPrev = reader.querySelector('[data-pdf-search-prev]');
      const searchNext = reader.querySelector('[data-pdf-search-next]');
      const searchClear = reader.querySelector('[data-pdf-search-clear]');
      const context = canvas.getContext('2d');
      let pdfDoc = null;
      let pageNumber = 1;
      let scale = 1.15;
      let renderTask = null;
      let textLayerTask = null;
      let fitWidth = true;
      let annotationDensity = 'comfortable';
      let pdfSearchQuery = '';
      let pdfSearchMatches = [];
      let pdfSearchCursor = -1;
      let pdfSearchTimer = null;
      let pdfSearchRunId = 0;
      const pdfTextCache = new Map();

      function setStatus(copy) {{
        if (status) status.textContent = copy;
      }}

      function setLoading(visible) {{
        if (loading) loading.hidden = !visible;
      }}

      function setOutlineStatus(copy) {{
        if (outlineStatus) outlineStatus.textContent = copy;
      }}

      async function resolvePdfOutlinePage(item) {{
        if (!pdfDoc || !item?.dest) return null;
        const destination = Array.isArray(item.dest)
          ? item.dest
          : await pdfDoc.getDestination(item.dest).catch(() => null);
        const ref = Array.isArray(destination) ? destination[0] : null;
        if (!ref) return null;
        if (typeof ref === 'number') return Math.max(1, Math.min(pdfDoc.numPages, ref + 1));
        const pageIndex = await pdfDoc.getPageIndex(ref).catch(() => -1);
        return pageIndex >= 0 ? pageIndex + 1 : null;
      }}

      async function appendPdfOutlineItem(item, depth = 0) {{
        if (!outlineList || !item) return 0;
        let count = 0;
        const title = String(item.title || '').trim();
        if (title) {{
          count += 1;
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'pdf-outline-item';
          button.dataset.outlineDepth = String(Math.min(2, Math.max(0, depth)));
          button.textContent = title;
          button.title = title;
          button.disabled = true;
          outlineList.appendChild(button);
          resolvePdfOutlinePage(item).then((page) => {{
            if (!page) {{
              button.title = `${{title}} · 无法解析页码`;
              return;
            }}
            button.disabled = false;
            button.dataset.pdfOutlinePage = String(page);
            button.title = `${{title}} · 第 ${{page}} 页`;
            button.addEventListener('click', () => {{
              window.peerassistPdfGoToPage(page)
                .then(() => appendPdfActivityLine('outline', `从 PDF 目录跳转到第 ${{page}} 页：${{title}}`))
                .catch(() => showToast('PDF 目录跳转未完成'));
            }});
          }});
        }}
        const children = Array.isArray(item.items) ? item.items : [];
        for (const child of children.slice(0, 8)) {{
          count += await appendPdfOutlineItem(child, depth + 1);
        }}
        return count;
      }}

      async function renderPdfOutline() {{
        if (!outlineList || !outlineEmpty || !pdfDoc) return;
        outlineList.replaceChildren();
        outlineEmpty.hidden = true;
        setOutlineStatus('正在读取内置目录');
        const outline = await pdfDoc.getOutline().catch(() => null);
        if (!Array.isArray(outline) || outline.length === 0) {{
          outlineEmpty.hidden = false;
          setOutlineStatus('无内置目录');
          return;
        }}
        let count = 0;
        for (const item of outline.slice(0, 12)) {{
          count += await appendPdfOutlineItem(item, 0);
        }}
        setOutlineStatus(`${{count}} 个目录入口`);
      }}

      function normalizePdfSearch(value) {{
        return String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
      }}

      function countPdfSearchOccurrences(text, query) {{
        const haystack = normalizePdfSearch(text);
        const needle = normalizePdfSearch(query);
        if (!needle) return 0;
        let count = 0;
        let index = haystack.indexOf(needle);
        while (index !== -1) {{
          count += 1;
          index = haystack.indexOf(needle, index + Math.max(1, needle.length));
        }}
        return count;
      }}

      function pdfSearchSnippet(text, query) {{
        const normalizedText = String(text || '').replace(/\\s+/g, ' ').trim();
        const lowerText = normalizedText.toLowerCase();
        const lowerQuery = normalizePdfSearch(query);
        const index = lowerText.indexOf(lowerQuery);
        if (index < 0) return normalizedText.slice(0, 72);
        const start = Math.max(0, index - 28);
        const end = Math.min(normalizedText.length, index + lowerQuery.length + 36);
        return `${{start > 0 ? '...' : ''}}${{normalizedText.slice(start, end)}}${{end < normalizedText.length ? '...' : ''}}`;
      }}

      function setPdfSearchStatus(copy) {{
        if (searchStatus) searchStatus.textContent = copy;
      }}

      function setPdfSearchNavigationEnabled(enabled) {{
        if (searchPrev) searchPrev.disabled = !enabled;
        if (searchNext) searchNext.disabled = !enabled;
      }}

      async function getPdfPageText(page) {{
        const safePage = Math.max(1, Math.min(pdfDoc?.numPages || 1, Number(page) || 1));
        if (pdfTextCache.has(safePage)) return pdfTextCache.get(safePage);
        const pdfPage = await pdfDoc.getPage(safePage);
        const textContent = await pdfPage.getTextContent();
        const text = (textContent.items || []).map((item) => String(item.str || '')).join(' ');
        pdfTextCache.set(safePage, text);
        return text;
      }}

      function applyPdfSearchHighlights() {{
        if (!textLayer) return;
        const query = normalizePdfSearch(pdfSearchQuery);
        textLayer.querySelectorAll('.pdf-text-match').forEach((node) => {{
          node.classList.remove('pdf-text-match');
          node.removeAttribute('data-pdf-search-match');
        }});
        if (!query) return;
        textLayer.querySelectorAll('span').forEach((span) => {{
          const text = normalizePdfSearch(span.textContent || '');
          if (text.includes(query)) {{
            span.classList.add('pdf-text-match');
            span.dataset.pdfSearchMatch = 'true';
          }}
        }});
      }}

      function renderPdfSearchResults() {{
        if (!searchResults) return;
        searchResults.replaceChildren();
        const total = pdfSearchMatches.reduce((sum, match) => sum + Number(match.count || 0), 0);
        const activeMatch = pdfSearchMatches[pdfSearchCursor] || null;
        setPdfSearchNavigationEnabled(pdfSearchMatches.length > 0);
        if (!pdfSearchQuery) {{
          const empty = document.createElement('span');
          empty.className = 'pdf-search-empty';
          empty.textContent = '等待检索关键词。';
          searchResults.appendChild(empty);
          setPdfSearchStatus('输入关键词后在整篇 PDF 中定位证据。');
          return;
        }}
        if (pdfSearchMatches.length === 0) {{
          const empty = document.createElement('span');
          empty.className = 'pdf-search-empty';
          empty.textContent = '未找到匹配页。';
          searchResults.appendChild(empty);
          setPdfSearchStatus(`未找到“${{pdfSearchQuery}}”的 PDF 命中。`);
          return;
        }}
        setPdfSearchStatus(
          activeMatch
            ? `“${{pdfSearchQuery}}”共 ${{total}} 处 · 当前第 ${{activeMatch.page}} 页`
            : `“${{pdfSearchQuery}}”共 ${{total}} 处，分布在 ${{pdfSearchMatches.length}} 页`
        );
        pdfSearchMatches.forEach((match, index) => {{
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'pdf-search-hit';
          button.dataset.pdfSearchHit = String(index);
          button.dataset.active = String(index === pdfSearchCursor);
          button.textContent = `第 ${{match.page}} 页 · ${{match.count}} 处 · ${{match.snippet}}`;
          button.title = match.snippet;
          button.addEventListener('click', () => {{
            goToPdfSearchMatch(index).catch((error) => {{
              showToast(`PDF 检索跳转失败：${{error.message || '未知错误'}}`);
            }});
          }});
          searchResults.appendChild(button);
        }});
      }}

      async function goToPdfSearchMatch(index) {{
        if (!pdfSearchMatches.length) return;
        const nextIndex = (index + pdfSearchMatches.length) % pdfSearchMatches.length;
        pdfSearchCursor = nextIndex;
        const match = pdfSearchMatches[nextIndex];
        renderPdfSearchResults();
        await window.peerassistPdfGoToPage(match.page);
        applyPdfSearchHighlights();
        showToast(`已定位到“${{pdfSearchQuery}}”第 ${{match.page}} 页`);
      }}

      async function runPdfSearch(query) {{
        const normalizedQuery = String(query || '').replace(/\\s+/g, ' ').trim();
        pdfSearchQuery = normalizedQuery;
        pdfSearchMatches = [];
        pdfSearchCursor = -1;
        renderPdfSearchResults();
        applyPdfSearchHighlights();
        if (!normalizedQuery || !pdfDoc) return;
        const runId = ++pdfSearchRunId;
        setPdfSearchStatus(`正在检索“${{normalizedQuery}}”...`);
        for (let page = 1; page <= pdfDoc.numPages; page += 1) {{
          if (runId !== pdfSearchRunId) return;
          const text = await getPdfPageText(page);
          const count = countPdfSearchOccurrences(text, normalizedQuery);
          if (count > 0) {{
            pdfSearchMatches.push({{page, count, snippet: pdfSearchSnippet(text, normalizedQuery)}});
          }}
          setPdfSearchStatus(`正在检索“${{normalizedQuery}}” · 第 ${{page}} / ${{pdfDoc.numPages}} 页`);
        }}
        if (runId !== pdfSearchRunId) return;
        pdfSearchCursor = pdfSearchMatches.findIndex((match) => match.page >= pageNumber);
        if (pdfSearchCursor < 0 && pdfSearchMatches.length > 0) pdfSearchCursor = 0;
        renderPdfSearchResults();
        applyPdfSearchHighlights();
        if (pdfSearchMatches.length > 0) {{
          appendPdfActivityLine('search', `PDF 原文检索“${{normalizedQuery}}”：${{pdfSearchMatches.length}} 页命中`);
        }}
      }}

      function schedulePdfSearch(query) {{
        window.clearTimeout(pdfSearchTimer);
        pdfSearchTimer = window.setTimeout(() => {{
          runPdfSearch(query).catch((error) => {{
            setPdfSearchStatus(`检索失败：${{error.message || '未知错误'}}`);
          }});
        }}, 320);
      }}

      function syncCurrentPdfPage() {{
        window.peerassistCurrentPdfPage = pageNumber;
        const activeQueueFilter = document.querySelector('[data-queue-filter][aria-pressed="true"]')?.dataset.queueFilter;
        if (activeQueueFilter === 'current-page') {{
          window.peerassistApplyQueueFilter?.('current-page');
        }}
        updatePdfPageContext();
        syncPdfPageAnnotations();
        syncPdfRuntimePagePulse();
        window.peerassistUpdatePdfAgentContext?.();
      }}

      function concernCountForPage(page) {{
        return collectPdfPageConcernCounts().get(Number(page) || 0) || 0;
      }}

      function nextConcernPage() {{
        const pages = Array.from(collectPdfPageConcernCounts().keys()).sort((a, b) => a - b);
        if (pages.length === 0) return null;
        return pages.find((page) => page > pageNumber) || pages[0];
      }}

      function pendingCountForPage(page) {{
        return collectPdfPagePendingCounts().get(Number(page) || 0) || 0;
      }}

      function nextPendingConcernPage() {{
        const pages = Array.from(collectPdfPagePendingCounts().keys()).sort((a, b) => a - b);
        if (pages.length === 0) return null;
        return pages.find((page) => page > pageNumber) || pages[0];
      }}

      function isMajorRouteLevel(level) {{
        return String(level || '').toLowerCase().includes('major');
      }}

      function reviewRouteScore(item) {{
        const pendingScore = isResolvedConcernStatus(item.dataset.concernStatus) ? 0 : 1000;
        const majorScore = isMajorRouteLevel(item.dataset.concernLevel) ? 200 : 0;
        const currentPageScore = Number(item.dataset.pdfPage || 0) === pageNumber ? 80 : 0;
        return pendingScore + majorScore + currentPageScore;
      }}

      function collectPdfReadingRouteItems() {{
        return Array.from(document.querySelectorAll('.queue-body .item[data-concern-id][data-pdf-page]'))
          .filter((item) => Number(item.dataset.pdfPage || 0) > 0)
          .sort((left, right) => {{
            const scoreDelta = reviewRouteScore(right) - reviewRouteScore(left);
            if (scoreDelta !== 0) return scoreDelta;
            const pageDelta = Number(left.dataset.pdfPage || 0) - Number(right.dataset.pdfPage || 0);
            if (pageDelta !== 0) return pageDelta;
            return String(left.dataset.concernTitle || '').localeCompare(String(right.dataset.concernTitle || ''), 'zh-Hans-CN');
          }})
          .slice(0, 4);
      }}

      function renderPdfReadingRoute() {{
        if (!readingRouteList || !readingRouteEmpty) return;
        const routeItems = collectPdfReadingRouteItems();
        readingRouteList.replaceChildren();
        readingRouteEmpty.hidden = routeItems.length > 0;
        routeItems.forEach((item, index) => {{
          const page = Number(item.dataset.pdfPage || 0);
          const concernId = item.dataset.concernId || '';
          const title = item.dataset.concernTitle || '未命名关注点';
          const level = item.dataset.concernLevel || 'unknown';
          const category = item.dataset.concernCategory || 'general';
          const pending = !isResolvedConcernStatus(item.dataset.concernStatus);
          const card = document.createElement('button');
          card.type = 'button';
          card.className = 'pdf-reading-route-card';
          card.dataset.pdfReadingRouteCard = concernId;
          card.dataset.pdfPage = String(page);
          card.dataset.active = String(page === pageNumber);
          card.dataset.routeState = pending ? 'pending' : isMajorRouteLevel(level) ? 'major' : 'ready';
          card.title = `${{title}} · PDF 第 ${{page}} 页`;
          card.innerHTML = `
            <span class="pdf-reading-route-page">P${{page}}</span>
            <span>
              <span class="pdf-reading-route-title">${{escapeHtml(title)}}</span>
              <span class="pdf-reading-route-meta">${{escapeHtml(level)}} · ${{escapeHtml(category)}} · ${{pending ? '待确认' : '已处理'}}</span>
            </span>
            <span class="pdf-reading-route-rank">#${{index + 1}}</span>
          `;
          card.addEventListener('click', () => {{
            window.peerassistFocusAnnotation?.(concernId, '', 'queue', page);
            appendPdfActivityLine('route', `按重点阅读路线定位 PDF 第 ${{page}} 页：${{title}}`);
          }});
          readingRouteList.appendChild(card);
        }});
      }}
      window.peerassistRenderPdfReadingRoute = renderPdfReadingRoute;

      function updatePdfPageContext() {{
        const count = concernCountForPage(pageNumber);
        const pendingCount = pendingCountForPage(pageNumber);
        const nextPendingPage = nextPendingConcernPage();
        const nextPage = nextPendingPage || nextConcernPage();
        renderPdfReadingRoute();
        if (routeCurrent) {{
          routeCurrent.textContent = pendingCount > 0
            ? `第 ${{pageNumber}} 页 · ${{pendingCount}} 条未处理`
            : count > 0
              ? `第 ${{pageNumber}} 页 · ${{count}} 条关注`
              : `第 ${{pageNumber}} 页 · 可继续阅读`;
        }}
        if (routePending) {{
          routePending.textContent = nextPendingPage ? `第 ${{nextPendingPage}} 页` : '暂无未处理';
        }}
        if (routeConcern) {{
          routeConcern.textContent = nextPage ? `第 ${{nextPage}} 页` : '暂无关注页';
        }}
        if (pageContextTitle) {{
          pageContextTitle.textContent = pendingCount > 0
            ? `PDF 第 ${{pageNumber}} 页 · 本页 ${{pendingCount}} 条未处理`
            : count > 0
              ? `PDF 第 ${{pageNumber}} 页 · 本页 ${{count}} 条审稿关注`
            : `PDF 第 ${{pageNumber}} 页 · 本页暂无审稿关注`;
        }}
        if (pageContextCopy) {{
          if (nextPendingPage === pageNumber && pendingCount > 0) {{
            pageContextCopy.textContent = '当前页仍有未处理批注，可逐条确认、暂挂、改写或核对。';
          }} else if (nextPendingPage) {{
            pageContextCopy.textContent = `下一处未处理批注：第 ${{nextPendingPage}} 页。`;
          }} else if (!nextPage) {{
            pageContextCopy.textContent = '当前论文尚未绑定页码级审稿关注。';
          }} else if (nextPage === pageNumber && count > 0) {{
            pageContextCopy.textContent = '当前页关注已处理，可继续核对本页队列或查看下一关注页。';
          }} else {{
            pageContextCopy.textContent = `下一处有关注的页面：第 ${{nextPage}} 页。`;
          }}
        }}
      }}
      window.peerassistPdfNextConcernPage = nextConcernPage;
      window.peerassistPdfNextPendingConcernPage = nextPendingConcernPage;

      function syncPdfRuntimePagePulse() {{
        if (!runtimePage) return;
        const count = concernCountForPage(pageNumber);
        runtimePage.textContent = `第 ${{pageNumber}} 页 · ${{count}} 条关注`;
      }}
      window.peerassistSyncPdfRuntimePagePulse = syncPdfRuntimePagePulse;

      function queueItemsForPdfPage(page) {{
        return Array.from(document.querySelectorAll('.queue-body .item[data-concern-id]')).filter((item) => {{
          return Number(item.dataset.pdfPage || 0) === Number(page);
        }});
      }}

      function isResolvedConcernStatus(status) {{
        return ['confirmed', 'rewritten', 'downgraded', 'deleted'].includes(String(status || '').toLowerCase());
      }}

      function syncPdfAnnotationProgress(items) {{
        const total = items.length;
        const done = items.filter((item) => isResolvedConcernStatus(item.dataset.concernStatus)).length;
        const pending = Math.max(0, total - done);
        if (annotationPending) annotationPending.textContent = `${{pending}} 条`;
        if (annotationDone) annotationDone.textContent = `${{done}} 条`;
        if (annotationTotal) annotationTotal.textContent = `${{total}} 条`;
        if (annotationMeter) {{
          annotationMeter.style.width = total > 0 ? `${{Math.round(done / total * 100)}}%` : '0%';
        }}
      }}

      function setPdfAnnotationDensity(nextDensity) {{
        annotationDensity = nextDensity === 'compact' ? 'compact' : 'comfortable';
        if (annotationList) annotationList.dataset.density = annotationDensity;
        if (annotationDensityToggle) {{
          const isCompact = annotationDensity === 'compact';
          annotationDensityToggle.setAttribute('aria-pressed', String(isCompact));
          annotationDensityToggle.textContent = isCompact ? '展开' : '紧凑';
        }}
      }}
      window.peerassistSetPdfAnnotationDensity = setPdfAnnotationDensity;

      function syncPdfPageAnnotations() {{
        if (!annotationList || !annotationCount || !annotationEmpty) return;
        const items = queueItemsForPdfPage(pageNumber);
        annotationList.replaceChildren();
        annotationList.dataset.density = annotationDensity;
        annotationCount.textContent = `${{items.length}} 条`;
        annotationEmpty.hidden = items.length > 0;
        syncPdfAnnotationProgress(items);
        items.slice(0, 6).forEach((item) => {{
          const concernId = item.dataset.concernId || '';
          const title = item.dataset.concernTitle || '未命名关注点';
          const level = item.dataset.concernLevel || 'unknown';
          const category = item.dataset.concernCategory || 'general';
          const status = item.dataset.concernStatus || 'pending';
          const card = document.createElement('article');
          card.className = 'pdf-annotation-card';
          card.setAttribute('data-pdf-annotation-card', concernId);
          card.dataset.pdfPage = String(pageNumber);
          card.dataset.concernStatus = status;
          const titleEl = document.createElement('div');
          titleEl.className = 'pdf-annotation-title';
          titleEl.textContent = title;
          const meta = document.createElement('div');
          meta.className = 'pdf-annotation-meta';
          meta.textContent = `${{level}} · ${{category}} · ${{status}}`;
          const actions = document.createElement('div');
          actions.className = 'pdf-annotation-actions';
          const confirm = document.createElement('button');
          confirm.className = 'inline-button pdf-annotation-action primary';
          confirm.type = 'button';
          confirm.dataset.action = 'confirm';
          confirm.dataset.concernId = concernId;
          confirm.textContent = '确认';
          const pending = document.createElement('button');
          pending.className = 'inline-button pdf-annotation-action';
          pending.type = 'button';
          pending.dataset.action = 'mark_pending';
          pending.dataset.concernId = concernId;
          pending.textContent = '暂挂';
          const edit = document.createElement('button');
          edit.className = 'inline-button pdf-annotation-action';
          edit.type = 'button';
          edit.dataset.pdfAnnotationEdit = 'true';
          edit.dataset.concernId = concernId;
          edit.dataset.pdfPage = String(pageNumber);
          edit.textContent = '改写';
          const locate = document.createElement('button');
          locate.className = 'inline-button pdf-annotation-action';
          locate.type = 'button';
          locate.textContent = '核对';
          locate.addEventListener('click', () => {{
            window.peerassistFocusAnnotation?.(concernId, '', 'queue', pageNumber);
          }});
          actions.append(confirm, pending, edit, locate);
          card.append(titleEl, meta, actions);
          annotationList.appendChild(card);
        }});
      }}
      window.peerassistSyncPdfPageAnnotations = syncPdfPageAnnotations;

      if (annotationDensityToggle) {{
        annotationDensityToggle.addEventListener('click', () => {{
          setPdfAnnotationDensity(annotationDensity === 'compact' ? 'comfortable' : 'compact');
        }});
      }}
      setPdfAnnotationDensity(annotationDensity);

      function updateButtons() {{
        reader.querySelectorAll('[data-pdf-action]').forEach((button) => {{
          const action = button.dataset.pdfAction;
          button.disabled =
            (action === 'prev' && pageNumber <= 1) ||
            (action === 'next' && pdfDoc && pageNumber >= pdfDoc.numPages);
        }});
        reader.querySelectorAll('[data-pdf-page-jump]').forEach((button) => {{
          button.setAttribute('aria-current', button.dataset.pdfPageJump === String(pageNumber) ? 'page' : 'false');
        }});
        syncCurrentPdfPage();
      }}

      window.peerassistPdfGoToPage = async (page) => {{
        if (!pdfDoc) throw new Error('PDF 尚未载入');
        const nextPage = Math.max(1, Math.min(pdfDoc.numPages, Number(page) || 1));
        pageNumber = nextPage;
        await renderPage();
        stage?.scrollIntoView({{behavior: 'smooth', block: 'center'}});
      }};

      function capturePdfTextSelection() {{
        const selection = window.getSelection();
        const text = String(selection?.toString() || '').replace(/\\s+/g, ' ').trim();
        if (!text || !selection || selection.rangeCount === 0) return;
        const anchorNode = selection.anchorNode;
        const focusNode = selection.focusNode;
        const isPdfSelection =
          (anchorNode && textLayer?.contains(anchorNode)) ||
          (focusNode && textLayer?.contains(focusNode));
        if (!isPdfSelection) return;
        const selectionRect = selection.getRangeAt(0).getBoundingClientRect();
        window.peerassistSetPdfSelection?.({{
          text,
          page: pageNumber,
          rect: {{
            left: selectionRect.left,
            top: selectionRect.top,
            right: selectionRect.right,
            bottom: selectionRect.bottom
          }}
        }});
      }}

      reader.addEventListener('mouseup', () => {{
        window.setTimeout(capturePdfTextSelection, 0);
      }});
      reader.addEventListener('keyup', () => {{
        window.setTimeout(capturePdfTextSelection, 0);
      }});

      function collectPdfPageConcernCounts() {{
        const pageConcerns = new Map();
        document.querySelectorAll('.item[data-pdf-page], .paper-comment[data-pdf-page]').forEach((node) => {{
          const page = Number(node.dataset.pdfPage || 0);
          if (!Number.isFinite(page) || page <= 0) return;
          const concernId =
            node.dataset.paperConcern ||
            node.dataset.concernId ||
            node.dataset.marginComment ||
            node.id ||
            `page-${{page}}-${{pageConcerns.size}}`;
          if (!pageConcerns.has(page)) pageConcerns.set(page, new Set());
          pageConcerns.get(page).add(concernId);
        }});
        return new Map(Array.from(pageConcerns, ([page, concerns]) => [page, concerns.size]));
      }}

      function collectPdfPagePendingCounts() {{
        const pageConcerns = new Map();
        document.querySelectorAll('.queue-body .item[data-concern-id][data-pdf-page]').forEach((node) => {{
          if (isResolvedConcernStatus(node.dataset.concernStatus)) return;
          const page = Number(node.dataset.pdfPage || 0);
          if (!Number.isFinite(page) || page <= 0) return;
          const concernId = node.dataset.concernId || `page-${{page}}-${{pageConcerns.size}}`;
          if (!pageConcerns.has(page)) pageConcerns.set(page, new Set());
          pageConcerns.get(page).add(concernId);
        }});
        return new Map(Array.from(pageConcerns, ([page, concerns]) => [page, concerns.size]));
      }}

      function annotatePdfPageRail() {{
        const counts = collectPdfPageConcernCounts();
        const pendingCounts = collectPdfPagePendingCounts();
        reader.querySelectorAll('[data-pdf-page-jump]').forEach((button) => {{
          const page = Number(button.dataset.pdfPageJump || 0);
          const count = counts.get(page) || 0;
          const pendingCount = pendingCounts.get(page) || 0;
          button.querySelector('.pdf-page-badge')?.remove();
          button.setAttribute('data-pdf-concern-count', String(count));
          button.setAttribute('data-pdf-pending-count', String(pendingCount));
          button.setAttribute('data-has-concern', count > 0 ? 'true' : 'false');
          button.setAttribute('data-has-pending', pendingCount > 0 ? 'true' : 'false');
          button.setAttribute(
            'aria-label',
            pendingCount > 0
              ? `跳转到第 ${{page}} 页，本页 ${{pendingCount}} 条未处理，${{count}} 条审稿关注`
              : count > 0
                ? `跳转到第 ${{page}} 页，本页 ${{count}} 条审稿关注`
                : `跳转到第 ${{page}} 页`
          );
          if (count > 0) {{
            const badge = document.createElement('span');
            badge.className = 'pdf-page-badge';
            badge.setAttribute('aria-hidden', 'true');
            badge.textContent = String(count);
            button.appendChild(badge);
          }}
        }});
      }}

      function buildPdfPageRail(pageCount) {{
        if (!pageRail) return;
        pageRail.replaceChildren();
        const label = document.createElement('span');
        label.className = 'pdf-page-rail-label';
        label.textContent = 'PDF 页码导航';
        pageRail.appendChild(label);
        for (let page = 1; page <= pageCount; page += 1) {{
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'pdf-page-button';
          button.dataset.pdfPageJump = String(page);
          const pageNumberLabel = document.createElement('span');
          pageNumberLabel.className = 'pdf-page-number';
          pageNumberLabel.textContent = String(page);
          button.appendChild(pageNumberLabel);
          button.setAttribute('aria-label', `跳转到第 ${{page}} 页`);
          button.addEventListener('click', () => {{
            window.peerassistPdfGoToPage(page).catch((error) => {{
              showToast(`PDF 跳页未完成：${{error.message || '未知错误'}}`);
            }});
          }});
          pageRail.appendChild(button);
        }}
        annotatePdfPageRail();
        updateButtons();
      }}

      function stageWidth() {{
        return Math.max(360, (stage?.clientWidth || 780) - 36);
      }}

      async function renderPage() {{
        if (!pdfDoc || !canvas || !context) return;
        setLoading(true);
        if (renderTask) {{
          renderTask.cancel();
          renderTask = null;
        }}
        const page = await pdfDoc.getPage(pageNumber);
        const naturalViewport = page.getViewport({{scale: 1}});
        const targetScale = fitWidth
          ? Math.min(2.2, Math.max(0.75, stageWidth() / naturalViewport.width))
          : scale;
        scale = targetScale;
        const viewport = page.getViewport({{scale: targetScale}});
        const outputScale = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = `${{Math.floor(viewport.width)}}px`;
        canvas.style.height = `${{Math.floor(viewport.height)}}px`;
        if (textLayer) {{
          if (textLayerTask) {{
            textLayerTask.cancel();
            textLayerTask = null;
          }}
          textLayer.replaceChildren();
          textLayer.style.width = `${{Math.floor(viewport.width)}}px`;
          textLayer.style.height = `${{Math.floor(viewport.height)}}px`;
        }}
        context.setTransform(outputScale, 0, 0, outputScale, 0, 0);
        renderTask = page.render({{canvasContext: context, viewport}});
        const textContent = textLayer ? page.getTextContent() : Promise.resolve(null);
        try {{
          await renderTask.promise;
          const resolvedTextContent = await textContent;
          if (textLayer && resolvedTextContent) {{
            textLayerTask = new pdfjsLib.TextLayer({{
              textContentSource: resolvedTextContent,
              container: textLayer,
              viewport
            }});
            await textLayerTask.render();
            applyPdfSearchHighlights();
          }}
        }} catch (error) {{
          if (error?.name !== 'RenderingCancelledException') throw error;
        }} finally {{
          renderTask = null;
          textLayerTask = null;
          setLoading(false);
        }}
        setStatus(`第 ${{pageNumber}} / ${{pdfDoc.numPages}} 页 · ${{Math.round(scale * 100)}}%`);
        updateButtons();
      }}
      window.peerassistPdfRenderCurrentPage = () => {{
        if (!pdfDoc) return Promise.resolve();
        fitWidth = true;
        return renderPage().catch(() => {{}});
      }};

      pdfjsLib.getDocument('/paper.pdf').promise
        .then((document) => {{
          pdfDoc = document;
          buildPdfPageRail(document.numPages);
          renderPdfOutline().catch(() => {{
            if (outlineEmpty) outlineEmpty.hidden = false;
            setOutlineStatus('目录读取失败');
          }});
          updateButtons();
          renderPdfSearchResults();
          return renderPage();
        }})
        .catch((error) => {{
          setLoading(false);
          if (fallback) fallback.hidden = false;
          setStatus(`PDF 渲染失败：${{error.message || '未知错误'}}`);
        }});

      reader.querySelectorAll('[data-pdf-action]').forEach((button) => {{
        button.addEventListener('click', () => {{
          const action = button.dataset.pdfAction;
          if (!pdfDoc) return;
          if (action === 'prev') pageNumber = Math.max(1, pageNumber - 1);
          if (action === 'next') pageNumber = Math.min(pdfDoc.numPages, pageNumber + 1);
          if (action === 'zoom-out') {{
            fitWidth = false;
            scale = Math.max(0.6, scale - 0.15);
          }}
          if (action === 'zoom-in') {{
            fitWidth = false;
            scale = Math.min(2.4, scale + 0.15);
          }}
          if (action === 'fit') fitWidth = true;
          renderPage().catch((error) => {{
            setLoading(false);
            if (fallback) fallback.hidden = false;
            setStatus(`PDF 渲染失败：${{error.message || '未知错误'}}`);
          }});
        }});
      }});

      if (searchInput) {{
        searchInput.addEventListener('input', () => {{
          schedulePdfSearch(searchInput.value);
        }});
        searchInput.addEventListener('keydown', (event) => {{
          if (event.key === 'Enter') {{
            event.preventDefault();
            if (normalizePdfSearch(searchInput.value) === normalizePdfSearch(pdfSearchQuery) && pdfSearchMatches.length > 0) {{
              goToPdfSearchMatch(pdfSearchCursor + 1).catch(() => {{}});
            }} else {{
              runPdfSearch(searchInput.value).catch((error) => {{
                setPdfSearchStatus(`检索失败：${{error.message || '未知错误'}}`);
              }});
            }}
          }}
        }});
      }}

      if (searchPrev) {{
        searchPrev.addEventListener('click', () => {{
          goToPdfSearchMatch(pdfSearchCursor - 1).catch(() => {{}});
        }});
      }}

      if (searchNext) {{
        searchNext.addEventListener('click', () => {{
          goToPdfSearchMatch(pdfSearchCursor + 1).catch(() => {{}});
        }});
      }}

      if (searchClear) {{
        searchClear.addEventListener('click', () => {{
          if (searchInput) searchInput.value = '';
          pdfSearchRunId += 1;
          pdfSearchQuery = '';
          pdfSearchMatches = [];
          pdfSearchCursor = -1;
          renderPdfSearchResults();
          applyPdfSearchHighlights();
          showToast('已清空 PDF 原文检索');
        }});
      }}

      let resizeTimer = null;
      window.addEventListener('resize', () => {{
        if (!fitWidth || !pdfDoc) return;
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(() => {{
          renderPage().catch(() => {{}});
        }}, 180);
      }});
    }}
  </script>
</body>
</html>
"""


def create_confirmation_server(
    *, run_dir: Path, paper_id: str, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    handler = _handler_factory(run_dir=Path(run_dir), paper_id=paper_id)
    return ThreadingHTTPServer((host, port), handler)


def _discover_source_pdf(*, run_dir: Path, paper_id: str) -> Path | None:
    aliases = _paper_pdf_aliases(paper_id)
    candidates: list[Path] = []
    direct_names = [
        "paper.pdf",
        "source.pdf",
        "manuscript.pdf",
        "input.pdf",
        f"{paper_id}.pdf",
    ]
    for name in direct_names:
        candidates.append(run_dir / name)

    search_roots = [run_dir, *list(run_dir.parents[:5])]
    for source_root in _evidence_source_roots(run_dir):
        search_roots.extend([source_root, *list(source_root.parents[:3])])
    seen_roots: set[Path] = set()
    for root in search_roots:
        if root in seen_roots:
            continue
        seen_roots.add(root)
        for folder_name in ("pdfs", "pdf", "inputs", "input", "source", ""):
            folder = root / folder_name if folder_name else root
            if not folder.is_dir():
                continue
            candidates.extend(sorted(folder.glob("*.pdf")))

    seen_candidates: set[Path] = set()
    existing: list[Path] = []
    for candidate in candidates:
        if candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        if candidate.is_file():
            existing.append(candidate)
    if not existing:
        return None

    for candidate in existing:
        normalized_name = _normalize_pdf_key(candidate.stem)
        if normalized_name in aliases:
            return candidate
    if len(existing) == 1:
        return existing[0]
    return None


def _evidence_source_roots(run_dir: Path) -> list[Path]:
    ledger_path = run_dir / "stages" / "peerassist" / "evidence_ledger.json"
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    rows = payload.get("items") if isinstance(payload.get("items"), list) else []
    roots: list[Path] = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        source_path = row.get("source_path")
        if not source_path:
            continue
        roots.append(Path(str(source_path)).parent)
    return roots


def _paper_pdf_aliases(paper_id: str) -> set[str]:
    raw = paper_id.strip()
    aliases = {
        _normalize_pdf_key(raw),
        _normalize_pdf_key(raw.replace("arxiv_", "")),
        _normalize_pdf_key(raw.replace("_v", "v")),
        _normalize_pdf_key(raw.replace("arxiv_", "").replace("_v", "v")),
        _normalize_pdf_key(raw.replace("arxiv_", "").replace("_", ".")),
        _normalize_pdf_key(raw.replace("arxiv_", "").replace("_v", "v").replace("_", ".")),
    }
    return {alias for alias in aliases if alias}


def _normalize_pdf_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _run_agent_review(
    *, run_dir: Path, selected_text: str, paper_id: str, state: dict[str, Any], review_mode: str = "fast"
) -> dict[str, Any]:
    selected_text = selected_text.strip()
    review_mode = _normalize_review_mode(review_mode)
    config = _resolve_model_config()
    api_key = _resolve_model_api_key()
    if not api_key:
        raise ValueError("模型 API Key 未配置。请在服务环境变量中设置 PEERASSIST_OPENAI_API_KEY。")
    context = _build_full_paper_review_context(
        run_dir=run_dir,
        paper_id=paper_id,
        selected_text=selected_text,
        state=state,
        review_mode=review_mode,
    )
    response = _chat_completion(
        api_key=api_key,
        base_url=config["base_url"],
        model=config["model"],
        messages=[
            {
                "role": "system",
                "content": (
                    "你是 PeerAssist 的证据忠实论文审稿辅助智能体。你不能替代审稿人作录用决定，"
                    "不能使用造假、实锤、定罪式语言。所有主要意见必须绑定原文证据位置，"
                    "包含影响、可能的善意解释和作者可执行修改建议。证据不足时只能写待人工核查。"
                ),
            },
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
    )
    response_payload = _extract_agent_review_payload(response)
    report_markdown = str(response_payload.get("report_markdown") or response).strip()
    draft_path = peerassist_stage_dir(run_dir) / "agent_review_draft.md"
    draft_path.write_text(report_markdown + "\n", encoding="utf-8")
    structured = _persist_agent_review_concerns(
        run_dir=run_dir,
        paper_id=paper_id,
        model=config["model"],
        response_text=response,
    )
    return {
        "schema_version": "peerassist.agent_review_result.v1",
        "model": config["model"],
        "base_url": config["base_url"],
        "review_mode": review_mode,
        "selected_text_chars": len(selected_text),
        "context": context["context_summary"],
        "draft_path": str(draft_path),
        **structured,
        "suggestion": report_markdown,
    }


def _normalize_review_mode(value: str) -> str:
    mode = value.strip().lower()
    return mode if mode in {"fast", "standard", "deep"} else "fast"


def _persist_agent_review_concerns(
    *, run_dir: Path, paper_id: str, model: str, response_text: str
) -> dict[str, Any]:
    out_dir = peerassist_stage_dir(run_dir)
    payload = _extract_agent_review_payload(response_text)
    concern_rows = payload.get("concerns") if isinstance(payload.get("concerns"), list) else []
    generated = [
        concern.model_dump(mode="json")
        for concern in _concerns_from_agent_review_rows(
            rows=concern_rows,
            existing_count=_existing_llm_concern_count(out_dir / "peerassist_concerns.json"),
            model=model,
        )
    ]
    if not generated:
        return {
            "structured_concern_count": 0,
            "queue_items": _queue_item_count(out_dir / "confirmation_review_queue.json"),
            "concerns_path": str(out_dir / "peerassist_concerns.json"),
            "queue_path": str(out_dir / "confirmation_review_queue.json"),
        }

    concerns_path = out_dir / "peerassist_concerns.json"
    existing_payload = read_json_safely(concerns_path)
    existing = existing_payload.get("concerns") if isinstance(existing_payload.get("concerns"), list) else []
    all_concerns = [row for row in existing if isinstance(row, dict)] + generated
    write_json_file(
        concerns_path,
        {
            "schema_version": "peerassist.concerns.v1",
            "mode": str(existing_payload.get("mode") or "fast"),
            "paper_id": paper_id,
            "concerns": all_concerns,
        },
    )

    evidence_lookup = _evidence_lookup_from_ledger(out_dir / "evidence_ledger.json")
    typed_concerns = [Concern.model_validate(row) for row in all_concerns]
    bundle = build_confirmation_bundle(concerns=typed_concerns, evidence_lookup=evidence_lookup)
    bundle_path = out_dir / "confirmation_bundle.json"
    queue_path = out_dir / "confirmation_review_queue.json"
    write_json_file(bundle_path, bundle)
    queue = build_confirmation_review_queue(bundle)
    write_json_file(queue_path, queue)
    return {
        "structured_concern_count": len(generated),
        "queue_items": len(queue.get("items") if isinstance(queue.get("items"), list) else []),
        "concerns_path": str(concerns_path),
        "queue_path": str(queue_path),
        "bundle_path": str(bundle_path),
    }


def _extract_agent_review_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    candidates = [stripped]
    if "```" in stripped:
        parts = stripped.split("```")
        candidates.extend(part.strip() for part in parts if "{" in part and "}" in part)
    for candidate in candidates:
        if candidate.startswith("json"):
            candidate = candidate[4:].strip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start < 0 or end <= start:
                continue
            try:
                payload = json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
        return payload if isinstance(payload, dict) else {}
    return {}


def _concerns_from_agent_review_rows(
    *, rows: list[Any], existing_count: int, model: str
) -> list[Concern]:
    concerns: list[Concern] = []
    for offset, row in enumerate(rows, start=existing_count + 1):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        evidence_ids = [str(item) for item in row.get("evidence_ids", []) if str(item).strip()]
        impact = str(row.get("impact") or "").strip()
        benign_explanation = str(row.get("benign_explanation") or "").strip()
        author_action = str(row.get("author_action") or "").strip()
        if not title or not evidence_ids or not impact or not author_action:
            continue
        concerns.append(
            Concern(
                id=str(row.get("id") or f"concern_llm_review_{offset:03d}"),
                level=_safe_concern_level(str(row.get("level") or "")),
                category=str(row.get("category") or "full_paper_review"),
                title=title,
                evidence_ids=evidence_ids,
                impact=impact,
                benign_explanation=benign_explanation
                or "模型未给出充分善意解释，需审稿人复核。",
                author_action=author_action,
                status=ConcernStatus.PENDING_HUMAN_CONFIRMATION,
                source_agent_ids=["llm_full_paper_review_agent"],
                metadata={"model": model, "source": "agent_review"},
            )
        )
    return concerns


def _safe_concern_level(value: str) -> ConcernLevel:
    try:
        return ConcernLevel(value)
    except ValueError:
        return ConcernLevel.CLARIFICATION_NEEDED


def _existing_llm_concern_count(path: Path) -> int:
    payload = read_json_safely(path)
    rows = payload.get("concerns") if isinstance(payload.get("concerns"), list) else []
    return sum(
        1
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "").startswith("concern_llm_review_")
    )


def _queue_item_count(path: Path) -> int:
    payload = read_json_safely(path)
    rows = payload.get("items") if isinstance(payload.get("items"), list) else []
    return len(rows)


def _evidence_lookup_from_ledger(path: Path) -> dict[str, str]:
    payload = read_json_safely(path)
    rows = payload.get("items") if isinstance(payload.get("items"), list) else []
    lookup: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        evidence_id = str(row.get("id") or "").strip()
        if evidence_id:
            lookup[evidence_id] = str(row.get("locator") or evidence_id)
    return lookup


def _build_full_paper_review_context(
    *, run_dir: Path, paper_id: str, selected_text: str, state: dict[str, Any], review_mode: str
) -> dict[str, Any]:
    out_dir = peerassist_stage_dir(run_dir)
    ledger_payload = read_json_safely(out_dir / "evidence_ledger.json")
    checks_payload = read_json_safely(out_dir / "deterministic_checks.json")
    agent_payload = read_json_safely(out_dir / "agent_results.json")
    queue_payload = read_json_safely(out_dir / "confirmation_review_queue.json")
    ledger_items = ledger_payload.get("items") if isinstance(ledger_payload.get("items"), list) else []
    checks = checks_payload.get("checks") if isinstance(checks_payload.get("checks"), list) else []
    agent_results = agent_payload.get("results") if isinstance(agent_payload.get("results"), list) else []
    queue_items = queue_payload.get("items") if isinstance(queue_payload.get("items"), list) else []
    return {
        "schema_version": "peerassist.full_paper_review_prompt.v1",
        "paper_id": paper_id,
        "review_mode": review_mode,
        "review_mode_policy": {
            "fast": "优先覆盖主要证据、确定性核查和当前人工队列，输出精炼但可执行的审稿草稿。",
            "standard": "在快速模式基础上补充结构、方法、统计、图表、引用和复现维度的均衡检查。",
            "deep": "在标准模式基础上更严格地检查补充材料、复现线索、反方解释和证据缺口。",
        }.get(review_mode, "优先覆盖主要证据、确定性核查和当前人工队列。"),
        "methodology": {
            "pipeline": [
                "解析层",
                "证据台账",
                "确定性检查",
                "多代理审核",
                "反方解释",
                "意见整合",
                "人工确认",
                "报告导出",
            ],
            "review_dimensions": ["结构", "方法", "统计", "图表", "引用", "伦理与复现"],
            "language_rules": [
                "只输出审稿线索，不自动给出录用或拒稿决定",
                "避免造假、实锤、定罪式表达",
                "每条主要意见必须有证据位置、影响、善意解释、作者行动建议",
                "证据不足时标记为待人工核查",
            ],
        },
        "context_summary": {
            "evidence_items": len(ledger_items),
            "deterministic_checks": len(checks),
            "agent_results": len(agent_results),
            "queue_items": len(queue_items),
            "selected_text_chars": len(selected_text),
        },
        "paper_outline_and_evidence": _summarize_evidence_for_review(ledger_items),
        "deterministic_checks": _summarize_checks_for_review(checks),
        "local_agent_results": _summarize_agent_results_for_review(agent_results),
        "existing_confirmation_queue": queue_items[:8],
        "reviewer_focus_text": selected_text[:2000],
        "output_contract": {
            "format": "strict_json_only",
            "schema": {
                "report_markdown": "中文审稿辅助报告 Markdown，包含论文概要、重点阅读路线、主要意见、次要意见、编辑关注、系统局限。",
                "concerns": [
                    {
                        "level": "major_concern | minor_concern | clarification_needed | editor_note",
                        "category": "structure | methodology | statistics | figure_table | citation | reproducibility | ethics | other",
                        "title": "问题标题",
                        "evidence_ids": ["必须来自 paper_outline_and_evidence 中的 id"],
                        "impact": "为什么影响论文结论、清晰度或可复现性",
                        "benign_explanation": "至少一种可能的善意解释",
                        "author_action": "建议作者如何修改或补充",
                    }
                ],
            },
        },
        "task": (
            "请基于整篇论文证据台账和已有确定性/本地代理结果生成中文审稿辅助报告。"
            "只返回一个 JSON 对象，不要返回 Markdown 代码围栏，不要添加 JSON 之外的解释。"
            "concerns 中每条意见必须绑定 evidence_ids；证据不足的问题不要写入 concerns，可在 report_markdown 的系统局限中说明。"
        ),
    }


def read_json_safely(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _summarize_evidence_for_review(rows: list[Any]) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    preferred_types = {
        "section",
        "text_span",
        "figure_caption",
        "table",
        "table_cell",
        "citation",
        "reference",
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_type = str(row.get("type") or "")
        text = str(row.get("text") or "").strip()
        if item_type not in preferred_types or not text:
            continue
        summaries.append(
            {
                "id": str(row.get("id") or ""),
                "type": item_type,
                "locator": str(row.get("locator") or ""),
                "section": str(row.get("section") or ""),
                "text": text[:500],
            }
        )
        if len(summaries) >= 80:
            break
    return summaries


def _summarize_checks_for_review(rows: list[Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for row in rows[:40]:
        if not isinstance(row, dict):
            continue
        summaries.append(
            {
                "id": str(row.get("id") or ""),
                "kind": str(row.get("kind") or ""),
                "applicability": str(row.get("applicability") or ""),
                "status": str(row.get("status") or ""),
                "evidence_ids": row.get("evidence_ids") if isinstance(row.get("evidence_ids"), list) else [],
                "message": str(row.get("message") or "")[:400],
                "benign_explanations": row.get("benign_explanations")
                if isinstance(row.get("benign_explanations"), list)
                else [],
            }
        )
    return summaries


def _summarize_agent_results_for_review(rows: list[Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        drafts = row.get("drafts") if isinstance(row.get("drafts"), list) else []
        summaries.append(
            {
                "agent_id": str(row.get("agent_id") or ""),
                "status": str(row.get("status") or ""),
                "draft_count": len(drafts),
                "warnings": row.get("warnings") if isinstance(row.get("warnings"), list) else [],
                "drafts": drafts[:5],
            }
        )
    return summaries


def _chat_completion(
    *, api_key: str, base_url: str, model: str, messages: list[dict[str, str]]
) -> str:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 900,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=_model_request_timeout_seconds()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:500]
        raise RuntimeError(f"模型服务返回 {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"模型服务连接失败: {exc.reason}") from exc
    choices = payload.get("choices") if isinstance(payload, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    content = message.get("content") if isinstance(message, dict) else ""
    if not content:
        raise RuntimeError("模型服务未返回可用审稿建议。")
    return str(content)


def _model_request_timeout_seconds() -> float:
    raw = os.getenv("PEERASSIST_OPENAI_TIMEOUT_SECONDS", "240").strip()
    try:
        timeout = float(raw)
    except ValueError:
        return 240
    return timeout if timeout > 0 else 240


def _resolve_model_api_key() -> str:
    return (
        os.getenv("PEERASSIST_OPENAI_API_KEY")
        or os.getenv("EXECUTION_OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()


def _resolve_model_config() -> dict[str, str]:
    api_key = _resolve_model_api_key()
    base_url = (
        os.getenv("PEERASSIST_OPENAI_BASE_URL")
        or os.getenv("EXECUTION_OPENAI_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://deepkey.top/v1"
    ).strip()
    model = (
        os.getenv("PEERASSIST_OPENAI_MODEL")
        or os.getenv("EXECUTION_OPENAI_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-5.4"
    ).strip()
    return {
        "provider": "openai-compatible",
        "model": model,
        "base_url": base_url,
        "api_key_configured": "true" if api_key else "false",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve a local PeerAssist confirmation review console.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    server = create_confirmation_server(
        run_dir=Path(args.run_dir),
        paper_id=args.paper_id,
        host=args.host,
        port=args.port,
    )
    url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    print(json.dumps({"url": url, "run_dir": str(Path(args.run_dir))}, ensure_ascii=False))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _handler_factory(*, run_dir: Path, paper_id: str) -> type[BaseHTTPRequestHandler]:
    class ConfirmationHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/" or self.path.startswith("/?"):
                self._send_html(render_confirmation_page(run_dir=run_dir, paper_id=paper_id))
                return
            if path == "/paper.pdf":
                source_pdf_path = _discover_source_pdf(run_dir=run_dir, paper_id=paper_id)
                if source_pdf_path is None:
                    self.send_error(404, "source PDF not found")
                    return
                self._send_pdf(source_pdf_path)
                return
            if path == "/api/state":
                self._send_json(load_confirmation_state(run_dir=run_dir))
                return
            if path == "/api/events":
                self._send_sse_state(load_confirmation_state(run_dir=run_dir))
                return
            self.send_error(404, "not found")

        def do_HEAD(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/paper.pdf":
                source_pdf_path = _discover_source_pdf(run_dir=run_dir, paper_id=paper_id)
                if source_pdf_path is None:
                    self.send_error(404, "source PDF not found")
                    return
                self._send_pdf(source_pdf_path, head_only=True)
                return
            if path == "/" or self.path.startswith("/?"):
                self._send_html(
                    render_confirmation_page(run_dir=run_dir, paper_id=paper_id),
                    head_only=True,
                )
                return
            self.send_error(404, "not found")

        def do_POST(self) -> None:
            if self.path == "/api/agent-review":
                try:
                    payload = self._read_json()
                    state = load_confirmation_state(run_dir=run_dir)
                    result = _run_agent_review(
                        run_dir=run_dir,
                        selected_text=str(payload.get("selected_text") or ""),
                        review_mode=str(payload.get("review_mode") or "fast"),
                        paper_id=paper_id,
                        state=state,
                    )
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                self._send_json(result)
                return
            if self.path != "/api/decision":
                self.send_error(404, "not found")
                return
            try:
                payload = self._read_json()
                result = apply_confirmation_decision(
                    run_dir=run_dir,
                    paper_id=paper_id,
                    concern_id=str(payload.get("concern_id") or ""),
                    action=str(payload.get("action") or ""),
                    reviewer_id=str(payload.get("reviewer_id") or "local-reviewer"),
                    timestamp=str(payload.get("timestamp") or ""),
                    previous_text=str(payload.get("previous_text") or ""),
                    new_text=str(payload.get("new_text") or ""),
                    reason=str(payload.get("reason") or ""),
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(result)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw or "{}")
            return payload if isinstance(payload, dict) else {}

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, text: str, *, status: int = 200, head_only: bool = False) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if head_only:
                return
            self.wfile.write(body)

        def _send_pdf(self, path: Path, *, head_only: bool = False) -> None:
            try:
                body = path.read_bytes()
            except FileNotFoundError:
                self.send_error(404, "source PDF not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if head_only:
                return
            self.wfile.write(body)

        def _send_sse_state(self, payload: dict[str, Any]) -> None:
            body = _sse_stream(payload)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ConfirmationHandler


def _sse_stream(payload: dict[str, Any]) -> bytes:
    state_data = json.dumps(payload, ensure_ascii=False)
    heartbeat = json.dumps(
        {
            "schema_version": "peerassist.confirmation_stream_event.v1",
            "event": "heartbeat",
            "pending_count": int(payload.get("pending_count") or 0),
            "actions_count": int(payload.get("actions_count") or 0),
        },
        ensure_ascii=False,
    )
    done = json.dumps(
        {
            "schema_version": "peerassist.confirmation_stream_event.v1",
            "event": "done",
        },
        ensure_ascii=False,
    )
    return (
        "retry: 15000\n"
        f"event: state\ndata: {state_data}\n\n"
        f"event: heartbeat\ndata: {heartbeat}\n\n"
        f"event: done\ndata: {done}\n\n"
    ).encode()


def _render_item(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
    first_evidence = next((row for row in evidence if isinstance(row, dict)), {})
    concern_id = str(item.get("id") or "")
    evidence_anchor = str(first_evidence.get("id") or concern_id)
    pdf_page = _evidence_pdf_page(first_evidence)
    pdf_page_attr = f' data-pdf-page="{pdf_page}"' if pdf_page is not None else ""
    level = str(item.get("level") or "")
    category = str(item.get("category") or "")
    status = str(item.get("status") or "")
    title = _localized_copy(str(item.get("title") or "未命名关注点"))
    impact = _localized_copy(str(item.get("impact") or ""))
    benign_explanation = _localized_copy(str(item.get("benign_explanation") or ""))
    author_action = _localized_copy(str(item.get("author_action") or ""))
    search_blob = " ".join(
        [
            concern_id,
            level,
            category,
            status,
            title,
            impact,
            benign_explanation,
            author_action,
            " ".join(str(row.get("id") or "") for row in evidence if isinstance(row, dict)),
            " ".join(str(row.get("locator") or "") for row in evidence if isinstance(row, dict)),
        ]
    )
    evidence_rows = "".join(
        (
            f'<div class="evidence-row"{_evidence_pdf_page_attr(row)}>'
            f"<code>{html.escape(str(row.get('id', '')))}</code>"
            f"<span>{html.escape(str(row.get('locator', '')))}</span>"
            "</div>"
        )
        for row in evidence
        if isinstance(row, dict)
    )
    if not evidence_rows:
        evidence_rows = '<div class="evidence-row"><span>暂无绑定证据。</span></div>'
    actions = item.get("allowed_actions") if isinstance(item.get("allowed_actions"), list) else []
    buttons = "".join(
        f'<button type="button" data-action="{html.escape(str(action))}">{html.escape(_localized_action(str(action)))}</button>'
        for action in actions
    )
    buttons = (
        f'<button type="button" data-paper-target="{html.escape(evidence_anchor, quote=True)}" '
        f'data-paper-concern="{html.escape(concern_id, quote=True)}"{pdf_page_attr}>查看原文高亮</button>'
        + buttons
    )
    source_agents = item.get("source_agent_ids") if isinstance(item.get("source_agent_ids"), list) else []
    source_agent_tags = "".join(
        f'<span class="tag">{html.escape(_display_name(str(agent_id)))}</span>'
        for agent_id in source_agents
    )
    previous_text = html.escape(str(item.get("author_action") or ""), quote=True)
    return f"""
<section class="item" id="concern-{html.escape(concern_id, quote=True)}" data-concern-id="{html.escape(concern_id, quote=True)}" data-previous-text="{previous_text}" data-concern-level="{html.escape(level, quote=True)}" data-concern-category="{html.escape(category, quote=True)}" data-concern-status="{html.escape(status, quote=True)}" data-concern-title="{html.escape(title, quote=True)}" data-concern-search="{html.escape(search_blob, quote=True)}"{pdf_page_attr}>
  <div>
    <div class="tags">
      <span class="tag">{html.escape(_localized_status(status))}</span>
      <span class="tag level">{html.escape(_localized_level(level))}</span>
      <span class="tag">{html.escape(_localized_category(category))}</span>
      {source_agent_tags}
    </div>
    <h2>{html.escape(title)}</h2>
    <p class="copy-block"><span class="label">影响</span><br>{html.escape(impact)}</p>
    <p class="copy-block"><span class="label">可能的良性解释</span><br>{html.escape(benign_explanation)}</p>
    <div class="evidence"><span class="label">证据</span>{evidence_rows}</div>
    <textarea aria-label="建议作者处理方式">{html.escape(author_action)}</textarea>
  </div>
  <div class="actions">{buttons}</div>
</section>
"""


def _evidence_pdf_page_attr(row: dict[str, Any]) -> str:
    page = _evidence_pdf_page(row)
    return f' data-pdf-page="{page}"' if page is not None else ""


def _evidence_pdf_page(row: dict[str, Any]) -> int | None:
    raw_page = row.get("page")
    if isinstance(raw_page, int) and raw_page > 0:
        return raw_page
    if isinstance(raw_page, str) and raw_page.strip().isdigit():
        parsed = int(raw_page.strip())
        return parsed if parsed > 0 else None
    locator = str(row.get("locator") or "")
    match = re.search(r"(?:page|p\.?|第)\s*(\d+)", locator, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    evidence_id = str(row.get("id") or "")
    match = re.match(r"P(\d+)-", evidence_id, flags=re.IGNORECASE)
    if match:
        parsed = int(match.group(1))
        return parsed if parsed > 0 else None
    return None


def _evidence_count(items: list[Any]) -> int:
    evidence_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        for row in evidence:
            if isinstance(row, dict) and row.get("id"):
                evidence_ids.add(str(row["id"]))
    return len(evidence_ids)


def _event_status_count(events: list[Any], status: str) -> int:
    expected = status.replace(" ", "_").lower()
    return sum(
        1
        for row in events
        if isinstance(row, dict)
        and str(row.get("status") or "").replace(" ", "_").lower() == expected
    )


def _render_evidence_chain_matrix(
    items: list[Any], *, events: list[Any], invocations: list[Any]
) -> str:
    concerns = [item for item in items if isinstance(item, dict)]
    if not concerns:
        return """
<section class="panel evidence-chain" data-panel="evidence-chain-matrix">
  <div class="evidence-chain-head">
    <div>
      <p class="evidence-chain-title">证据链矩阵</p>
      <div class="evidence-chain-copy">暂无待展示的审稿关注点。</div>
    </div>
    <span class="evidence-chain-count">0 条</span>
  </div>
</section>
"""
    rows: list[str] = [
        """
  <div class="evidence-chain-row header" aria-hidden="true">
    <span>关注点</span><span>PDF 页</span><span>证据数</span><span>来源代理</span><span>调用状态</span><span>人工状态</span><span>操作</span>
  </div>
"""
    ]
    for item in concerns:
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence_rows = [row for row in evidence if isinstance(row, dict)]
        first_evidence = evidence_rows[0] if evidence_rows else {}
        evidence_ids = {
            str(row.get("id"))
            for row in evidence_rows
            if isinstance(row.get("id"), str) and str(row.get("id")).strip()
        }
        concern_id = str(item.get("id") or "")
        title = _localized_copy(str(item.get("title") or "未命名关注点"))
        if len(title) > 54:
            title = title[:51].rstrip() + "..."
        pdf_page = _evidence_pdf_page(first_evidence)
        pdf_page_text = f"第 {pdf_page} 页" if pdf_page else "未绑定"
        source_agents = item.get("source_agent_ids") if isinstance(item.get("source_agent_ids"), list) else []
        agent_text = "、".join(_display_name(str(agent_id)) for agent_id in source_agents[:2])
        if len(source_agents) > 2:
            agent_text += f" +{len(source_agents) - 2}"
        if not agent_text:
            agent_text = "待归属"
        call_status = _evidence_chain_call_status(
            evidence_ids=evidence_ids,
            source_agents={str(agent_id) for agent_id in source_agents},
            events=events,
            invocations=invocations,
        )
        call_label, call_class = _evidence_chain_status_badge(call_status)
        human_status = _localized_status(str(item.get("status") or "pending_human_confirmation"))
        human_class = (
            "ok"
            if str(item.get("status") or "").lower() in {"confirmed", "rewritten", "downgraded"}
            else "warn"
        )
        pdf_page_attr = f' data-pdf-page="{pdf_page}"' if pdf_page else ""
        evidence_anchor = str(first_evidence.get("id") or concern_id)
        rows.append(
            f"""
  <div class="evidence-chain-row" data-evidence-chain-row data-concern-id="{html.escape(concern_id, quote=True)}"{pdf_page_attr}>
    <div>
      <div class="chain-title">{html.escape(title)}</div>
      <div class="chain-muted">{html.escape(str(first_evidence.get('locator') or '未标注位置'))}</div>
    </div>
    <span class="chain-pill">{html.escape(pdf_page_text)}</span>
    <span class="chain-pill ok">{len(evidence_rows)} 条</span>
    <span class="chain-pill">{html.escape(agent_text)}</span>
    <span class="chain-pill {call_class}">{html.escape(call_label)}</span>
    <span class="chain-pill {human_class}">{html.escape(human_status)}</span>
    <div class="chain-actions">
      <button class="chain-action" type="button" data-jump-concern="{html.escape(concern_id, quote=True)}" data-paper-target="{html.escape(evidence_anchor, quote=True)}"{pdf_page_attr}>定位</button>
    </div>
  </div>
"""
        )
    return f"""
<section class="panel evidence-chain" data-panel="evidence-chain-matrix" aria-label="证据链矩阵">
  <div class="evidence-chain-head">
    <div>
      <p class="evidence-chain-title">证据链矩阵</p>
      <div class="evidence-chain-copy">把每条关注点的 PDF 位置、证据、代理来源、调用状态和人工状态放在同一行。</div>
    </div>
    <span class="evidence-chain-count">{len(concerns)} 条关注点</span>
  </div>
  <div class="evidence-chain-grid">
    {''.join(rows)}
  </div>
</section>
"""


def _evidence_chain_call_status(
    *,
    evidence_ids: set[str],
    source_agents: set[str],
    events: list[Any],
    invocations: list[Any],
) -> str:
    statuses: list[str] = []
    for row in [*events, *invocations]:
        if not isinstance(row, dict):
            continue
        row_evidence = row.get("evidence_ids") if isinstance(row.get("evidence_ids"), list) else []
        row_evidence_ids = {str(value) for value in row_evidence if str(value).strip()}
        row_agent = str(row.get("agent_id") or "")
        matches_evidence = bool(evidence_ids and row_evidence_ids & evidence_ids)
        matches_agent = bool(source_agents and row_agent in source_agents)
        if matches_evidence or matches_agent:
            statuses.append(str(row.get("status") or "").replace(" ", "_").lower())
    if not statuses:
        return "not_recorded"
    priority = [
        "failed",
        "approval_required",
        "running",
        "progress",
        "started",
        "queued",
        "completed",
    ]
    for status in priority:
        if status in statuses:
            return status
    return statuses[-1] or "not_recorded"


def _evidence_chain_status_badge(status: str) -> tuple[str, str]:
    if status == "failed":
        return "失败", "danger"
    if status == "approval_required":
        return "需批准", "warn"
    if status in {"running", "progress", "started", "queued"}:
        return "进行中", "warn"
    if status == "completed":
        return "已完成", "ok"
    return "未记录", "warn"


def _render_evidence_focus(items: list[Any]) -> str:
    rows: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _localized_copy(str(item.get("title") or "未命名关注点"))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        for row in evidence:
            if not isinstance(row, dict):
                continue
            evidence_id = str(row.get("id") or "").strip()
            if not evidence_id or evidence_id in seen:
                continue
            seen.add(evidence_id)
            rows.append(
                f"""
<div class="focus-row">
  <div class="focus-title"><code>{html.escape(evidence_id)}</code> · {html.escape(title)}</div>
  <div class="focus-meta">{html.escape(str(row.get('locator') or '未标注位置'))}</div>
</div>
"""
            )
            if len(rows) >= 5:
                break
        if len(rows) >= 5:
            break
    if not rows:
        rows.append('<div class="trace-summary">暂无证据焦点。</div>')
    return f'<div class="evidence-focus">{"".join(rows)}</div>'


def _render_model_entry(model_config: dict[str, str]) -> str:
    key_state = "已配置" if model_config["api_key_configured"] == "true" else "未配置"
    return f"""
      <section class="panel model-entry" data-panel="model-entry">
        <div class="panel-header">
          <p class="panel-title">智能体审稿入口</p>
          <div class="panel-subtitle">按方法论通读整篇论文：证据台账、确定性核查、多代理维度与人工确认</div>
        </div>
        <div class="model-config-list">
          <div class="model-config-row"><span>模型</span><code>{html.escape(model_config["model"])}</code></div>
          <div class="model-config-row"><span>Base URL</span><code>{html.escape(model_config["base_url"])}</code></div>
          <div class="model-config-row"><span>API Key</span><code>{html.escape(key_state)}</code></div>
        </div>
        <div class="agent-flow" aria-label="全篇智能审稿流程">
          <div class="agent-flow-step"><div class="agent-flow-index">01</div><div class="agent-flow-title">解析证据</div></div>
          <div class="agent-flow-step"><div class="agent-flow-index">02</div><div class="agent-flow-title">多代理核查</div></div>
          <div class="agent-flow-step"><div class="agent-flow-index">03</div><div class="agent-flow-title">结构化入队</div></div>
          <div class="agent-flow-step"><div class="agent-flow-index">04</div><div class="agent-flow-title">人工确认</div></div>
        </div>
        <div class="agent-review-box">
          <button class="agent-primary-button" type="button" data-agent-review-start>开始全篇智能审稿</button>
          <div class="agent-stream-box" data-agent-review-stream>系统将读取整篇论文证据台账、确定性核查和代理结果，生成可追溯审稿草稿；选中的 PDF 文字只作为额外关注点。</div>
        </div>
      </section>
"""


def _render_paper_review_surface(
    items: list[Any], *, paper_id: str, evidence_preview: list[Any], has_source_pdf: bool, events: list[Any]
) -> str:
    concerns = [item for item in items if isinstance(item, dict)]
    first_concern = concerns[0] if concerns else {}
    preview_rows = [row for row in evidence_preview if isinstance(row, dict)]
    first_preview = preview_rows[0] if preview_rows else {}
    first_title = _localized_copy(
        str(first_concern.get("title") or first_preview.get("text") or "待核查关注点")
    )
    first_impact = _localized_copy(
        str(first_concern.get("impact") or "当前真实论文片段已进入证据预览，等待进一步结构化核查。")
    )
    evidence = first_concern.get("evidence") if isinstance(first_concern.get("evidence"), list) else []
    first_evidence = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
    evidence_id = str(first_evidence.get("id") or first_preview.get("id") or "P01-L001")
    locator = str(first_evidence.get("locator") or first_preview.get("locator") or "p.1 line 1")
    paper_lines = _render_paper_preview_lines(preview_rows, fallback_title=first_title)
    pdf_link = (
        '<a class="paper-chip" href="/paper.pdf" target="_blank" rel="noreferrer">打开 PDF</a>'
        if has_source_pdf
        else '<span class="paper-chip">未发现源 PDF</span>'
    )
    source_document = (
        _render_source_pdf_viewer(events)
        if has_source_pdf
        else _render_extracted_text_preview(
            paper_id=paper_id,
            paper_lines=paper_lines,
            locator=locator,
            first_impact=first_impact,
        )
    )
    source_label = "原始 PDF 已导入" if has_source_pdf else "抽取文本预览"
    return f"""
<section class="panel paper-viewer" data-panel="paper-viewer" id="paper-viewer">
  <div class="panel-header">
    <p class="panel-title">论文原文 PDF</p>
    <div class="panel-subtitle">真实 PDF 阅读面优先；旁栏保留证据锚点、页边批注与人工确认队列联动</div>
  </div>
  <div class="paper-toolbar">
    <span class="paper-chip">{source_label}</span>
    <span class="paper-chip">批注 {len(concerns)}</span>
    <span class="paper-chip">证据锚点 {html.escape(evidence_id)}</span>
    {pdf_link}
    <a class="paper-chip" href="#review-queue">跳转队列</a>
    <button class="paper-chip focus-review-button" type="button" data-focus-review-toggle aria-pressed="false">专注审稿</button>
  </div>
  <div class="paper-canvas">
    {source_document}
  </div>
</section>
"""


def _render_source_pdf_viewer(events: list[Any] | None = None) -> str:
    return f"""
    <div class="source-pdf-shell" data-source-pdf-viewer data-pdf-reader>
      <div class="pdf-reader-bar" aria-label="PDF 阅读控制">
        <div class="pdf-control-group">
          <button class="pdf-icon-button" type="button" title="上一页" aria-label="上一页" data-pdf-action="prev">‹</button>
          <button class="pdf-icon-button" type="button" title="下一页" aria-label="下一页" data-pdf-action="next">›</button>
        </div>
        <div class="pdf-page-status" data-pdf-status>正在载入 PDF</div>
        <div class="pdf-control-group">
          <button class="pdf-icon-button" type="button" title="缩小" aria-label="缩小" data-pdf-action="zoom-out">−</button>
          <button class="pdf-icon-button" type="button" title="放大" aria-label="放大" data-pdf-action="zoom-in">＋</button>
          <button class="pdf-icon-button" type="button" title="适应宽度" aria-label="适应宽度" data-pdf-action="fit">⤢</button>
          <button class="inline-button" type="button" data-focus-review-toggle aria-pressed="false">专注审稿</button>
        </div>
      </div>
      <div class="pdf-page-rail" data-pdf-page-rail aria-label="PDF 页码导航">
        <span class="pdf-page-rail-label">PDF 页码导航</span>
      </div>
      <section class="pdf-reading-map" data-pdf-reading-map aria-label="PDF 目录与审稿索引">
        <div class="pdf-reading-panel">
          <div class="pdf-reading-head">
            <span>PDF 目录</span>
            <span class="pdf-reading-subtitle" data-pdf-outline-status>正在读取内置目录</span>
          </div>
          <div class="pdf-outline-list" data-pdf-outline-list></div>
          <div class="pdf-outline-empty" data-pdf-outline-empty hidden>此 PDF 未提供内置目录，可使用页码导航、原文检索和审稿索引继续定位。</div>
        </div>
        <div class="pdf-reading-panel">
          <div class="pdf-reading-head">
            <span>审稿索引</span>
            <span class="pdf-reading-subtitle">围绕当前页推进</span>
          </div>
          <div class="pdf-review-route">
            <div class="pdf-route-chip">
              <div class="pdf-route-label">当前页</div>
              <div class="pdf-route-value" data-pdf-route-current>等待 PDF</div>
            </div>
            <div class="pdf-route-chip">
              <div class="pdf-route-label">下一未处理</div>
              <div class="pdf-route-value" data-pdf-route-pending>等待队列</div>
            </div>
            <div class="pdf-route-chip">
              <div class="pdf-route-label">下一关注</div>
              <div class="pdf-route-value" data-pdf-route-concern>等待队列</div>
            </div>
          </div>
          <div class="pdf-reading-route-list" data-pdf-reading-route-list aria-label="重点阅读路线"></div>
          <div class="pdf-reading-route-empty" data-pdf-reading-route-empty>等待证据队列生成重点阅读路线。</div>
        </div>
      </section>
      <section class="pdf-search-strip" data-pdf-search-strip aria-label="PDF 原文检索">
        <label class="pdf-search-box">
          <span class="pdf-search-label">原文检索</span>
          <input class="pdf-search-input" type="search" data-pdf-search-input placeholder="搜索术语、指标、图表编号" aria-label="搜索 PDF 原文">
        </label>
        <div class="pdf-search-status" data-pdf-search-status>输入关键词后在整篇 PDF 中定位证据。</div>
        <div class="pdf-search-actions">
          <button class="inline-button" type="button" data-pdf-search-prev disabled>上一处</button>
          <button class="inline-button primary" type="button" data-pdf-search-next disabled>下一处</button>
          <button class="inline-button" type="button" data-pdf-search-clear>清空</button>
        </div>
        <div class="pdf-search-results" data-pdf-search-results>
          <span class="pdf-search-empty">等待检索关键词。</span>
        </div>
      </section>
      <div class="pdf-review-command-strip" data-pdf-review-command-strip aria-label="PDF 审稿命令条">
        <div class="pdf-command-label">PDF 审稿命令条</div>
        <div class="pdf-command-actions">
          <button class="pdf-command-button primary" type="button" data-pdf-review-command="agent-review">全篇审稿</button>
          <button class="pdf-command-button" type="button" data-pdf-review-command="selection-review">选区审稿</button>
          <button class="pdf-command-button" type="button" data-pdf-review-command="current-page">本页队列</button>
          <button class="pdf-command-button" type="button" data-pdf-review-command="next-pending">下一未处理</button>
          <button class="pdf-command-button" type="button" data-pdf-review-command="next-concern">下一关注</button>
          <button class="pdf-command-button" type="button" data-pdf-review-command="focus">专注模式</button>
        </div>
      </div>
      <section class="pdf-agent-dock" data-pdf-agent-dock data-review-mode="fast" aria-label="PDF 智能体审稿操作坞">
        <div class="pdf-agent-card">
          <div class="pdf-agent-kicker">智能体入口</div>
          <div class="pdf-agent-title">证据约束审稿</div>
          <div class="pdf-agent-copy">当前 PDF、证据台账、确定性核查与人工队列会一起进入审稿上下文。</div>
        </div>
        <div class="pdf-agent-card">
          <div class="pdf-agent-kicker">审稿模式</div>
          <div class="pdf-agent-copy">当前：<span data-pdf-agent-mode-label>快速</span></div>
          <div class="pdf-agent-mode-grid" role="group" aria-label="选择智能审稿模式">
            <button class="pdf-agent-mode" type="button" data-pdf-agent-mode="fast" aria-pressed="true">快速</button>
            <button class="pdf-agent-mode" type="button" data-pdf-agent-mode="standard" aria-pressed="false">标准</button>
            <button class="pdf-agent-mode" type="button" data-pdf-agent-mode="deep" aria-pressed="false">深度</button>
          </div>
        </div>
        <div class="pdf-agent-card">
          <div class="pdf-agent-kicker">立即动作</div>
          <div class="pdf-agent-action-grid">
            <button class="pdf-agent-action primary" type="button" data-pdf-agent-action="agent-review">全篇审稿</button>
            <button class="pdf-agent-action" type="button" data-pdf-agent-action="selection-review">选区审稿</button>
            <button class="pdf-agent-action" type="button" data-pdf-agent-action="current-page">本页队列</button>
          </div>
          <div class="pdf-agent-run-controls" data-pdf-agent-run-controls data-run-state="idle" aria-label="智能审稿任务控制">
            <div class="pdf-agent-run-state" data-pdf-agent-run-state>任务空闲，可启动审稿</div>
            <button class="inline-button" type="button" data-pdf-agent-cancel disabled>取消</button>
            <button class="inline-button" type="button" data-pdf-agent-retry disabled>重试</button>
          </div>
          <div class="pdf-agent-status-grid" aria-label="智能体审稿约束">
            <div class="pdf-agent-status-chip">
              <div class="pdf-agent-status-label">证据</div>
              <div class="pdf-agent-status-value">必绑定</div>
            </div>
            <div class="pdf-agent-status-chip">
              <div class="pdf-agent-status-label">人工</div>
              <div class="pdf-agent-status-value">逐条确认</div>
            </div>
            <div class="pdf-agent-status-chip">
              <div class="pdf-agent-status-label">输出</div>
              <div class="pdf-agent-status-value">中文草稿</div>
            </div>
          </div>
        </div>
        <div class="pdf-agent-context-manifest" data-pdf-agent-context-manifest aria-label="智能审稿上下文装载清单">
          <div class="pdf-agent-context-head">
            <span>审稿上下文装载</span>
            <span class="pdf-agent-context-subtitle">模型只读取可追溯来源；无证据内容进入待人工核查</span>
          </div>
          <div class="pdf-agent-context-list">
            <div class="pdf-agent-context-row" data-pdf-agent-context-item="pdf" data-context-state="ready">
              <div class="pdf-agent-context-label">PDF 原文</div>
              <div class="pdf-agent-context-value" data-pdf-agent-context-value="pdf">已导入，可浏览与选区</div>
              <div class="pdf-agent-context-status" data-pdf-agent-context-status="pdf">主阅读面</div>
            </div>
            <div class="pdf-agent-context-row" data-pdf-agent-context-item="evidence" data-context-state="ready">
              <div class="pdf-agent-context-label">证据台账</div>
              <div class="pdf-agent-context-value" data-pdf-agent-context-value="evidence">等待队列同步</div>
              <div class="pdf-agent-context-status" data-pdf-agent-context-status="evidence">必绑定</div>
            </div>
            <div class="pdf-agent-context-row" data-pdf-agent-context-item="checks" data-context-state="ready">
              <div class="pdf-agent-context-label">确定性核查</div>
              <div class="pdf-agent-context-value" data-pdf-agent-context-value="checks">数值/统计/引用线索</div>
              <div class="pdf-agent-context-status" data-pdf-agent-context-status="checks">先规则后模型</div>
            </div>
            <div class="pdf-agent-context-row" data-pdf-agent-context-item="tools" data-context-state="waiting">
              <div class="pdf-agent-context-label">MCP/Skills</div>
              <div class="pdf-agent-context-value" data-pdf-agent-context-value="tools">等待事件流同步</div>
              <div class="pdf-agent-context-status" data-pdf-agent-context-status="tools">可追溯调用</div>
            </div>
            <div class="pdf-agent-context-row" data-pdf-agent-context-item="selection" data-context-state="waiting">
              <div class="pdf-agent-context-label">当前选区</div>
              <div class="pdf-agent-context-value" data-pdf-agent-context-value="selection">未选择 PDF 文本</div>
              <div class="pdf-agent-context-status" data-pdf-agent-context-status="selection">可选关注点</div>
            </div>
            <div class="pdf-agent-context-row" data-pdf-agent-context-item="human" data-context-state="waiting">
              <div class="pdf-agent-context-label">人工确认</div>
              <div class="pdf-agent-context-value" data-pdf-agent-context-value="human">等待队列同步</div>
              <div class="pdf-agent-context-status" data-pdf-agent-context-status="human">逐条确认</div>
            </div>
          </div>
        </div>
        <div class="pdf-agent-phase-rail" data-pdf-agent-phase-rail data-active-phase="prepare" aria-label="PDF 智能审稿任务阶段">
          <div class="pdf-agent-phase-step" data-pdf-agent-phase="prepare" data-phase-state="active">
            <div class="pdf-agent-phase-index">01</div>
            <div class="pdf-agent-phase-title">准备上下文</div>
            <div class="pdf-agent-phase-copy" data-pdf-agent-phase-copy>等待审稿指令</div>
          </div>
          <div class="pdf-agent-phase-step" data-pdf-agent-phase="evidence" data-phase-state="pending">
            <div class="pdf-agent-phase-index">02</div>
            <div class="pdf-agent-phase-title">读取证据</div>
            <div class="pdf-agent-phase-copy" data-pdf-agent-phase-copy>证据台账与队列</div>
          </div>
          <div class="pdf-agent-phase-step" data-pdf-agent-phase="model" data-phase-state="pending">
            <div class="pdf-agent-phase-index">03</div>
            <div class="pdf-agent-phase-title">调用模型</div>
            <div class="pdf-agent-phase-copy" data-pdf-agent-phase-copy>证据约束生成</div>
          </div>
          <div class="pdf-agent-phase-step" data-pdf-agent-phase="queue" data-phase-state="pending">
            <div class="pdf-agent-phase-index">04</div>
            <div class="pdf-agent-phase-title">写回队列</div>
            <div class="pdf-agent-phase-copy" data-pdf-agent-phase-copy>结构化 concerns</div>
          </div>
          <div class="pdf-agent-phase-step" data-pdf-agent-phase="human" data-phase-state="pending">
            <div class="pdf-agent-phase-index">05</div>
            <div class="pdf-agent-phase-title">人工确认</div>
            <div class="pdf-agent-phase-copy" data-pdf-agent-phase-copy>逐条处理</div>
          </div>
        </div>
      </section>
      {_render_pdf_tool_trace_strip(events or [])}
      <section class="pdf-recovery-strip" data-pdf-recovery-strip data-recovery-state="idle" aria-label="PDF 智能审稿检查点与恢复">
        <div class="pdf-recovery-cell">
          <div class="pdf-recovery-label">检查点</div>
          <div class="pdf-recovery-value" data-pdf-recovery-value="status">等待首个检查点</div>
        </div>
        <div class="pdf-recovery-cell">
          <div class="pdf-recovery-label">最近调用</div>
          <div class="pdf-recovery-value" data-pdf-recovery-value="call">尚无调用</div>
        </div>
        <div class="pdf-recovery-cell">
          <div class="pdf-recovery-label">失败事件</div>
          <div class="pdf-recovery-value" data-pdf-recovery-value="failed">0 个失败事件</div>
        </div>
        <div class="pdf-recovery-cell">
          <div class="pdf-recovery-label">最近产物</div>
          <div class="pdf-recovery-value" data-pdf-recovery-value="artifact">暂无产物</div>
        </div>
        <div class="pdf-recovery-actions">
          <button class="inline-button" type="button" data-pdf-recovery-trace>查看追踪</button>
          <button class="inline-button" type="button" data-pdf-recovery-failed disabled>失败事件</button>
          <button class="inline-button" type="button" data-pdf-recovery-artifacts>产物区</button>
        </div>
      </section>
      <div class="pdf-runtime-pulse" data-pdf-runtime-pulse data-stream-source="connecting" aria-label="PDF 审稿运行脉冲">
        <div class="pdf-runtime-chip">
          <div class="pdf-runtime-label">事件流</div>
          <div class="pdf-runtime-value" data-pdf-runtime-state>任务流连接中</div>
        </div>
        <div class="pdf-runtime-chip">
          <div class="pdf-runtime-label">待确认</div>
          <div class="pdf-runtime-value" data-pdf-runtime-pending>--</div>
        </div>
        <div class="pdf-runtime-chip">
          <div class="pdf-runtime-label">工具事件</div>
          <div class="pdf-runtime-value" data-pdf-runtime-events>--</div>
        </div>
        <div class="pdf-runtime-chip">
          <div class="pdf-runtime-label">当前页</div>
          <div class="pdf-runtime-value" data-pdf-runtime-page>等待 PDF</div>
        </div>
      </div>
      <section class="pdf-human-gate" data-pdf-human-gate aria-label="PDF 人工确认闸门">
        <div>
          <div class="pdf-human-gate-head">
            <span class="pdf-human-gate-title">人工确认闸门</span>
            <span class="pdf-human-gate-copy" data-pdf-human-gate-copy>等待队列同步</span>
          </div>
          <div class="pdf-human-gate-grid">
            <div class="pdf-human-gate-chip">
              <div class="pdf-human-gate-label">待确认</div>
              <div class="pdf-human-gate-value" data-pdf-human-gate-value="pending">--</div>
            </div>
            <div class="pdf-human-gate-chip">
              <div class="pdf-human-gate-label">已处理</div>
              <div class="pdf-human-gate-value" data-pdf-human-gate-value="resolved">--</div>
            </div>
            <div class="pdf-human-gate-chip">
              <div class="pdf-human-gate-label">主要待处理</div>
              <div class="pdf-human-gate-value" data-pdf-human-gate-value="major">--</div>
            </div>
            <div class="pdf-human-gate-chip">
              <div class="pdf-human-gate-label">总队列</div>
              <div class="pdf-human-gate-value" data-pdf-human-gate-value="total">--</div>
            </div>
            <div class="pdf-human-gate-meter" aria-hidden="true"><span data-pdf-human-gate-meter></span></div>
          </div>
        </div>
        <div class="pdf-human-gate-actions">
          <button class="inline-button primary" type="button" data-pdf-human-gate-open>打开人工确认</button>
          <button class="inline-button" type="button" data-pdf-human-gate-next>下一未处理</button>
          <button class="inline-button" type="button" data-pdf-human-gate-queue>队列清单</button>
        </div>
      </section>
      <section class="pdf-evidence-gate" data-pdf-evidence-gate data-evidence-gate-state="empty" aria-label="PDF 证据绑定闸门">
        <div>
          <div class="pdf-evidence-gate-head">
            <span class="pdf-evidence-gate-title">证据绑定闸门</span>
            <span class="pdf-evidence-gate-copy" data-pdf-evidence-gate-copy>等待证据台账同步</span>
          </div>
          <div class="pdf-evidence-gate-grid">
            <div class="pdf-evidence-gate-chip">
              <div class="pdf-evidence-gate-label">总关注点</div>
              <div class="pdf-evidence-gate-value" data-pdf-evidence-gate-value="total">--</div>
            </div>
            <div class="pdf-evidence-gate-chip">
              <div class="pdf-evidence-gate-label">PDF 绑定</div>
              <div class="pdf-evidence-gate-value" data-pdf-evidence-gate-value="bound">--</div>
            </div>
            <div class="pdf-evidence-gate-chip">
              <div class="pdf-evidence-gate-label">待人工核查</div>
              <div class="pdf-evidence-gate-value" data-pdf-evidence-gate-value="unbound">--</div>
            </div>
            <div class="pdf-evidence-gate-chip">
              <div class="pdf-evidence-gate-label">证据锚点</div>
              <div class="pdf-evidence-gate-value" data-pdf-evidence-gate-value="anchors">--</div>
            </div>
            <div class="pdf-evidence-gate-meter" aria-hidden="true"><span data-pdf-evidence-gate-meter></span></div>
          </div>
        </div>
        <div class="pdf-evidence-gate-actions">
          <button class="inline-button primary" type="button" data-pdf-evidence-gate-chain>证据链矩阵</button>
          <button class="inline-button" type="button" data-pdf-evidence-gate-pdf>有 PDF 证据</button>
          <button class="inline-button" type="button" data-pdf-evidence-gate-unbound>待核查项</button>
        </div>
      </section>
      <section class="pdf-activity-feed" data-pdf-activity-feed aria-label="PDF 审稿近期事件">
        <div class="pdf-activity-head">
          <span>PDF 审稿近期事件</span>
          <span>最近</span>
        </div>
        <div class="pdf-activity-list" data-pdf-activity-list></div>
        <div class="pdf-activity-empty" data-pdf-activity-empty>等待审稿事件流。</div>
      </section>
      <div class="pdf-page-context" data-pdf-page-context>
        <div>
          <div class="pdf-page-context-title" data-pdf-page-context-title>正在统计本页审稿关注</div>
          <div class="pdf-page-context-copy" data-pdf-page-context-copy>页码导航会标记每页绑定的 concerns。</div>
        </div>
        <div class="pdf-page-context-actions">
          <button class="inline-button" type="button" data-pdf-page-filter-current>只看本页队列</button>
          <button class="inline-button" type="button" data-pdf-page-next-pending>下一未处理</button>
          <button class="inline-button" type="button" data-pdf-page-next-concern>下一关注页</button>
        </div>
      </div>
      <section class="pdf-page-annotations" data-pdf-page-annotations aria-label="PDF 本页审稿批注">
        <div class="pdf-annotation-head">
          <span>PDF 本页审稿批注</span>
          <span class="pdf-annotation-head-actions">
            <button class="inline-button" type="button" data-pdf-annotation-density-toggle aria-pressed="false">紧凑</button>
            <span class="pdf-annotation-count" data-pdf-annotation-count>0 条</span>
          </span>
        </div>
        <div class="pdf-annotation-progress" data-pdf-annotation-progress aria-label="PDF 本页批注处理进度">
          <div class="pdf-annotation-progress-chip">
            <div class="pdf-annotation-progress-label">待处理</div>
            <div class="pdf-annotation-progress-value" data-pdf-annotation-pending>0 条</div>
          </div>
          <div class="pdf-annotation-progress-chip">
            <div class="pdf-annotation-progress-label">已处理</div>
            <div class="pdf-annotation-progress-value" data-pdf-annotation-done>0 条</div>
          </div>
          <div class="pdf-annotation-progress-chip">
            <div class="pdf-annotation-progress-label">本页总计</div>
            <div class="pdf-annotation-progress-value" data-pdf-annotation-total>0 条</div>
          </div>
          <div class="pdf-annotation-meter" aria-hidden="true"><span data-pdf-annotation-meter></span></div>
        </div>
        <div class="pdf-annotation-list" data-pdf-annotation-list data-density="comfortable"></div>
        <div class="pdf-annotation-empty" data-pdf-annotation-empty>当前页暂无绑定审稿批注。</div>
      </section>
      <div class="pdf-selection-tray" data-pdf-selection-tray hidden>
        <div>
          <div class="pdf-selection-meta" data-pdf-selection-meta>尚未选择 PDF 文字</div>
          <div class="pdf-selection-quote" data-pdf-selection-quote></div>
        </div>
        <div class="pdf-selection-actions">
          <button class="inline-button" type="button" data-pdf-selection-copy>复制选区</button>
          <button class="inline-button primary" type="button" data-pdf-selection-use data-pdf-selection-review>基于选区审稿</button>
          <button class="inline-button" type="button" data-pdf-selection-clear>清空</button>
        </div>
      </div>
      <div class="pdf-reader-stage" data-pdf-stage>
        <div class="pdf-selection-popover" data-pdf-selection-popover data-visible="false" aria-label="PDF 选区审稿浮层">
          <div class="pdf-selection-popover-head">
            <span>选区证据</span>
            <span class="pdf-selection-popover-meta" data-pdf-selection-popover-meta>PDF 选区</span>
          </div>
          <div class="pdf-selection-popover-quote" data-pdf-selection-popover-quote></div>
          <div class="pdf-selection-popover-actions">
            <button class="inline-button primary" type="button" data-pdf-selection-use>基于选区审稿</button>
            <button class="inline-button" type="button" data-pdf-selection-copy>复制</button>
            <button class="inline-button" type="button" data-pdf-selection-clear>清空</button>
          </div>
        </div>
        <div class="pdf-loading" data-pdf-loading>正在渲染论文页面</div>
        <div class="pdf-page-wrap" data-pdf-page-wrap>
          <canvas class="pdf-page-canvas" data-pdf-canvas aria-label="论文 PDF 当前页"></canvas>
          <div class="pdf-text-layer" data-pdf-text-layer aria-label="可选择文字层"></div>
        </div>
        <div class="source-pdf-fallback" data-pdf-fallback hidden>
          <p>PDF.js 未能完成渲染，请<a href="/paper.pdf" target="_blank" rel="noreferrer">打开原始 PDF</a>。</p>
        </div>
      </div>
    </div>
"""


def _render_pdf_tool_trace_strip(events: list[Any]) -> str:
    valid_events = [event for event in events if isinstance(event, dict)]
    latest = list(reversed(valid_events))[:3]
    if not latest:
        cards = '<div class="pdf-tool-trace-empty" data-pdf-tool-trace-empty>等待工具调用事件。</div>'
    else:
        cards = "".join(_render_pdf_tool_trace_card(event) for event in latest)
    return f"""
      <section class="pdf-tool-trace-strip" data-pdf-tool-trace-strip aria-label="PDF 工具调用轨迹">
        <div class="pdf-tool-trace-head">
          <span>PDF 工具调用轨迹</span>
          <a class="inline-button" href="#tool-trace">查看全部</a>
        </div>
        <div class="pdf-tool-trace-list" data-pdf-tool-trace-list>{cards}</div>
      </section>
"""


def _render_pdf_tool_trace_card(event: dict[str, Any]) -> str:
    status = str(event.get("status") or "unknown").replace(" ", "_").lower()
    tool = str(event.get("tool") or event.get("call_id") or "tool")
    summary = str(event.get("output_summary") or event.get("input_summary") or event.get("error_code") or "等待输出摘要")
    agent = _display_name(str(event.get("agent_id") or "peerassist"))
    return f"""
          <article class="pdf-tool-trace-card" data-pdf-tool-trace-card data-status="{html.escape(status, quote=True)}">
            <div class="pdf-tool-trace-top">
              <span class="pdf-tool-trace-name">{html.escape(tool)}</span>
              <span class="pdf-tool-trace-status">{html.escape(_localized_status(status))}</span>
            </div>
            <div class="pdf-tool-trace-copy">{html.escape(agent)} · {html.escape(summary[:120])}</div>
          </article>
"""


def _render_extracted_text_preview(
    *, paper_id: str, paper_lines: str, locator: str, first_impact: str
) -> str:
    return f"""
    <article class="paper-sheet" aria-label="论文抽取文本预览">
      <h2 class="paper-title">PeerAssist Manuscript Preview · {html.escape(paper_id)}</h2>
      <div class="paper-authors">Anonymous submission · evidence-grounded review copy</div>
      <div class="paper-section-title">Evidence Preview</div>
      {paper_lines}
      <div class="paper-section-title">Reviewer Evidence Anchor</div>
      <p class="paper-line" data-line="4">
        Evidence locator: <span class="paper-highlight">{html.escape(locator)}</span>. The margin note records the concern,
        benign explanation, and suggested author action without adding unevidenced facts. {html.escape(first_impact[:120])}
      </p>
    </article>
"""


def _render_paper_preview_lines(rows: list[dict[str, Any]], *, fallback_title: str) -> str:
    if not rows:
        rows = [{"id": "P01-L001", "text": fallback_title, "locator": "p.1 line 1"}]
    rendered: list[str] = []
    for idx, row in enumerate(rows[:5], start=1):
        evidence_id = str(row.get("id") or f"P01-L{idx:03d}")
        text = str(row.get("text") or "").strip()
        if len(text) > 260:
            text = text[:257].rstrip() + "..."
        rendered.append(
            f"""
      <p class="paper-line" data-line="{idx}">
        <span class="paper-highlight" data-evidence-anchor="{html.escape(evidence_id, quote=True)}">
          {html.escape(text or fallback_title)}
        </span>
      </p>
"""
        )
    return "".join(rendered)


def _render_margin_comments(
    items: list[dict[str, Any]], *, evidence_preview: list[dict[str, Any]]
) -> str:
    rows: list[str] = []
    for position, item in enumerate(items[:4], start=1):
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        first_evidence = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
        evidence_id = str(first_evidence.get("id") or f"C{position:02d}")
        concern_id = str(item.get("id") or "")
        locator = str(first_evidence.get("locator") or "未标注位置")
        pdf_page_attr = _evidence_pdf_page_attr(first_evidence)
        title = _localized_copy(str(item.get("title") or "待核查关注点"))
        action = _localized_copy(str(item.get("author_action") or "请审稿人确认该关注点。"))
        rows.append(
            f"""
<div class="paper-comment" data-margin-comment="{html.escape(evidence_id, quote=True)}" data-paper-concern="{html.escape(concern_id, quote=True)}"{pdf_page_attr}>
  <div class="comment-anchor">批注 {position} · {html.escape(evidence_id)} · {html.escape(locator)}</div>
  <div class="comment-title">{html.escape(title)}</div>
  <div class="comment-copy">{html.escape(action)}</div>
  <div class="comment-actions">
    <button class="inline-button" type="button" data-jump-concern="{html.escape(concern_id, quote=True)}" data-paper-target="{html.escape(evidence_id, quote=True)}"{pdf_page_attr}>定位队列</button>
  </div>
</div>
"""
        )
    if not rows:
        preview_text = _render_margin_evidence_preview(evidence_preview)
        rows.append(
            f"""
<div class="paper-comment">
  <div class="comment-anchor">证据预览</div>
  <div class="comment-title">暂无待人工确认批注</div>
  <div class="comment-copy">当前真实论文已完成证据预览；如需生成可确认批注，需要更强的表格/参考文献结构解析或新的确定性 lead。</div>
  {preview_text}
</div>
"""
        )
    return "".join(rows)


def _render_margin_evidence_preview(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    snippets: list[str] = []
    for row in rows[:3]:
        evidence_id = str(row.get("id") or "")
        text = str(row.get("text") or "").strip()
        if len(text) > 90:
            text = text[:87].rstrip() + "..."
        snippets.append(
            f"""
  <div class="comment-copy">
    <strong>{html.escape(evidence_id)}</strong> · {html.escape(text)}
  </div>
"""
        )
    return "".join(snippets)


def _render_artifact_workspace(paths: dict[str, Any]) -> str:
    labels = {
        "source_pdf": "原始 PDF",
        "evidence_ledger": "证据账本",
        "queue": "审稿队列",
        "confirmations": "人工确认",
        "agent_results": "代理结果",
        "capability_invocations": "能力调用",
        "tool_trace": "工具追踪",
    }
    rows: list[str] = []
    for key, label in labels.items():
        value = str(paths.get(key) or "").strip()
        if not value:
            continue
        rows.append(
            f"""
<div class="artifact-row">
  <div>
    <div class="artifact-name">{html.escape(label)}</div>
    <div class="artifact-path">{html.escape(value)}</div>
  </div>
  <button class="inline-button" type="button" data-copy-path="{html.escape(value, quote=True)}">复制路径</button>
</div>
"""
        )
    if not rows:
        rows.append('<div class="trace-summary">暂无可展示产物路径。</div>')
    return f'<div class="artifact-list">{"".join(rows)}</div>'


def _render_next_actions(
    *, pending_count: int, failed_event_count: int, actions_count: int
) -> str:
    rows = [
        (
            "处理待确认项",
            f"当前还有 {pending_count} 条关注点等待确认、改写、降级、删除或暂挂。",
        ),
        (
            "复核工具异常",
            f"当前记录到 {failed_event_count} 个失败工具事件；若非 0，先查看右侧工具追踪。",
        ),
        (
            "导出确认报告",
            f"已记录 {actions_count} 个动作；完成确认后刷新报告产物并检查中英文输出。",
        ),
    ]
    rendered = "".join(
        f"""
<div class="next-action-row">
  <div class="next-action-title">{html.escape(title)}</div>
  <div class="next-action-copy">{html.escape(copy)}</div>
</div>
"""
        for title, copy in rows
    )
    return f'<div class="next-action-list">{rendered}</div>'


def _render_stream_console(*, events_count: int, actions_count: int) -> str:
    return f"""
<div class="stream-console" data-panel="stream-console" data-stream-log>
  <div><span class="stream-key">实时事件：</span> 等待 /api/events 首帧</div>
  <div><span class="stream-key">当前快照：</span> {events_count} 条追踪事件 · {actions_count} 个动作</div>
  <div><span class="stream-key">兜底通道：</span> /api/state 每 15 秒轮询</div>
</div>
"""


def _review_progress(*, pending_count: int, actions_count: int) -> int:
    total = max(0, pending_count) + max(0, actions_count)
    if total == 0:
        return 100
    return round(max(0, actions_count) / total * 100)


def _runtime_health_label(*, failed_event_count: int) -> str:
    if failed_event_count > 0:
        return f"{failed_event_count} 个调用需复核"
    return "调用链健康"


def _review_stage_lifecycle(
    *,
    queue_items: int,
    evidence_preview_count: int,
    agent_runs: list[Any],
    events: list[Any],
    invocations: list[Any],
    pending_count: int,
    actions_count: int,
    failed_event_count: int,
) -> list[dict[str, str]]:
    event_statuses = {
        str(row.get("status") or "").replace(" ", "_").lower()
        for row in events
        if isinstance(row, dict)
    }
    has_trace_activity = any(status for status in event_statuses)
    has_running_trace = bool(
        event_statuses & {"queued", "started", "running", "progress", "approval_required"}
    )
    has_completed_trace = "completed" in event_statuses
    has_agents = any(isinstance(row, dict) for row in agent_runs)
    has_invocations = any(isinstance(row, dict) for row in invocations)
    has_evidence = evidence_preview_count > 0 or queue_items > 0
    has_queue = queue_items > 0

    def trace_status() -> str:
        if failed_event_count > 0:
            return "blocked"
        if has_completed_trace:
            return "completed"
        if has_running_trace:
            return "active"
        return "waiting"

    human_status = "waiting"
    if pending_count > 0:
        human_status = "active"
    elif actions_count > 0:
        human_status = "completed"
    elif has_queue:
        human_status = "active"

    report_status = "completed" if actions_count > 0 else "waiting"

    return [
        {
            "index": "01",
            "title": "排队",
            "status": "completed" if has_queue or has_evidence else "active",
            "copy": "恢复当前论文审稿任务与队列快照",
        },
        {
            "index": "02",
            "title": "解析论文",
            "status": "completed" if has_evidence else "waiting",
            "copy": f"已恢复 {evidence_preview_count} 个原文预览锚点",
        },
        {
            "index": "03",
            "title": "建立证据台账",
            "status": "completed" if has_evidence else "waiting",
            "copy": f"{queue_items} 条队列项已绑定证据",
        },
        {
            "index": "04",
            "title": "确定性核查",
            "status": trace_status(),
            "copy": "统计、图表、引用等规则检查进入 trace",
        },
        {
            "index": "05",
            "title": "多代理评审",
            "status": "completed" if has_agents else ("active" if has_trace_activity else "waiting"),
            "copy": f"{len(agent_runs)} 个代理结果可复盘",
        },
        {
            "index": "06",
            "title": "MCP / Skills 调用",
            "status": "blocked" if failed_event_count > 0 else ("completed" if has_invocations or has_completed_trace else "waiting"),
            "copy": f"{len(invocations)} 条能力调用记录",
        },
        {
            "index": "07",
            "title": "等待人工批准",
            "status": human_status,
            "copy": f"{pending_count} 条待确认，{actions_count} 条已记录动作",
        },
        {
            "index": "08",
            "title": "报告导出",
            "status": report_status,
            "copy": "确认后生成中英文 JSON / Markdown 报告",
        },
    ]


def _render_agent_stage_board(stages: list[dict[str, str]]) -> str:
    cards = []
    for stage in stages:
        status = str(stage.get("status") or "waiting").replace(" ", "_").lower()
        cards.append(
            f"""
<div class="agent-stage-card" data-agent-stage="{html.escape(str(stage.get('index') or ''), quote=True)}" data-stage-status="{html.escape(status, quote=True)}">
  <div class="agent-stage-top">
    <span class="agent-stage-index">{html.escape(str(stage.get('index') or ''))}</span>
    <span class="agent-stage-status">{html.escape(_localized_status(status))}</span>
  </div>
  <div class="agent-stage-title">{html.escape(str(stage.get('title') or ''))}</div>
  <div class="agent-stage-copy">{html.escape(str(stage.get('copy') or ''))}</div>
</div>
"""
        )
    return f'<div class="agent-stage-board" data-agent-stage-board aria-label="PeerAssist 智能体审稿生命周期">{"".join(cards)}</div>'


def _render_agent_timeline(agent_runs: list[Any]) -> str:
    rows = []
    for row in agent_runs:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "unknown")
        detail = (
            f"{int(row.get('draft_count') or 0)} 条草稿"
            f" · {int(row.get('warning_count') or 0)} 条警告"
        )
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        metadata_text = _compact_metadata(metadata)
        rows.append(
            f"""
<div class="agent-row">
  <div class="agent-dot"></div>
  <div>
    <div class="agent-name">{html.escape(_display_name(str(row.get('agent_id') or 'agent')))} {_status_pill(status)}</div>
    <div class="agent-detail">{html.escape(detail)}</div>
    <div class="agent-meta">{html.escape(metadata_text)}</div>
  </div>
</div>
"""
        )
    if not rows:
        rows.append('<div class="agent-detail">暂无代理运行结果。</div>')
    return f'<div class="agent-timeline">{"".join(rows)}</div>'


def _render_trace_events(events: list[Any]) -> str:
    rows = []
    visible_events = [row for row in events[-8:] if isinstance(row, dict)]
    for index, row in enumerate(visible_events):
        if not isinstance(row, dict):
            continue
        input_summary = _localized_copy(str(row.get("input_summary") or ""))
        output_summary = _localized_copy(str(row.get("output_summary") or ""))
        summary = output_summary or input_summary or "暂无摘要"
        duration = row.get("duration_ms")
        duration_text = f" · {int(duration)} ms" if isinstance(duration, int) else ""
        status = str(row.get("status") or "unknown")
        open_attr = " open" if index == len(visible_events) - 1 else ""
        artifacts = [str(item) for item in row.get("artifact_ids", []) if item]
        evidence_ids = [str(item) for item in row.get("evidence_ids", []) if item]
        error_text = " · ".join(
            str(item)
            for item in (row.get("error_code"), row.get("error_message"))
            if item
        )
        rows.append(
            f"""
<details class="trace-event" data-trace-audit-card data-status="{html.escape(status.replace(' ', '_').lower(), quote=True)}"{open_attr}>
  <summary>
    <span>
      <span class="trace-head"><span class="trace-tool">{html.escape(str(row.get('tool') or row.get('call_id') or 'tool'))}</span>{_status_pill(status)}</span>
      <span class="trace-summary">{html.escape(_display_name(str(row.get('agent_id') or 'peerassist')))}{html.escape(duration_text)}</span>
      <span class="trace-summary">{html.escape(summary)}</span>
    </span>
  </summary>
  <div class="trace-audit-body">
    <div class="trace-audit-grid">
      {_render_audit_field('调用 ID', str(row.get('call_id') or '未记录'))}
      {_render_audit_field('任务 ID', str(row.get('task_id') or '未记录'))}
      {_render_audit_field('来源', _localized_source(str(row.get('source') or 'internal')))}
      {_render_audit_field('开始时间', str(row.get('ts') or '未记录'))}
      {_render_audit_field('输入摘要', input_summary or '未记录')}
      {_render_audit_field('输出摘要', output_summary or '未记录')}
    </div>
    {_render_audit_chips('证据', evidence_ids)}
    {_render_audit_chips('产物', artifacts)}
    {_render_audit_field('错误信息', error_text or '无')}
  </div>
</details>
"""
        )
    if not rows:
        rows.append('<div class="trace-summary">暂无工具追踪事件。</div>')
    return f'<div class="trace-list">{"".join(rows)}</div>'


def _render_trace_filters(events: list[Any]) -> str:
    counts = {
        "all": sum(1 for row in events if isinstance(row, dict)),
        "queued": _event_status_count(events, "queued"),
        "completed": _event_status_count(events, "completed"),
        "failed": _event_status_count(events, "failed"),
    }
    labels = {
        "all": "全部",
        "queued": "排队",
        "completed": "完成",
        "failed": "失败",
    }
    buttons = "".join(
        (
            f'<button class="trace-filter" type="button" data-trace-filter="{html.escape(key, quote=True)}" '
            f'aria-pressed="{str(key == "all").lower()}">{html.escape(label)} {counts[key]}</button>'
        )
        for key, label in labels.items()
    )
    return f'<div class="trace-toolbar" data-trace-toolbar>{buttons}</div>'


def _render_invocations(invocations: list[Any]) -> str:
    rows = []
    visible_invocations = [row for row in invocations[-4:] if isinstance(row, dict)]
    for index, row in enumerate(visible_invocations):
        if not isinstance(row, dict):
            continue
        artifacts = [str(item) for item in row.get("artifact_ids", []) if item]
        evidence_ids = [str(item) for item in row.get("evidence_ids", []) if item]
        status = str(row.get("status") or "unknown")
        duration = row.get("duration_ms")
        duration_text = f" · {int(duration)} ms" if isinstance(duration, int) else ""
        open_attr = " open" if index == len(visible_invocations) - 1 else ""
        error_text = " · ".join(
            str(item)
            for item in (row.get("error_code"), row.get("error_message"))
            if item
        )
        rows.append(
            f"""
<details class="trace-event" data-capability-audit-card data-status="{html.escape(status.replace(' ', '_').lower(), quote=True)}"{open_attr}>
  <summary>
    <span>
      <span class="trace-head"><span class="trace-tool">{html.escape(str(row.get('capability_name') or row.get('call_id') or 'capability'))}</span>{_status_pill(status)}</span>
      <span class="trace-summary">来源={html.escape(_localized_source(str(row.get('source') or '')))} · 尝试={html.escape(str(row.get('attempts') or 0))}{html.escape(duration_text)}</span>
      <span class="trace-summary">能力调用可追溯记录</span>
    </span>
  </summary>
  <div class="trace-audit-body">
    <div class="trace-audit-grid">
      {_render_audit_field('调用 ID', str(row.get('call_id') or '未记录'))}
      {_render_audit_field('任务 ID', str(row.get('task_id') or '未记录'))}
      {_render_audit_field('代理', _display_name(str(row.get('agent_id') or 'peerassist')))}
      {_render_audit_field('来源', _localized_source(str(row.get('source') or 'internal')))}
      {_render_audit_field('尝试次数', str(row.get('attempts') or 0))}
      {_render_audit_field('状态', _localized_status(status))}
    </div>
    {_render_audit_chips('证据', evidence_ids)}
    {_render_audit_chips('产物', artifacts)}
    {_render_audit_field('错误信息', error_text or '无')}
  </div>
</details>
"""
        )
    if not rows:
        rows.append('<div class="trace-summary">暂无能力调用记录。</div>')
    return f'<div class="invocation-list">{"".join(rows)}</div>'


def _render_audit_field(label: str, value: str) -> str:
    return f"""
<div class="trace-audit-field">
  <div class="trace-audit-label">{html.escape(label)}</div>
  <div class="trace-audit-value">{html.escape(value)}</div>
</div>
"""


def _render_audit_chips(label: str, values: list[str]) -> str:
    chips = "".join(f'<span class="trace-chip">{html.escape(value)}</span>' for value in values)
    if not chips:
        chips = '<span class="trace-chip">无</span>'
    return f"""
<div class="trace-audit-field">
  <div class="trace-audit-label">{html.escape(label)}</div>
  <div class="trace-chip-row">{chips}</div>
</div>
"""


def _status_pill(status: str) -> str:
    normalized = status.replace(" ", "_").lower()
    return (
        f'<span class="status-pill status-{html.escape(normalized, quote=True)}">'
        f"{html.escape(_localized_status(status))}"
        "</span>"
    )


def _display_name(value: str) -> str:
    localized = {
        "statistics_agent": "统计核查代理",
        "figure_table_agent": "图表核查代理",
        "defense_agent": "反证审查代理",
        "integrator_agent": "综合整理代理",
        "citation_agent": "引用核查代理",
        "peerassist_stage": "PeerAssist 阶段",
        "peerassist": "PeerAssist",
    }.get(value)
    if localized:
        return localized
    return f"代理：{value}" if value else "代理"


def _compact_metadata(metadata: dict[str, Any]) -> str:
    if not metadata:
        return "暂无元数据"
    parts: list[str] = []
    for key, value in metadata.items():
        if isinstance(value, list):
            shown = ", ".join(str(item) for item in value[:2])
            if len(value) > 2:
                shown += f", +{len(value) - 2}"
        else:
            shown = str(value)
        parts.append(f"{_localized_metadata_key(str(key))}={shown}")
        if len(parts) >= 2:
            break
    return " · ".join(parts)


def _localized_mode(mode: str) -> str:
    return {
        "fast": "快速",
        "standard": "标准",
        "deep": "深度",
        "unknown": "未知",
    }.get(str(mode or "").lower(), str(mode or "未知"))


def _localized_action(action: str) -> str:
    return {
        "confirm": "确认",
        "rewrite": "改写",
        "downgrade": "降级",
        "delete": "删除",
        "mark_pending": "暂挂",
    }.get(action, action.replace("_", " "))


def _localized_status(status: str) -> str:
    return {
        "pending_human_confirmation": "待人工确认",
        "confirmed": "已确认",
        "rewritten": "已改写",
        "downgraded": "已降级",
        "deleted": "已删除",
        "mark_pending": "暂挂",
        "pending": "待处理",
        "queued": "已排队",
        "started": "运行中",
        "active": "进行中",
        "waiting": "等待",
        "completed": "已完成",
        "blocked": "需复核",
        "failed": "失败",
        "approval_required": "需审批",
        "unknown": "未知",
    }.get(str(status or "").lower(), str(status or "未知"))


def _localized_level(level: str) -> str:
    return {
        "clarification_needed": "需要澄清",
        "major": "主要问题",
        "minor": "次要问题",
        "critical": "严重问题",
        "info": "信息提示",
    }.get(str(level or "").lower(), str(level or "未分级"))


def _localized_category(category: str) -> str:
    return {
        "statistics": "统计",
        "figure_table": "图表",
        "citation": "引用",
        "methodology": "方法",
        "reproducibility": "可复现",
        "manual": "人工",
    }.get(str(category or "").lower(), str(category or "未分类"))


def _localized_source(source: str) -> str:
    return {
        "builtin": "内置",
        "mcp": "MCP",
        "skill": "技能",
    }.get(str(source or "").lower(), str(source or "未知"))


def _localized_metadata_key(key: str) -> str:
    return {
        "lead_count": "线索数",
        "pressure_tests": "反证测试",
        "integrated_from": "整合来源",
    }.get(key, key)


def _localized_copy(text: str) -> str:
    replacements = {
        "Reported percentage needs clarification": "报告百分比需要澄清",
        "Significance stars need clarification": "显著性星号需要澄清",
        "Figure Table Reference": "图表引用需要核查",
        "This may affect whether the reported result supports the paper's claim.": "这可能影响论文结果是否支持其主张。",
        "rounding; different denominator; filtered sample": "四舍五入；分母不同；样本经过筛选",
        "table transcription issue; star legend differs for this table; p-value was rounded from a more precise value": "表格转录问题；该表使用不同星号图例；p 值由更精确数值四舍五入而来",
        "parser missed a caption; supplementary material reference; label formatting changed": "解析器可能漏掉标题；引用的是补充材料；标签格式发生变化",
        "Please clarify the calculation basis and provide enough detail for readers to reproduce it.": "请澄清计算依据，并提供足够细节以便读者复核。",
        "run deterministic PeerAssist consistency checks": "运行 PeerAssist 确定性一致性核查",
        "deterministic check leads": "确定性核查线索",
        "run local PeerAssist agents in fast mode": "以快速模式运行本地 PeerAssist 代理",
        "resolve FactReview parse artifacts": "解析 FactReview 产物",
        "provider=mineru; warnings=0": "provider=mineru；警告=0",
        "mode=fast": "模式=快速",
        "6 evidence items": "6 条证据",
        "agent_results.json": "agent_results.json",
    }
    return replacements.get(text, text)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
