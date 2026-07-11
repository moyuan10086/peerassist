import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as pdfjsLib from "pdfjs-dist";
import {
  Bot,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  FolderDown,
  GitBranch,
  Loader2,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";
import "./styles.css";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.mjs",
  import.meta.url,
).toString();

type WorkspaceWindow = {
  id: WindowId;
  label: string;
  path: string;
};

type WindowId = "paper" | "agent" | "queue" | "trace" | "confirm" | "artifacts";

type Evidence = {
  id?: string;
  locator?: string;
  page?: number | null;
  text?: string;
};

type Concern = {
  id: string;
  level?: string;
  category?: string;
  title?: string;
  impact?: string;
  benign_explanation?: string;
  author_action?: string;
  status?: string;
  evidence?: Evidence[];
  evidence_ids?: string[];
  source_agent_ids?: string[];
  allowed_actions?: string[];
};

type ToolEvent = {
  task_id?: string;
  call_id?: string;
  agent_id?: string;
  source?: string;
  tool?: string;
  status?: string;
  ts?: string;
  input_summary?: string;
  output_summary?: string;
  evidence_ids?: string[];
  artifact_ids?: string[];
};

type AgentRun = {
  agent_id?: string;
  status?: string;
  draft_count?: number;
  warnings?: string[];
  drafts?: Concern[];
};

type ConfirmationState = {
  schema_version?: string;
  runtime?: {
    mode?: string;
    queue_items?: number;
    agent_count?: number;
    tool_event_count?: number;
    capability_invocation_count?: number;
  };
  queue?: { items?: Concern[] };
  pending_count?: number;
  actions_count?: number;
  agent_runs?: AgentRun[];
  capability_invocations?: Record<string, unknown>[];
  tool_trace?: {
    events?: ToolEvent[];
    counts_by_status?: Record<string, number>;
  };
  evidence_preview?: Evidence[];
  paths?: Record<string, string>;
};

type Bootstrap = {
  schema_version?: string;
  paper_id: string;
  state: ConfirmationState;
  model_config: {
    provider?: string;
    model?: string;
    base_url?: string;
    api_key_configured?: string;
  };
  assets: {
    pdf_url?: string;
    legacy_url?: string;
  };
  windows: WorkspaceWindow[];
};

type PdfDocumentProxy = Awaited<ReturnType<typeof pdfjsLib.getDocument>> extends {
  promise: Promise<infer T>;
}
  ? T
  : never;

type PdfTextItem = {
  str: string;
  transform: number[];
  width?: number;
  height?: number;
};

const fallbackBootstrap: Bootstrap = {
  paper_id: "unknown",
  state: {},
  model_config: {},
  assets: { pdf_url: "/paper.pdf", legacy_url: "/legacy" },
  windows: [
    { id: "paper", label: "论文阅读", path: "/paper" },
    { id: "agent", label: "智能审稿", path: "/agent" },
    { id: "queue", label: "证据队列", path: "/queue" },
    { id: "trace", label: "工具追踪", path: "/trace" },
    { id: "confirm", label: "人工确认", path: "/confirm" },
    { id: "artifacts", label: "产物导出", path: "/artifacts" },
  ],
};

function readServerBootstrap(): Partial<Bootstrap> {
  const node = document.getElementById("peerassist-server-bootstrap");
  if (!node?.textContent) return {};
  try {
    const parsed = JSON.parse(node.textContent);
    return typeof parsed === "object" && parsed ? parsed : {};
  } catch {
    return {};
  }
}

function currentWindowFromPath(): WindowId {
  const path = window.location.pathname.replace(/\/$/, "") || "/paper";
  if (path.includes("agent")) return "agent";
  if (path.includes("queue")) return "queue";
  if (path.includes("trace")) return "trace";
  if (path.includes("confirm")) return "confirm";
  if (path.includes("artifacts")) return "artifacts";
  return "paper";
}

