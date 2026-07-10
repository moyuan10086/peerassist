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
        rows = '<section class="empty">No confirmation items are available.</section>'
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
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PeerAssist Review Console</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #18201f;
      --muted: #6b7472;
      --line: #d9dfdc;
      --paper: #f6f6f1;
      --panel: #ffffff;
      --panel-soft: #f9faf6;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --amber: #9a6500;
      --danger: #a33a3a;
      --violet: #5f4b8b;
      --shadow: 0 18px 46px rgba(24, 32, 31, 0.10);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.5 "Aptos", "Segoe UI", ui-sans-serif, sans-serif;
      color: var(--ink);
      background: var(--paper);
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
      border-bottom: 1px solid var(--line);
      background: rgba(246, 246, 241, 0.96);
      backdrop-filter: blur(16px);
    }}
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
      color: var(--accent-strong);
      background: #eef8f4;
      font-weight: 800;
    }}
    h1 {{ margin: 0; font-size: 19px; line-height: 1.15; font-weight: 760; letter-spacing: 0; }}
    .subtitle {{ margin-top: 3px; color: var(--muted); font-size: 12px; }}
    .meta {{ color: var(--muted); font-size: 13px; }}
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
    .queue {{
      min-height: 100%;
      overflow: hidden;
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
    .trace-event {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--panel-soft);
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
    .empty {{ padding: 40px 0; color: var(--muted); }}
    @media (max-width: 1100px) {{
      .console-shell {{ grid-template-columns: 1fr; }}
      .right-stack {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 760px) {{
      .topbar {{ grid-template-columns: 1fr; }}
      .console-shell {{ padding: 12px; }}
      .item {{ grid-template-columns: 1fr; }}
      .right-stack {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body data-peerassist-agent-console>
  <header class="topbar">
    <div class="brand">
      <div class="mark">PA</div>
      <div>
        <h1>PeerAssist Review Console</h1>
        <div class="subtitle">Evidence-grounded peer review queue for <code>{html.escape(paper_id)}</code></div>
      </div>
    </div>
    <div class="meta"><span class="mode-badge">{html.escape(mode or "unknown")} mode</span> · <span id="runtime-status">{len(events)} trace events</span></div>
  </header>
  <main class="console-shell">
    <aside class="rail">
      <section class="panel" data-panel="run-summary">
        <div class="panel-header">
          <p class="panel-title">Run Summary</p>
          <div class="panel-subtitle">Recoverable state from PeerAssist artifacts</div>
        </div>
        <div class="metric-grid">
          <div class="metric"><div class="metric-value">{pending_count}</div><div class="metric-label">Pending items</div></div>
          <div class="metric"><div class="metric-value">{actions_count}</div><div class="metric-label">Recorded actions</div></div>
          <div class="metric"><div class="metric-value">{len(agent_runs)}</div><div class="metric-label">Agent runs</div></div>
          <div class="metric"><div class="metric-value">{len(events)}</div><div class="metric-label">Trace events</div></div>
        </div>
      </section>
      <section class="panel" data-panel="agent-runs">
        <div class="panel-header">
          <p class="panel-title">Agent Timeline</p>
          <div class="panel-subtitle">Drafts, warnings, and incomplete checks</div>
        </div>
        {_render_agent_timeline(agent_runs)}
      </section>
    </aside>
    <section class="panel queue" data-panel="review-queue">
      <div class="panel-header">
        <p class="panel-title">Evidence Review Queue</p>
        <div class="panel-subtitle">Confirm, rewrite, downgrade, delete, or keep pending each concern</div>
      </div>
      <div class="queue-body">{rows}</div>
    </section>
    <aside class="right-stack">
      <section class="panel" data-panel="tool-trace">
        <div class="panel-header">
          <p class="panel-title">Tool Trace</p>
          <div class="panel-subtitle">MCP, Skills, and builtin capability lifecycle</div>
        </div>
        {_render_trace_events(events)}
      </section>
      <section class="panel" data-panel="human-confirmation">
        <div class="panel-header">
          <p class="panel-title">Human Confirmation</p>
          <div class="panel-subtitle">No confirmed concern is exported without evidence</div>
        </div>
        <div class="confirmation-note">
          Review edits are written to <code>human_confirmations.json</code>, then reports are regenerated in English and Chinese. Refreshing this page recovers queue, actions, paths, and trace state from disk.
        </div>
        {_render_invocations(invocations)}
      </section>
    </aside>
  </main>
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
        reason: action === 'rewrite' || action === 'downgrade' ? 'Edited in PeerAssist Review Console.' : ''
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
      const runtime = state.runtime || {{}};
      const status = document.getElementById('runtime-status');
      if (status) {{
        status.textContent = `${{runtime.tool_event_count || 0}} trace events · ${{state.actions_count || 0}} actions`;
      }}
    }}
    document.querySelectorAll('button[data-action]').forEach((button) => {{
      button.addEventListener('click', () => submitDecision(button).catch((error) => alert(error.message)));
    }});
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
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/" or self.path.startswith("/?"):
                self._send_html(render_confirmation_page(run_dir=run_dir, paper_id=paper_id))
                return
            if self.path == "/api/state":
                self._send_json(load_confirmation_state(run_dir=run_dir))
                return
            self.send_error(404, "not found")

        def do_POST(self) -> None:  # noqa: N802
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

    return ConfirmationHandler


def _render_item(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
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
        evidence_rows = '<div class="evidence-row"><span>No evidence attached.</span></div>'
    actions = item.get("allowed_actions") if isinstance(item.get("allowed_actions"), list) else []
    buttons = "".join(
        f'<button type="button" data-action="{html.escape(str(action))}">{html.escape(str(action).replace("_", " ").title())}</button>'
        for action in actions
    )
    source_agents = item.get("source_agent_ids") if isinstance(item.get("source_agent_ids"), list) else []
    source_agent_tags = "".join(
        f'<span class="tag">{html.escape(_display_name(str(agent_id)))}</span>'
        for agent_id in source_agents
    )
    previous_text = html.escape(str(item.get("author_action") or ""), quote=True)
    return f"""
<section class="item" data-concern-id="{html.escape(str(item.get('id', '')), quote=True)}" data-previous-text="{previous_text}">
  <div>
    <div class="tags">
      <span class="tag">{html.escape(str(item.get('status', '')))}</span>
      <span class="tag level">{html.escape(str(item.get('level', '')))}</span>
      <span class="tag">{html.escape(str(item.get('category', '')))}</span>
      {source_agent_tags}
    </div>
    <h2>{html.escape(str(item.get('title', 'Untitled concern')))}</h2>
    <p class="copy-block"><span class="label">Impact</span><br>{html.escape(str(item.get('impact', '')))}</p>
    <p class="copy-block"><span class="label">Benign explanation</span><br>{html.escape(str(item.get('benign_explanation', '')))}</p>
    <div class="evidence"><span class="label">Evidence</span>{evidence_rows}</div>
    <textarea aria-label="Suggested author action">{html.escape(str(item.get('author_action', '')))}</textarea>
  </div>
  <div class="actions">{buttons}</div>
</section>
"""


def _render_agent_timeline(agent_runs: list[Any]) -> str:
    rows = []
    for row in agent_runs:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "unknown")
        detail = (
            f"{int(row.get('draft_count') or 0)} drafts"
            f" · {int(row.get('warning_count') or 0)} warnings"
        )
        rows.append(
            f"""
<div class="agent-row">
  <div class="agent-dot"></div>
  <div>
    <div class="agent-name">{html.escape(_display_name(str(row.get('agent_id') or 'agent')))} {_status_pill(status)}</div>
    <div class="agent-detail">{html.escape(detail)}</div>
  </div>
</div>
"""
        )
    if not rows:
        rows.append('<div class="agent-detail">No agent results have been recorded yet.</div>')
    return f'<div class="agent-timeline">{"".join(rows)}</div>'


def _render_trace_events(events: list[Any]) -> str:
    rows = []
    for row in events[-8:]:
        if not isinstance(row, dict):
            continue
        summary = str(row.get("output_summary") or row.get("input_summary") or "")
        duration = row.get("duration_ms")
        duration_text = f" · {int(duration)} ms" if isinstance(duration, int) else ""
        rows.append(
            f"""
<div class="trace-event">
  <div class="trace-head">
    <div class="trace-tool">{html.escape(str(row.get('tool') or row.get('call_id') or 'tool'))}</div>
    {_status_pill(str(row.get('status') or 'unknown'))}
  </div>
  <div class="trace-summary">{html.escape(str(row.get('agent_id') or 'peerassist'))}{html.escape(duration_text)}</div>
  <div class="trace-summary">{html.escape(summary)}</div>
</div>
"""
        )
    if not rows:
        rows.append('<div class="trace-summary">No tool trace events have been recorded yet.</div>')
    return f'<div class="trace-list">{"".join(rows)}</div>'


def _render_invocations(invocations: list[Any]) -> str:
    rows = []
    for row in invocations[-4:]:
        if not isinstance(row, dict):
            continue
        artifacts = ", ".join(str(item) for item in row.get("artifact_ids", []) if item)
        rows.append(
            f"""
<div class="trace-event">
  <div class="trace-head">
    <div class="trace-tool">{html.escape(str(row.get('capability_name') or row.get('call_id') or 'capability'))}</div>
    {_status_pill(str(row.get('status') or 'unknown'))}
  </div>
  <div class="trace-summary">source={html.escape(str(row.get('source') or ''))} · attempts={html.escape(str(row.get('attempts') or 0))}</div>
  <div class="trace-summary">artifacts: {html.escape(artifacts or 'none')}</div>
</div>
"""
        )
    if not rows:
        rows.append('<div class="trace-summary">No capability invocations are available.</div>')
    return f'<div class="invocation-list">{"".join(rows)}</div>'


def _status_pill(status: str) -> str:
    normalized = status.replace(" ", "_").lower()
    return (
        f'<span class="status-pill status-{html.escape(normalized, quote=True)}">'
        f"{html.escape(status or 'unknown')}"
        "</span>"
    )


def _display_name(value: str) -> str:
    words = [word for word in value.replace("-", "_").split("_") if word]
    return " ".join(word.capitalize() for word in words) if words else "Agent"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
