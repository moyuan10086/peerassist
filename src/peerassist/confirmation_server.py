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
    evidence_count = _evidence_count(items)
    completed_event_count = _event_status_count(events, "completed")
    failed_event_count = _event_status_count(events, "failed")
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
      --paper: #f6f6f1;
      --panel: #ffffff;
      --panel-soft: #f9faf6;
      --panel-tint: #eef6ff;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --blue: #2458a6;
      --amber: #9a6500;
      --danger: #a33a3a;
      --violet: #5f4b8b;
      --shadow: 0 18px 46px rgba(24, 32, 31, 0.10);
      --mono-panel: #202724;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.5 "Microsoft YaHei", "PingFang SC", "Segoe UI", ui-sans-serif, sans-serif;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(15, 118, 110, 0.04) 1px, transparent 1px),
        linear-gradient(180deg, rgba(36, 88, 166, 0.035) 1px, transparent 1px),
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
      border-bottom: 1px solid var(--line);
      background: rgba(246, 246, 241, 0.96);
      backdrop-filter: blur(16px);
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
      color: var(--accent-strong);
      background: #eef8f4;
      font-weight: 800;
    }}
    h1 {{ margin: 0; font-size: 19px; line-height: 1.15; font-weight: 760; letter-spacing: 0; }}
    .subtitle {{ margin-top: 3px; color: var(--muted); font-size: 12px; }}
    .meta {{ color: var(--muted); font-size: 13px; }}
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
    }}
    .stream-console div + div {{ margin-top: 5px; }}
    .stream-key {{ color: #9ad0c4; }}
    .empty {{ padding: 40px 0; color: var(--muted); }}
    @media (max-width: 1100px) {{
      .ops-strip {{ grid-template-columns: 1fr 1fr; }}
      .console-shell {{ grid-template-columns: 1fr; }}
      .right-stack {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 760px) {{
      .topbar {{ grid-template-columns: 1fr; }}
      .ops-strip {{ width: calc(100% - 24px); grid-template-columns: 1fr; }}
      .console-shell {{ padding: 12px; }}
      .item {{ grid-template-columns: 1fr; }}
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
        <div class="subtitle">面向 <code>{html.escape(paper_id)}</code> 的证据审稿与人工确认队列</div>
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
      <div class="ops-label">事件流、证据队列与人工闸门同步展示</div>
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
      <section class="panel" data-panel="session-navigator">
        <div class="panel-header">
          <p class="panel-title">会话导览</p>
          <div class="panel-subtitle">按审稿流转顺序定位当前任务</div>
        </div>
        <nav class="session-nav" aria-label="PeerAssist 会话导览">
          <a class="nav-step" href="#review-queue"><span class="step-index">1</span><span><span class="step-title">证据队列</span><span class="step-copy">先看关注点与证据定位</span></span></a>
          <a class="nav-step" href="#tool-trace"><span class="step-index">2</span><span><span class="step-title">工具追踪</span><span class="step-copy">核对 MCP/Skills 调用过程</span></span></a>
          <a class="nav-step" href="#human-confirmation"><span class="step-index">3</span><span><span class="step-title">人工闸门</span><span class="step-copy">确认、改写、降级或删除</span></span></a>
        </nav>
      </section>
      <section class="panel" data-panel="agent-runs">
        <div class="panel-header">
          <p class="panel-title">代理时间线</p>
          <div class="panel-subtitle">草稿、警告与未完成核查</div>
        </div>
        {_render_agent_timeline(agent_runs)}
      </section>
    </aside>
    <section class="panel queue" data-panel="review-queue" id="review-queue">
      <div class="panel-header">
        <p class="panel-title">证据审稿队列</p>
        <div class="panel-subtitle">逐条确认、改写、降级、删除或暂挂关注点</div>
      </div>
      <div class="queue-body">{rows}</div>
    </section>
    <aside class="right-stack">
      <section class="panel" data-panel="evidence-focus">
        <div class="panel-header">
          <p class="panel-title">证据焦点</p>
          <div class="panel-subtitle">优先核对当前队列绑定的证据位置</div>
        </div>
        {_render_evidence_focus(items)}
      </section>
      <section class="panel" data-panel="tool-trace" id="tool-trace">
        <div class="panel-header">
          <p class="panel-title">工具追踪</p>
          <div class="panel-subtitle">MCP、Skills 与内置能力调用生命周期</div>
        </div>
        {_render_trace_events(events)}
      </section>
      <section class="panel" data-panel="human-confirmation" id="human-confirmation">
        <div class="panel-header">
          <p class="panel-title">人工确认</p>
          <div class="panel-subtitle">没有证据的关注点不会进入确认报告</div>
        </div>
        <div class="confirmation-note">
          审稿人的每次编辑都会写入 <code>human_confirmations.json</code>，随后重新生成中文与英文报告。刷新页面时会从磁盘恢复队列、动作、路径和工具追踪状态。
        </div>
        <div class="stream-console" data-panel="stream-console">
          <div><span class="stream-key">事件：</span> state</div>
          <div><span class="stream-key">通道：</span> /api/events + /api/state 兜底</div>
          <div><span class="stream-key">约束：</span> 证据绑定的人工决策日志</div>
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
    }}
    function connectEventStream() {{
      if (!window.EventSource) {{
        refreshRuntime().catch(() => {{}});
        return;
      }}
      const events = new EventSource('/api/events');
      events.addEventListener('state', (event) => {{
        updateRuntime(JSON.parse(event.data), 'stream');
        events.close();
      }});
      events.onerror = () => {{
        events.close();
        refreshRuntime().catch(() => {{}});
      }};
    }}
    document.querySelectorAll('button[data-action]').forEach((button) => {{
      button.addEventListener('click', () => submitDecision(button).catch((error) => alert(error.message)));
    }});
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
        def do_GET(self) -> None:  # noqa: N802
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

        def _send_sse_state(self, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False)
            body = f"event: state\ndata: {data}\n\n".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
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
        evidence_rows = '<div class="evidence-row"><span>暂无绑定证据。</span></div>'
    actions = item.get("allowed_actions") if isinstance(item.get("allowed_actions"), list) else []
    buttons = "".join(
        f'<button type="button" data-action="{html.escape(str(action))}">{html.escape(_localized_action(str(action)))}</button>'
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