function App() {
  const initial = useMemo(() => ({ ...fallbackBootstrap, ...readServerBootstrap() }), []);
  const [bootstrap, setBootstrap] = useState<Bootstrap>(initial as Bootstrap);
  const [activeWindow, setActiveWindow] = useState<WindowId>(currentWindowFromPath());
  const [streamLines, setStreamLines] = useState<string[]>(["等待 PeerAssist 运行态事件流"]);
  const [lastReviewDraft, setLastReviewDraft] = useState("");
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState(false);

  const state = bootstrap.state || {};
  const queueItems = state.queue?.items || [];
  const events = state.tool_trace?.events || [];
  const agentRuns = state.agent_runs || [];
  const paths = state.paths || {};

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 2400);
  }, []);

  const refresh = useCallback(async () => {
    const response = await fetch("/api/bootstrap");
    if (!response.ok) throw new Error("无法读取 PeerAssist 状态");
    const payload = (await response.json()) as Bootstrap;
    setBootstrap(payload);
    return payload;
  }, []);

  useEffect(() => {
    refresh().catch(() => setStreamLines((lines) => [...lines, "初始状态读取失败，保留本地壳"]));
  }, [refresh]);

  useEffect(() => {
    if (!window.EventSource) return;
    const eventsSource = new EventSource("/api/events");
    eventsSource.addEventListener("state", (event) => {
      const statePayload = JSON.parse((event as MessageEvent).data) as ConfirmationState;
      setBootstrap((old) => ({ ...old, state: statePayload }));
      setStreamLines((lines) => [...lines.slice(-80), `state: 队列 ${statePayload.pending_count || 0} 条待确认`]);
    });
    eventsSource.addEventListener("heartbeat", () => {
      setStreamLines((lines) => [...lines.slice(-80), "heartbeat: 后端运行态通道正常"]);
    });
    eventsSource.onerror = () => {
      setStreamLines((lines) => [...lines.slice(-80), "error: 事件流中断，切换为手动刷新"]);
      eventsSource.close();
    };
    return () => eventsSource.close();
  }, []);

  const navigate = (windowId: WindowId, path: string) => {
    window.history.pushState({}, "", path);
    setActiveWindow(windowId);
  };

  useEffect(() => {
    const listener = () => setActiveWindow(currentWindowFromPath());
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  }, []);

  async function runAgentReview(selectedText = "", reviewMode = "fast") {
    setBusy(true);
    setLastReviewDraft("");
    setStreamLines((lines) => [...lines.slice(-80), `agent: 以 ${reviewMode} 模式启动智能审稿`]);
    try {
      const response = await fetch("/api/agent-review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_text: selectedText, review_mode: reviewMode }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "智能审稿失败");
      const structuredCount = Number(payload.structured_concern_count || 0);
      const queueItems = Number(payload.queue_items || 0);
      const suggestion = String(payload.suggestion || "");
      showToast(`已写入 ${structuredCount} 条结构化意见`);
      setLastReviewDraft(suggestion);
      setStreamLines((lines) => [
        ...lines.slice(-80),
        `agent: 审稿完成，新增 ${structuredCount} 条结构化意见，当前队列 ${queueItems} 条`,
      ]);
      await refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : "智能审稿失败";
      showToast(message);
      setStreamLines((lines) => [...lines.slice(-80), `agent-error: ${message}`]);
    } finally {
      setBusy(false);
    }
  }

  async function submitManualConcern(selectedText: string, note: string, page: string) {
    setBusy(true);
    try {
      const response = await fetch("/api/manual-concern", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_text: selectedText, note, page: Number(page) || 0 }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "加入队列失败");
      showToast(`已加入队列：${payload.concern_id}`);
      await refresh();
      navigate("queue", "/queue");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "加入队列失败");
    } finally {
      setBusy(false);
    }
  }

  async function submitDecision(concern: Concern, action: string) {
    setBusy(true);
    try {
      const response = await fetch("/api/decision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          concern_id: concern.id,
          action,
          reviewer_id: "local-reviewer",
          timestamp: new Date().toISOString(),
          previous_text: concern.author_action || "",
          new_text: concern.author_action || "",
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "确认失败");
      showToast(`已记录：${labelAction(action)}`);
      await refresh();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "确认失败");
    } finally {
      setBusy(false);
    }
  }

  const activeLabel = bootstrap.windows.find((item) => item.id === activeWindow)?.label || "论文阅读";

  return (
    <div className="app-shell">
      <aside className="app-sidebar" aria-label="PeerAssist 工作窗口">
        <button className="brand" type="button" onClick={() => navigate("paper", "/paper")}>
          <span className="brand-mark">PA</span>
          <span>
            <strong>PeerAssist</strong>
            <small>论文审核辅助系统</small>
          </span>
        </button>
        <nav className="window-nav">
          {bootstrap.windows.map((item) => (
            <button
              key={item.id}
              type="button"
              className="nav-link"
              aria-current={activeWindow === item.id ? "page" : undefined}
              onClick={() => navigate(item.id, item.path)}
            >
              <span className="nav-icon">{windowIcon(item.id)}</span>
              <span className="nav-label">{item.label}</span>
              <span className="nav-meta">{windowMeta(item.id, state)}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span>{bootstrap.model_config.model || "模型未配置"}</span>
          <a href={bootstrap.assets.legacy_url || "/legacy"}>旧版 PDF.js 审稿台</a>
        </div>
      </aside>

      <main className="workspace">
        <header className="workspace-topbar">
          <div>
            <p className="eyebrow">现代智能体审稿工作区</p>
            <h1>{activeLabel}</h1>
          </div>
          <div className="topbar-actions">
            <button className="ghost-button" type="button" onClick={() => refresh().then(() => showToast("状态已刷新"))}>
              <RefreshCw size={16} /> 刷新状态
            </button>
            <button className="primary-button" type="button" disabled={busy} onClick={() => navigate("agent", "/agent")}>
              <Bot size={16} /> 打开智能审稿
            </button>
          </div>
        </header>

        <StatusStrip state={state} model={bootstrap.model_config.model || ""} />

        {activeWindow === "paper" && (
          <PaperWindow
            pdfUrl={bootstrap.assets.pdf_url || ""}
            queueItems={queueItems}
            busy={busy}
            onRunReview={runAgentReview}
            onSubmitManual={submitManualConcern}
          />
        )}
        {activeWindow === "agent" && (
          <AgentWindow
            busy={busy}
            model={bootstrap.model_config.model || ""}
            baseUrl={bootstrap.model_config.base_url || ""}
            agentRuns={agentRuns}
            streamLines={streamLines}
            lastReviewDraft={lastReviewDraft}
            onRunReview={runAgentReview}
          />
        )}
        {activeWindow === "queue" && (
          <QueueWindow items={queueItems} onDecision={submitDecision} onOpenConfirm={() => navigate("confirm", "/confirm")} />
        )}
        {activeWindow === "trace" && <TraceWindow events={events} streamLines={streamLines} />}
        {activeWindow === "confirm" && <ConfirmWindow items={queueItems} busy={busy} onDecision={submitDecision} />}
        {activeWindow === "artifacts" && <ArtifactsWindow paths={paths} />}
        {toast && <div className="toast">{toast}</div>}
      </main>
    </div>
  );
}

function StatusStrip({ state, model }: { state: ConfirmationState; model: string }) {
  const metrics = [
    ["待确认", String(state.pending_count || 0)],
    ["已处理", String(state.actions_count || 0)],
    ["工具事件", String(state.runtime?.tool_event_count || state.tool_trace?.events?.length || 0)],
    ["模型", model || "未配置"],
  ];
  return (
    <section className="status-strip" aria-label="PeerAssist 状态指标">
      {metrics.map(([label, value]) => (
        <div className="metric-card" key={label}>
          <small>{label}</small>
          <strong>{value}</strong>
        </div>
      ))}
    </section>
  );
}

function PaperWindow({
  pdfUrl,
  queueItems,
  busy,
  onRunReview,
  onSubmitManual,
}: {
  pdfUrl: string;
  queueItems: Concern[];
  busy: boolean;
  onRunReview: (selectedText?: string, reviewMode?: string) => void;
  onSubmitManual: (selectedText: string, note: string, page: string) => void;
}) {
  const [selectedText, setSelectedText] = useState("");
  const [note, setNote] = useState("");
  const [page, setPage] = useState("1");
  const handlePdfSelection = useCallback((payload: { text: string; page: number }) => {
    setSelectedText(payload.text);
    setPage(String(payload.page));
  }, []);
  return (
    <section className="paper-grid">
      <div className="panel pdf-panel">
        <div className="panel-head">
          <h2>原文 PDF</h2>
          <span className="tag good">可选中文字</span>
        </div>
        {pdfUrl ? (
          <PdfReviewReader pdfUrl={pdfUrl} onSelection={handlePdfSelection} />
        ) : (
          <div className="empty-pdf">当前运行目录没有发现原始 PDF。</div>
        )}
      </div>
      <aside className="inspector">
        <div className="panel">
          <div className="panel-head">
            <h2>选区审稿</h2>
            <span className="tag">人工入口</span>
          </div>
          <div className="panel-body selection-box">
            <p className="muted">在 PDF 中选中文字后，可把关键原文粘贴到这里，交给智能体审稿或作为人工关注点入队。</p>
            <textarea value={selectedText} onChange={(event) => setSelectedText(event.target.value)} placeholder="粘贴 PDF 选中的原文..." />
            <div className="field-row">
              <input value={page} onChange={(event) => setPage(event.target.value)} aria-label="PDF 页码" />
              <button className="ghost-button" type="button" disabled={busy} onClick={() => onRunReview(selectedText, "fast")}>
                <Send size={15} /> 基于选区审稿
              </button>
            </div>
            <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="人工批注，例如：请作者解释统计显著性阈值..." />
            <button className="primary-button" type="button" disabled={busy} onClick={() => onSubmitManual(selectedText, note, page)}>
              <ClipboardCheck size={15} /> 加入证据队列
            </button>
          </div>
        </div>
        <div className="panel">
          <div className="panel-head">
            <h2>本页审稿线索</h2>
            <span className="tag">{queueItems.length} 条</span>
          </div>
          <div className="panel-body concern-list">
            {queueItems.slice(0, 5).map((item) => (
              <ConcernCard concern={item} compact key={item.id} />
            ))}
          </div>
        </div>
      </aside>
    </section>
  );
}

function PdfReviewReader({
  pdfUrl,
  onSelection,
}: {
  pdfUrl: string;
  onSelection: (payload: { text: string; page: number }) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const [pdfDoc, setPdfDoc] = useState<PdfDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1.18);
  const [status, setStatus] = useState("正在加载 PDF");

  useEffect(() => {
    let cancelled = false;
    setStatus("正在加载 PDF");
    const loadingTask = pdfjsLib.getDocument({ url: pdfUrl });
    loadingTask.promise
      .then((document) => {
        if (cancelled) return;
        setPdfDoc(document);
        setPageCount(document.numPages);
        setPageNumber(1);
        setStatus(`已载入 ${document.numPages} 页`);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setStatus(error instanceof Error ? `PDF 加载失败：${error.message}` : "PDF 加载失败");
      });
    return () => {
      cancelled = true;
      loadingTask.destroy();
    };
  }, [pdfUrl]);

  useEffect(() => {
    if (!pdfDoc || !canvasRef.current || !textLayerRef.current) return;
    let cancelled = false;
    const canvas = canvasRef.current;
    const textLayer = textLayerRef.current;
    const context = canvas.getContext("2d");
    if (!context) return;
    textLayer.replaceChildren();
    setStatus(`正在渲染第 ${pageNumber} 页`);
    pdfDoc
      .getPage(pageNumber)
      .then(async (page) => {
        if (cancelled) return;
        const viewport = page.getViewport({ scale });
        const pixelRatio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(viewport.width * pixelRatio);
        canvas.height = Math.floor(viewport.height * pixelRatio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        textLayer.style.width = `${viewport.width}px`;
        textLayer.style.height = `${viewport.height}px`;
        context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
        await page.render({ canvas, canvasContext: context, viewport }).promise;
        const textContent = await page.getTextContent();
        if (cancelled) return;
        textLayer.replaceChildren();
        for (const rawItem of textContent.items) {
          if (!("str" in rawItem) || !rawItem.str.trim()) continue;
          const item = rawItem as PdfTextItem;
          const transform = pdfjsLib.Util.transform(viewport.transform, item.transform);
          const textNode = document.createElement("span");
          textNode.className = "pdf-text-item";
          textNode.textContent = item.str;
          textNode.style.left = `${transform[4]}px`;
          textNode.style.top = `${transform[5]}px`;
          textNode.style.fontSize = `${Math.max(8, Math.hypot(transform[2], transform[3]))}px`;
          textNode.style.transform = "translateY(-100%)";
          if (item.width) textNode.style.width = `${item.width * scale}px`;
          textLayer.appendChild(textNode);
        }
        setStatus(`第 ${pageNumber} / ${pdfDoc.numPages} 页`);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setStatus(error instanceof Error ? `PDF 渲染失败：${error.message}` : "PDF 渲染失败");
      });
    return () => {
      cancelled = true;
    };
  }, [pdfDoc, pageNumber, scale]);

  const captureSelection = () => {
    const selection = window.getSelection();
    const text = selection?.toString().replace(/\s+/g, " ").trim() || "";
    if (!text || !textLayerRef.current || !selection?.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (!textLayerRef.current.contains(range.commonAncestorContainer)) return;
    onSelection({ text, page: pageNumber });
  };

  const goToPage = (nextPage: number) => {
    setPageNumber(Math.max(1, Math.min(pageCount || 1, nextPage)));
  };

  return (
    <div className="pdf-reader">
      <div className="pdf-toolbar">
        <div className="button-row">
          <button className="ghost-button" type="button" disabled={pageNumber <= 1} onClick={() => goToPage(pageNumber - 1)}>
            上一页
          </button>
          <button className="ghost-button" type="button" disabled={pageNumber >= pageCount} onClick={() => goToPage(pageNumber + 1)}>
            下一页
          </button>
        </div>
        <span className="pdf-status">{status}</span>
        <div className="button-row">
          <button className="ghost-button" type="button" onClick={() => setScale((value) => Math.max(0.72, value - 0.12))}>
            缩小
          </button>
          <button className="ghost-button" type="button" onClick={() => setScale((value) => Math.min(2.2, value + 0.12))}>
            放大
          </button>
        </div>
      </div>
      <div className="pdf-scroll">
        <div className="pdf-page-shell" onMouseUp={captureSelection}>
          <canvas ref={canvasRef} className="pdf-canvas" />
          <div ref={textLayerRef} className="pdf-text-layer" />
        </div>
      </div>
    </div>
  );
}

function AgentWindow({
  busy,
  model,
  baseUrl,
  agentRuns,
  streamLines,
  lastReviewDraft,
  onRunReview,
}: {
  busy: boolean;
  model: string;
  baseUrl: string;
  agentRuns: AgentRun[];
  streamLines: string[];
  lastReviewDraft: string;
  onRunReview: (selectedText?: string, reviewMode?: string) => void;
}) {
  const [mode, setMode] = useState("fast");
  return (
    <section className="agent-grid">
      <div className="panel">
        <div className="panel-head">
          <h2>智能审稿控制台</h2>
          <span className="tag good">{model || "模型未配置"}</span>
        </div>
        <div className="panel-body review-run-box">
          <p className="muted">后端会读取证据台账、确定性核查、多代理结果和人工队列，生成可追溯中文审稿意见。</p>
          <div className="segmented">
            {["fast", "standard", "deep"].map((item) => (
              <button key={item} type="button" aria-pressed={mode === item} onClick={() => setMode(item)}>
                {labelMode(item)}
              </button>
            ))}
          </div>
          <button className="primary-button wide" type="button" disabled={busy} onClick={() => onRunReview("", mode)}>
            {busy ? <Loader2 className="spin" size={16} /> : <Bot size={16} />} 开始全篇智能审稿
          </button>
          <div className="model-box">
            <code>{baseUrl || "未配置 Base URL"}</code>
          </div>
        </div>
      </div>
      <div className="panel">
        <div className="panel-head">
          <h2>实时数据流</h2>
          <span className="tag">EventSource</span>
        </div>
        <div className="stream-console">
          {streamLines.slice(-40).map((line, index) => (
            <div className="stream-line" key={`${line}-${index}`}>
              <span className="stream-kind">{line.split(":")[0]}</span>
              <span className="stream-message">{line.includes(":") ? line.slice(line.indexOf(":") + 1).trim() : line}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="panel full-span">
        <div className="panel-head">
          <h2>审稿草稿预览</h2>
          <span className="tag">模型输出</span>
        </div>
        <ReviewDraftPreview draft={lastReviewDraft} />
      </div>
      <div className="panel full-span">
        <div className="panel-head">
          <h2>多代理结果</h2>
          <span className="tag">{agentRuns.length} 个代理</span>
        </div>
        <div className="panel-body agent-list">
          {agentRuns.map((run) => (
            <div className="agent-card" key={run.agent_id}>
              <div className="tag-row">
                <span className="tag">{run.status || "unknown"}</span>
                <span className="tag">{run.draft_count || 0} 条草稿</span>
              </div>
              <h3>{run.agent_id || "未命名代理"}</h3>
              <p className="muted">{(run.warnings || []).join("；") || "暂无警告。"}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ReviewDraftPreview({ draft }: { draft: string }) {
  const cleanDraft = draft.trim();
  if (!cleanDraft) {
    return (
      <div className="panel-body draft-empty">
        启动智能审稿后，完整草稿会显示在这里；实时数据流只保留状态事件和简短摘要。
      </div>
    );
  }
  return (
    <div className="draft-preview">
      {cleanDraft.split(/\n{2,}/).map((block, index) => {
        const normalized = block.trim();
        if (!normalized) return null;
        if (normalized.startsWith("#")) {
          return <h3 key={`${normalized}-${index}`}>{normalized.replace(/^#+\s*/, "")}</h3>;
        }
        return <p key={`${normalized}-${index}`}>{normalized.replace(/^[-*]\s*/, "")}</p>;
      })}
    </div>
  );
}

function QueueWindow({
  items,
  onDecision,
  onOpenConfirm,
}: {
  items: Concern[];
  onDecision: (concern: Concern, action: string) => void;
  onOpenConfirm: () => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = items.filter((item) => JSON.stringify(item).toLowerCase().includes(query.toLowerCase()));
  return (
    <section className="queue-grid">
      <div className="panel">
        <div className="panel-head">
          <h2>证据队列</h2>
          <div className="search-box">
            <Search size={15} />
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索关注点、证据、代理..." />
          </div>
        </div>
        <div className="panel-body concern-list">
          {filtered.map((item) => (
            <ConcernCard concern={item} key={item.id} onDecision={onDecision} />
          ))}
        </div>
      </div>
      <aside className="panel">
        <div className="panel-head">
          <h2>队列策略</h2>
        </div>
        <div className="panel-body timeline">
          {["证据绑定", "确定性核查", "代理审稿", "人工确认", "报告导出"].map((step, index) => (
            <div className="timeline-step" key={step}>
              <span className="timeline-dot">{index + 1}</span>
              <div>
                <strong>{step}</strong>
                <p className="muted">每一步都保留证据来源与人工决策记录。</p>
              </div>
            </div>
          ))}
          <button className="primary-button" type="button" onClick={onOpenConfirm}>
            <CheckCircle2 size={15} /> 进入人工确认
          </button>
        </div>
      </aside>
    </section>
  );
}

function TraceWindow({ events, streamLines }: { events: ToolEvent[]; streamLines: string[] }) {
  return (
    <section className="trace-grid">
      <div className="panel">
        <div className="panel-head">
          <h2>MCP / Skills 工具追踪</h2>
          <span className="tag">{events.length} 条事件</span>
        </div>
        <div className="panel-body event-list">
          {events.map((event, index) => (
            <div className="event-card" key={`${event.call_id}-${index}`}>
              <div className="tag-row">
                <span className={`tag ${event.status === "failed" ? "danger" : event.status === "completed" ? "good" : "warn"}`}>
                  {event.status || "unknown"}
                </span>
                <span className="tag">{event.source || "tool"}</span>
              </div>
              <h3>{event.tool || event.call_id || "工具调用"}</h3>
              <p>{event.output_summary || event.input_summary || "暂无摘要。"}</p>
              <code>{event.ts || ""}</code>
            </div>
          ))}
        </div>
      </div>
      <div className="panel">
        <div className="panel-head">
          <h2>流式日志</h2>
        </div>
        <div className="stream-console">
          {streamLines.slice(-50).map((line, index) => (
            <div className="stream-line" key={`${line}-${index}`}>
              <span className="stream-kind">{index + 1}</span>
              <span>{line}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ConfirmWindow({
  items,
  busy,
  onDecision,
}: {
  items: Concern[];
  busy: boolean;
  onDecision: (concern: Concern, action: string) => void;
}) {
  const pending = items.filter((item) => String(item.status || "").toLowerCase().includes("pending"));
  return (
    <section className="confirm-grid">
      <div className="panel">
        <div className="panel-head">
          <h2>人工逐条确认</h2>
          <span className="tag warn">{pending.length} 条待处理</span>
        </div>
        <div className="panel-body concern-list">
          {pending.map((item) => (
            <ConcernCard concern={item} disabled={busy} key={item.id} onDecision={onDecision} />
          ))}
        </div>
      </div>
      <aside className="panel">
        <div className="panel-head">
          <h2>确认原则</h2>
        </div>
        <div className="panel-body">
          <p className="muted">PeerAssist 只辅助审稿，不自动替代审稿人判断。每条意见必须先看证据、再确认、改写、降级或删除。</p>
          <div className="tag-row">
            <span className="tag good">证据忠实</span>
            <span className="tag">可追溯</span>
            <span className="tag warn">人工闸门</span>
          </div>
        </div>
      </aside>
    </section>
  );
}

function ArtifactsWindow({ paths }: { paths: Record<string, string> }) {
  const entries = Object.entries(paths);
  return (
    <section className="artifact-grid">
      <div className="panel">
        <div className="panel-head">
          <h2>产物导出</h2>
          <span className="tag">{entries.length} 个产物</span>
        </div>
        <div className="panel-body artifact-list">
          {entries.map(([key, value]) => (
            <div className="artifact-card" key={key}>
              <div className="tag-row">
                <span className="tag">{artifactLabel(key)}</span>
              </div>
              <h3>{key}</h3>
              <code>{value}</code>
            </div>
          ))}
        </div>
      </div>
      <aside className="panel">
        <div className="panel-head">
          <h2>导出说明</h2>
        </div>
        <div className="panel-body">
          <p className="muted">报告、证据台账、确认队列、工具追踪和原始 PDF 均保留本地路径，便于复现实验与飞书同步。</p>
        </div>
      </aside>
    </section>
  );
}

function ConcernCard({
  concern,
  compact,
  disabled,
  onDecision,
}: {
  concern: Concern;
  compact?: boolean;
  disabled?: boolean;
  onDecision?: (concern: Concern, action: string) => void;
}) {
  const evidence = concern.evidence || [];
  return (
    <article className="concern-card">
      <div className="tag-row">
        <span className={`tag ${concern.level === "major_concern" ? "danger" : "warn"}`}>{labelLevel(concern.level || "")}</span>
        <span className="tag">{labelCategory(concern.category || "")}</span>
        <span className="tag">{labelStatus(concern.status || "")}</span>
      </div>
      <h3>{concern.title || concern.id}</h3>
      {!compact && <p>{concern.impact || "暂无影响说明。"}</p>}
      <p className="muted">{concern.author_action || concern.benign_explanation || "暂无作者行动建议。"}</p>
      <div className="evidence-list">
        {evidence.map((item) => (
          <code key={item.id || item.locator}>{item.id || "证据"} · {item.locator || "无定位"}</code>
        ))}
      </div>
      {onDecision && (
        <div className="button-row">
          {["confirm", "rewrite", "downgrade", "delete"].map((action) => (
            <button className={action === "delete" ? "danger-button" : "ghost-button"} disabled={disabled} key={action} type="button" onClick={() => onDecision(concern, action)}>
              {labelAction(action)}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}

function windowIcon(id: WindowId) {
  const icons: Record<WindowId, React.ReactNode> = {
    paper: <FileText size={17} />,
    agent: <Bot size={17} />,
    queue: <GitBranch size={17} />,
    trace: <TerminalSquare size={17} />,
    confirm: <ShieldCheck size={17} />,
    artifacts: <FolderDown size={17} />,
  };
  return icons[id];
}

function windowMeta(id: WindowId, state: ConfirmationState) {
  if (id === "queue" || id === "confirm") return String(state.pending_count || 0);
  if (id === "trace") return String(state.runtime?.tool_event_count || 0);
  if (id === "agent") return String(state.runtime?.agent_count || 0);
  return "";
}

function labelLevel(value: string) {
  return ({
    major_concern: "主要问题",
    minor_concern: "次要问题",
    clarification_needed: "需要澄清",
    editor_note: "编辑提示",
  } as Record<string, string>)[value] || value || "待分类";
}

function labelCategory(value: string) {
  return ({
    methodology: "方法",
    statistics: "统计",
    figure_table: "图表",
    citation: "引用",
    reproducibility: "复现",
    manual_annotation: "人工批注",
  } as Record<string, string>)[value] || value || "其他";
}

function labelStatus(value: string) {
  return value === "pending_human_confirmation" ? "待人工确认" : value || "未知状态";
}

function labelAction(value: string) {
  return ({
    confirm: "确认",
    rewrite: "改写",
    downgrade: "降级",
    delete: "删除",
    mark_pending: "保留待定",
  } as Record<string, string>)[value] || value;
}

function labelMode(value: string) {
  return ({ fast: "快速", standard: "标准", deep: "深入" } as Record<string, string>)[value] || value;
}

function artifactLabel(value: string) {
  return ({
    evidence_ledger: "证据台账",
    queue: "确认队列",
    confirmations: "人工确认",
    agent_results: "代理结果",
    capability_invocations: "能力调用",
    tool_trace: "工具追踪",
    source_pdf: "原始 PDF",
  } as Record<string, string>)[value] || "产物";
}

createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
