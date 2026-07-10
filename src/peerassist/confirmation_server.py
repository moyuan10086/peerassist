"""Local PeerAssist human confirmation review server."""

from __future__ import annotations

import argparse
import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from common.pipeline_context import peerassist_stage_dir
from peerassist.confirmation_workflow import apply_confirmation_decision, load_confirmation_state


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
      display: none;
      grid-template-columns: 1.2fr repeat(4, minmax(120px, 1fr));
      gap: 10px;
      align-items: stretch;
    }}
    .workflow-band {{
      width: min(1560px, calc(100% - 40px));
      margin: 12px auto 0;
      display: none;
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
      grid-template-columns: minmax(0, 1fr) 360px;
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
    .paper-canvas {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 260px;
      gap: 14px;
      padding: 16px;
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
    .pdf-reader-stage {{
      position: relative;
      min-height: 780px;
      height: calc(100vh - 235px);
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
    }}
    .paper-comment {{
      border: 1px solid #c8e1d9;
      border-left: 4px solid var(--accent);
      border-radius: 8px;
      background: #f4fbf8;
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
    .item:last-child {{ border-bottom: 0; }}
    .item h2 {{ margin: 0 0 10px; font-size: 18px; line-height: 1.25; letter-spacing: 0; }}
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
    .trace-event {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--panel-soft);
    }}
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
    .trace-summary {{ margin-top: 6px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }}
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
      .console-shell {{ grid-template-columns: 1fr; }}
      .paper-canvas {{ grid-template-columns: 1fr; }}
      .paper-comments {{ grid-template-columns: 1fr 1fr; }}
      .source-pdf-shell {{ min-height: 620px; }}
      .pdf-reader-stage {{ min-height: 580px; height: 70vh; }}
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
      .pdf-reader-stage {{ min-height: 480px; height: 68vh; padding: 14px; }}
      .paper-comments {{ grid-template-columns: 1fr; }}
      .item {{ grid-template-columns: 1fr; }}
      .queue-toolbar {{ grid-template-columns: 1fr; }}
      .right-stack {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body data-peerassist-agent-console>
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
      {_render_paper_review_surface(items, paper_id=paper_id, evidence_preview=evidence_preview, has_source_pdf=source_pdf_path is not None)}
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
        <div class="queue-body">{rows}</div>
      </section>
    </section>
    <aside class="right-stack">
      {_render_model_entry(model_config)}
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
      const item = button.closest('[data-concern-id]');
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
    function appendStreamLine(kind, copy) {{
      const consoleEl = document.querySelector('[data-stream-log]');
      if (!consoleEl) return;
      const row = document.createElement('div');
      row.innerHTML = `<span class="stream-key">${{escapeHtml(kind)}}：</span>${{escapeHtml(copy)}}`;
      consoleEl.prepend(row);
      while (consoleEl.children.length > 6) {{
        consoleEl.removeChild(consoleEl.lastElementChild);
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
    function clearAnnotationActiveState() {{
      document.querySelectorAll('[data-active="true"]').forEach((node) => {{
        delete node.dataset.active;
      }});
    }}
    function focusAnnotation(concernId, evidenceId, target) {{
      clearAnnotationActiveState();
      const item = concernId ? document.querySelector(`[data-concern-id="${{CSS.escape(concernId)}}"]`) : null;
      const highlight = evidenceId ? document.querySelector(`[data-evidence-anchor="${{CSS.escape(evidenceId)}}"]`) : null;
      const comment = concernId ? document.querySelector(`[data-paper-concern="${{CSS.escape(concernId)}}"].paper-comment`) : null;
      if (item) item.dataset.active = 'true';
      if (highlight) highlight.dataset.active = 'true';
      if (comment) comment.dataset.active = 'true';
      const scrollTarget = target === 'queue' ? item : highlight || comment;
      if (scrollTarget) {{
        scrollTarget.scrollIntoView({{behavior: 'smooth', block: 'center'}});
      }}
      showToast(target === 'queue' ? '已定位到审稿队列' : '已定位到论文高亮');
    }}
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
      if (source === 'stream') {{
        appendStreamLine('state', `${{runtime.tool_event_count || 0}} 条追踪事件 · ${{state.pending_count || 0}} 条待确认`);
      }}
    }}
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
    document.querySelectorAll('button[data-action]').forEach((button) => {{
      button.addEventListener('click', () => submitDecision(button).catch((error) => alert(error.message)));
    }});
    document.querySelectorAll('[data-copy-path]').forEach((button) => {{
      button.addEventListener('click', () => {{
        copyText(button.dataset.copyPath || '')
          .then(() => showToast('产物路径已复制'))
          .catch((error) => showToast(`复制失败：${{error.message}}`));
      }});
    }});
    document.querySelectorAll('[data-trace-filter]').forEach((button) => {{
      button.addEventListener('click', () => applyTraceFilter(button.dataset.traceFilter || 'all'));
    }});
    document.querySelectorAll('[data-jump-concern]').forEach((button) => {{
      button.addEventListener('click', () => {{
        focusAnnotation(button.dataset.jumpConcern || '', button.dataset.paperTarget || '', 'queue');
      }});
    }});
    document.querySelectorAll('.paper-comment').forEach((comment) => {{
      comment.addEventListener('click', (event) => {{
        if (event.target.closest('button')) return;
        focusAnnotation(comment.dataset.paperConcern || '', comment.dataset.marginComment || '', 'queue');
      }});
    }});
    document.querySelectorAll('[data-paper-target]:not([data-jump-concern])').forEach((button) => {{
      button.addEventListener('click', () => {{
        focusAnnotation(button.dataset.paperConcern || '', button.dataset.paperTarget || '', 'paper');
      }});
    }});
    document.querySelectorAll('[data-agent-review-start]').forEach((button) => {{
      button.addEventListener('click', async () => {{
        const stream = document.querySelector('[data-agent-review-stream]');
        const selectedText = String(window.getSelection()?.toString() || '').trim();
        if (!stream) return;
        button.disabled = true;
        stream.textContent = '正在按方法论启动全篇智能审稿：读取证据台账、确定性核查、本地代理结果与现有确认队列...';
        try {{
          const response = await fetch('/api/agent-review', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{selected_text: selectedText}})
          }});
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || '智能审稿失败');
          stream.textContent = `${{payload.suggestion}}\\n\\n草稿路径：${{payload.draft_path}}`;
          showToast('全篇智能审稿草稿已生成');
        }} catch (error) {{
          stream.textContent = `智能审稿未完成：${{error.message}}`;
          showToast('智能审稿未完成');
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});
    applyTraceFilter('all');
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
      const context = canvas.getContext('2d');
      let pdfDoc = null;
      let pageNumber = 1;
      let scale = 1.15;
      let renderTask = null;
      let textLayerTask = null;
      let fitWidth = true;

      function setStatus(copy) {{
        if (status) status.textContent = copy;
      }}

      function setLoading(visible) {{
        if (loading) loading.hidden = !visible;
      }}

      function updateButtons() {{
        reader.querySelectorAll('[data-pdf-action]').forEach((button) => {{
          const action = button.dataset.pdfAction;
          button.disabled =
            (action === 'prev' && pageNumber <= 1) ||
            (action === 'next' && pdfDoc && pageNumber >= pdfDoc.numPages);
        }});
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

      pdfjsLib.getDocument('/paper.pdf').promise
        .then((document) => {{
          pdfDoc = document;
          updateButtons();
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
    *, run_dir: Path, selected_text: str, paper_id: str, state: dict[str, Any]
) -> dict[str, Any]:
    selected_text = selected_text.strip()
    config = _resolve_model_config()
    api_key = _resolve_model_api_key()
    if not api_key:
        raise ValueError("模型 API Key 未配置。请在服务环境变量中设置 PEERASSIST_OPENAI_API_KEY。")
    context = _build_full_paper_review_context(
        run_dir=run_dir,
        paper_id=paper_id,
        selected_text=selected_text,
        state=state,
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
    draft_path = peerassist_stage_dir(run_dir) / "agent_review_draft.md"
    draft_path.write_text(response + "\n", encoding="utf-8")
    return {
        "schema_version": "peerassist.agent_review_result.v1",
        "model": config["model"],
        "base_url": config["base_url"],
        "selected_text_chars": len(selected_text),
        "context": context["context_summary"],
        "draft_path": str(draft_path),
        "suggestion": response,
    }


def _build_full_paper_review_context(
    *, run_dir: Path, paper_id: str, selected_text: str, state: dict[str, Any]
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
        "task": (
            "请基于整篇论文证据台账和已有确定性/本地代理结果，生成中文审稿辅助草稿。"
            "输出结构必须包含：一、论文概要；二、重点阅读路线；三、主要意见；四、次要意见；"
            "五、需要编辑关注的问题；六、系统局限。主要意见每条包含位置、证据、影响、可能的善意解释、建议作者如何修改。"
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
        with urlopen(request, timeout=60) as response:
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
    evidence_rows = "".join(
        (
            '<div class="evidence-row">'
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
        f'data-paper-concern="{html.escape(concern_id, quote=True)}">查看原文高亮</button>'
        + buttons
    )
    source_agents = item.get("source_agent_ids") if isinstance(item.get("source_agent_ids"), list) else []
    source_agent_tags = "".join(
        f'<span class="tag">{html.escape(_display_name(str(agent_id)))}</span>'
        for agent_id in source_agents
    )
    previous_text = html.escape(str(item.get("author_action") or ""), quote=True)
    return f"""
<section class="item" id="concern-{html.escape(concern_id, quote=True)}" data-concern-id="{html.escape(concern_id, quote=True)}" data-previous-text="{previous_text}">
  <div>
    <div class="tags">
      <span class="tag">{html.escape(_localized_status(str(item.get('status', ''))))}</span>
      <span class="tag level">{html.escape(_localized_level(str(item.get('level', ''))))}</span>
      <span class="tag">{html.escape(_localized_category(str(item.get('category', ''))))}</span>
      {source_agent_tags}
    </div>
    <h2>{html.escape(_localized_copy(str(item.get('title', '未命名关注点'))))}</h2>
    <p class="copy-block"><span class="label">影响</span><br>{html.escape(_localized_copy(str(item.get('impact', ''))))}</p>
    <p class="copy-block"><span class="label">可能的良性解释</span><br>{html.escape(_localized_copy(str(item.get('benign_explanation', ''))))}</p>
    <div class="evidence"><span class="label">证据</span>{evidence_rows}</div>
    <textarea aria-label="建议作者处理方式">{html.escape(_localized_copy(str(item.get('author_action', ''))))}</textarea>
  </div>
  <div class="actions">{buttons}</div>
</section>
"""


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
        <div class="agent-review-box">
          <button class="agent-primary-button" type="button" data-agent-review-start>开始全篇智能审稿</button>
          <div class="agent-stream-box" data-agent-review-stream>系统将读取整篇论文证据台账、确定性核查和代理结果，生成可追溯审稿草稿；选中的 PDF 文字只作为额外关注点。</div>
        </div>
      </section>
"""


def _render_paper_review_surface(
    items: list[Any], *, paper_id: str, evidence_preview: list[Any], has_source_pdf: bool
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
        _render_source_pdf_viewer()
        if has_source_pdf
        else _render_extracted_text_preview(
            paper_id=paper_id,
            paper_lines=paper_lines,
            locator=locator,
            first_impact=first_impact,
        )
    )
    comments = _render_margin_comments(concerns, evidence_preview=preview_rows)
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
  </div>
  <div class="paper-canvas">
    {source_document}
    <aside class="paper-comments" aria-label="页边批注">
      {comments}
    </aside>
  </div>
</section>
"""


def _render_source_pdf_viewer() -> str:
    return """
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
        </div>
      </div>
      <div class="pdf-reader-stage" data-pdf-stage>
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
        title = _localized_copy(str(item.get("title") or "待核查关注点"))
        action = _localized_copy(str(item.get("author_action") or "请审稿人确认该关注点。"))
        rows.append(
            f"""
<div class="paper-comment" data-margin-comment="{html.escape(evidence_id, quote=True)}" data-paper-concern="{html.escape(concern_id, quote=True)}">
  <div class="comment-anchor">批注 {position} · {html.escape(evidence_id)} · {html.escape(locator)}</div>
  <div class="comment-title">{html.escape(title)}</div>
  <div class="comment-copy">{html.escape(action)}</div>
  <div class="comment-actions">
    <button class="inline-button" type="button" data-jump-concern="{html.escape(concern_id, quote=True)}" data-paper-target="{html.escape(evidence_id, quote=True)}">定位队列</button>
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
    for row in events[-8:]:
        if not isinstance(row, dict):
            continue
        summary = _localized_copy(str(row.get("output_summary") or row.get("input_summary") or ""))
        duration = row.get("duration_ms")
        duration_text = f" · {int(duration)} ms" if isinstance(duration, int) else ""
        status = str(row.get("status") or "unknown")
        rows.append(
            f"""
<div class="trace-event" data-status="{html.escape(status.replace(' ', '_').lower(), quote=True)}">
  <div class="trace-head">
    <div class="trace-tool">{html.escape(str(row.get('tool') or row.get('call_id') or 'tool'))}</div>
    {_status_pill(status)}
  </div>
  <div class="trace-summary">{html.escape(_display_name(str(row.get('agent_id') or 'peerassist')))}{html.escape(duration_text)}</div>
  <div class="trace-summary">{html.escape(summary)}</div>
</div>
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
    for row in invocations[-4:]:
        if not isinstance(row, dict):
            continue
        artifacts = ", ".join(str(item) for item in row.get("artifact_ids", []) if item)
        status = str(row.get("status") or "unknown")
        rows.append(
            f"""
<div class="trace-event" data-status="{html.escape(status.replace(' ', '_').lower(), quote=True)}">
  <div class="trace-head">
    <div class="trace-tool">{html.escape(str(row.get('capability_name') or row.get('call_id') or 'capability'))}</div>
    {_status_pill(status)}
  </div>
  <div class="trace-summary">来源={html.escape(_localized_source(str(row.get('source') or '')))} · 尝试={html.escape(str(row.get('attempts') or 0))}</div>
  <div class="trace-summary">产物：{html.escape(artifacts or '无')}</div>
</div>
"""
        )
    if not rows:
        rows.append('<div class="trace-summary">暂无能力调用记录。</div>')
    return f'<div class="invocation-list">{"".join(rows)}</div>'


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
        "completed": "已完成",
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
