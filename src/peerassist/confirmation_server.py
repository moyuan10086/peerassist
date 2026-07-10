"""Local PeerAssist human confirmation review server."""

from __future__ import annotations

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from peerassist.confirmation_workflow import apply_confirmation_decision, load_confirmation_state


def render_confirmation_page(*, run_dir: Path, paper_id: str) -> str:
    state = load_confirmation_state(run_dir=run_dir)
    items = state.get("queue", {}).get("items", [])
    rows = "\n".join(_render_item(item) for item in items if isinstance(item, dict))
    if not rows:
        rows = '<section class="empty">暂无需要人工确认的审稿关注点。</section>'
    state_json = html.escape(json.dumps(state, ensure_ascii=False), quote=False)
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
      width: min(1440px, calc(100% - 40px));
      margin: 18px auto 0;
      display: grid;
      grid-template-columns: 1.2fr repeat(4, minmax(120px, 1fr));
      gap: 10px;
      align-items: stretch;
    }}
    .workflow-band {{
      width: min(1440px, calc(100% - 40px));
      margin: 12px auto 0;
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
      width: min(1440px, 100%);
      margin: 0 auto;
      padding: 20px;
      display: grid;
      grid-template-columns: 260px minmax(0, 1fr) 340px;
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
      display: grid;
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
      grid-template-columns: minmax(0, 1fr) 210px;
      gap: 14px;
      padding: 16px;
      background: #eef3f2;
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
      .right-stack {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 760px) {{
      .topbar {{ grid-template-columns: 1fr; }}
      .ops-strip {{ width: calc(100% - 24px); grid-template-columns: 1fr; }}
      .workflow-band {{ width: calc(100% - 24px); }}
      .console-shell {{ padding: 12px; }}
      .paper-canvas {{ padding: 12px; }}
      .paper-sheet {{ min-height: 420px; padding: 28px 28px 28px 38px; }}
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
      {_render_paper_review_surface(items, paper_id=paper_id)}
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
    applyTraceFilter('all');
    connectEventStream();
    window.setInterval(() => refreshRuntime().catch(() => {{}}), 15000);
  </script>
</body>
</html>
"""


def create_confirmation_server(
    *, run_dir: Path, paper_id: str, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    handler = _handler_factory(run_dir=Path(run_dir), paper_id=paper_id)
    return ThreadingHTTPServer((host, port), handler)


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
            if self.path == "/" or self.path.startswith("/?"):
                self._send_html(render_confirmation_page(run_dir=run_dir, paper_id=paper_id))
                return
            if self.path == "/api/state":
                self._send_json(load_confirmation_state(run_dir=run_dir))
                return
            if self.path == "/api/events":
                self._send_sse_state(load_confirmation_state(run_dir=run_dir))
                return
            self.send_error(404, "not found")

        def do_POST(self) -> None:
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

        def _send_html(self, text: str, *, status: int = 200) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
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


def _render_paper_review_surface(items: list[Any], *, paper_id: str) -> str:
    concerns = [item for item in items if isinstance(item, dict)]
    first_concern = concerns[0] if concerns else {}
    first_concern_id = str(first_concern.get("id") or "")
    first_title = _localized_copy(str(first_concern.get("title") or "待核查关注点"))
    first_impact = _localized_copy(str(first_concern.get("impact") or "当前关注点需要审稿人结合证据确认。"))
    evidence = first_concern.get("evidence") if isinstance(first_concern.get("evidence"), list) else []
    first_evidence = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
    evidence_id = str(first_evidence.get("id") or "P01-L001")
    locator = str(first_evidence.get("locator") or "p.1 line 1")
    comments = _render_margin_comments(concerns)
    return f"""
<section class="panel paper-viewer" data-panel="paper-viewer" id="paper-viewer">
  <div class="panel-header">
    <p class="panel-title">论文原文预览</p>
    <div class="panel-subtitle">PDF/Word 批注式阅读面：证据高亮、页边批注与人工确认队列联动</div>
  </div>
  <div class="paper-toolbar">
    <span class="paper-chip">页面 1</span>
    <span class="paper-chip">批注 {len(concerns)}</span>
    <span class="paper-chip">证据锚点 {html.escape(evidence_id)}</span>
    <a class="paper-chip" href="#review-queue">跳转队列</a>
  </div>
  <div class="paper-canvas">
    <article class="paper-sheet" aria-label="论文页面预览">
      <h2 class="paper-title">PeerAssist Manuscript Preview · {html.escape(paper_id)}</h2>
      <div class="paper-authors">Anonymous submission · evidence-grounded review copy</div>
      <div class="paper-section-title">Results</div>
      <p class="paper-line" data-line="1">
        We report the primary outcome and associated percentage summary for the evaluated cohort.
        <span class="paper-highlight" data-evidence-anchor="{html.escape(evidence_id, quote=True)}" data-paper-concern="{html.escape(first_concern_id, quote=True)}">
          {html.escape(first_title)}
        </span>
      </p>
      <p class="paper-line" data-line="2">
        The reported result should remain tied to a reproducible denominator, filtering rule, and table transcription path.
      </p>
      <p class="paper-line" data-line="3">
        PeerAssist marks this passage for reviewer confirmation because {html.escape(first_impact[:160])}
      </p>
      <div class="paper-section-title">Reviewer Evidence Anchor</div>
      <p class="paper-line" data-line="4">
        Evidence locator: <span class="paper-highlight">{html.escape(locator)}</span>. The margin note records the concern,
        benign explanation, and suggested author action without adding unevidenced facts.
      </p>
    </article>
    <aside class="paper-comments" aria-label="页边批注">
      {comments}
    </aside>
  </div>
</section>
"""


def _render_margin_comments(items: list[dict[str, Any]]) -> str:
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
        rows.append('<div class="comment-copy">暂无可映射到论文页面的批注。</div>')
    return "".join(rows)


def _render_artifact_workspace(paths: dict[str, Any]) -> str:
    labels = {
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
