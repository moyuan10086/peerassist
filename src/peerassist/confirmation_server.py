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
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PeerAssist Review Console</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17201b;
      --muted: #647067;
      --line: #d8ded8;
      --paper: #fbfbf7;
      --panel: #ffffff;
      --accent: #1f6f5b;
      --warn: #8a5a00;
      --danger: #9d2f2f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 15px/1.45 ui-serif, Georgia, "Times New Roman", serif;
      color: var(--ink);
      background: var(--paper);
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 24px;
      padding: 18px 28px;
      border-bottom: 1px solid var(--line);
      background: rgba(251, 251, 247, 0.96);
    }}
    h1 {{ margin: 0; font-size: 22px; font-weight: 700; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px 28px 40px; }}
    .meta {{ color: var(--muted); font-size: 13px; }}
    .item {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 270px;
      gap: 22px;
      padding: 20px 0;
      border-bottom: 1px solid var(--line);
    }}
    .item h2 {{ margin: 0 0 10px; font-size: 20px; }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }}
    .tag {{ border: 1px solid var(--line); padding: 2px 8px; font-size: 12px; background: var(--panel); }}
    .evidence {{ margin: 12px 0; padding: 10px 12px; border-left: 3px solid var(--accent); background: var(--panel); }}
    .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .actions {{ display: grid; gap: 8px; align-content: start; }}
    button {{
      width: 100%;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      padding: 9px 10px;
      text-align: left;
      cursor: pointer;
    }}
    button[data-action="confirm"] {{ border-color: var(--accent); color: var(--accent); }}
    button[data-action="delete"] {{ border-color: var(--danger); color: var(--danger); }}
    textarea {{ width: 100%; min-height: 76px; border: 1px solid var(--line); padding: 8px; font: inherit; }}
    .empty {{ padding: 40px 0; color: var(--muted); }}
    @media (max-width: 760px) {{
      header {{ display: block; }}
      main {{ padding: 18px; }}
      .item {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>PeerAssist Review Console</h1>
    <div class="meta">Paper <code>{html.escape(paper_id)}</code> · {state.get("actions_count", 0)} actions recorded</div>
  </header>
  <main>{rows}</main>
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
    document.querySelectorAll('button[data-action]').forEach((button) => {{
      button.addEventListener('click', () => submitDecision(button).catch((error) => alert(error.message)));
    }});
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
        f"<div><code>{html.escape(str(row.get('id', '')))}</code> {html.escape(str(row.get('locator', '')))}</div>"
        for row in evidence
        if isinstance(row, dict)
    )
    actions = item.get("allowed_actions") if isinstance(item.get("allowed_actions"), list) else []
    buttons = "".join(
        f'<button type="button" data-action="{html.escape(str(action))}">{html.escape(str(action).replace("_", " ").title())}</button>'
        for action in actions
    )
    previous_text = html.escape(str(item.get("author_action") or ""), quote=True)
    return f"""
<section class="item" data-concern-id="{html.escape(str(item.get('id', '')), quote=True)}" data-previous-text="{previous_text}">
  <div>
    <div class="tags">
      <span class="tag">{html.escape(str(item.get('status', '')))}</span>
      <span class="tag">{html.escape(str(item.get('level', '')))}</span>
      <span class="tag">{html.escape(str(item.get('category', '')))}</span>
    </div>
    <h2>{html.escape(str(item.get('title', 'Untitled concern')))}</h2>
    <p><span class="label">Impact</span><br>{html.escape(str(item.get('impact', '')))}</p>
    <p><span class="label">Benign explanation</span><br>{html.escape(str(item.get('benign_explanation', '')))}</p>
    <div class="evidence"><span class="label">Evidence</span>{evidence_rows}</div>
    <textarea aria-label="Suggested author action">{html.escape(str(item.get('author_action', '')))}</textarea>
  </div>
  <div class="actions">{buttons}</div>
</section>
"""
