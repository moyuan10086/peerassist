import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as pdfjsLib from "pdfjs-dist";
import {
  Bot,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Columns2,
  Download,
  ExternalLink,
  FileText,
  FileUp,
  FolderDown,
  GitBranch,
  History,
  Loader2,
  Maximize2,
  MessageSquareText,
  Minus,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Square,
  TerminalSquare,
} from "lucide-react";
import "./styles.css";

type GetOrInsertComputed = (key: unknown, callback: (key: unknown) => unknown) => unknown;

const mapPrototype = Map.prototype as Map<unknown, unknown> & {
  getOrInsertComputed?: GetOrInsertComputed;
};
if (!mapPrototype.getOrInsertComputed) {
  Object.defineProperty(mapPrototype, "getOrInsertComputed", {
    configurable: true,
    value(this: Map<unknown, unknown>, key: unknown, callback: (key: unknown) => unknown) {
      if (this.has(key)) return this.get(key);
      const value = callback(key);
      this.set(key, value);
      return value;
    },
  });
}

const weakMapPrototype = WeakMap.prototype as WeakMap<object, unknown> & {
  getOrInsertComputed?: GetOrInsertComputed;
};
if (!weakMapPrototype.getOrInsertComputed) {
  Object.defineProperty(weakMapPrototype, "getOrInsertComputed", {
    configurable: true,
    value(this: WeakMap<object, unknown>, key: object, callback: (key: object) => unknown) {
      if (this.has(key)) return this.get(key);
      const value = callback(key);
      this.set(key, value);
      return value;
    },
  });
}

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/legacy/build/pdf.worker.min.mjs",
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
  bbox?: number[] | null;
};

type Concern = {
  id: string;
  finding_lineage_id?: string;
  finding_id?: string;
  revision?: number;
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

type ReviewJob = {
  id: string;
  paper_id: string;
  status: string;
  stage: string;
  revision: number;
  confirmation_revision?: number;
  required_consents?: string[];
  resume_stage?: string | null;
  degradation_code?: string | null;
  degraded_services?: string[];
  error?: string | null;
  updated_at?: string;
  timeline?: ReviewJobTimeline;
};

type ReviewJobTimelineEvent = {
  event_id: number;
  event_type: string;
  timestamp: string;
  stage?: string | null;
  status?: string | null;
  attempt: number;
  duration_ms?: number | null;
  details?: { service?: string };
};

type ReviewJobTimeline = {
  event_count: number;
  last_event_id: number;
  items: ReviewJobTimelineEvent[];
};

type ReportArtifact = {
  name: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  download_url: string;
};

type ReportArtifacts = {
  ready?: boolean;
  report_version?: string;
  confirmation_revision?: number;
  items?: ReportArtifact[];
};

type CitationDifference = {
  field: string;
  manuscript_value: string;
  external_value: string;
  comparison: string;
  rule: string;
};

type CitationVerificationSummary = {
  source: string;
  status: string;
  checked_at: string;
  source_url: string;
  field_differences: CitationDifference[];
  error_code?: string;
};

type CitationReferenceSummary = {
  id: string;
  reference_number?: number;
  raw_text: string;
  title: string;
  doi: string;
  year?: number | null;
  evidence?: Evidence[];
  verification?: CitationVerificationSummary | null;
};

type CitationLinkSummary = {
  id: string;
  reference_number: number;
  status: string;
  mention: Evidence;
  references: CitationReferenceSummary[];
  verification?: CitationVerificationSummary | null;
  findings: { id: string; status: string; severity: string; message: string; requires_human_review: boolean }[];
  concern_id?: string;
  concern_status?: string;
};

type CitationAuditSummary = {
  available?: boolean;
  parse_version?: string;
  coverage?: Record<string, number>;
  records?: CitationReferenceSummary[];
  links?: CitationLinkSummary[];
  warnings?: string[];
};

type PdfConcernAnnotation = {
  id: string;
  concernId: string;
  page: number;
  title: string;
  level: string;
  status: string;
  evidence: Evidence;
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
  confirmation_revision?: number;
  ready_for_confirmation?: boolean;
  agent_runs?: AgentRun[];
  capability_invocations?: Record<string, unknown>[];
  tool_trace?: {
    events?: ToolEvent[];
    counts_by_status?: Record<string, number>;
  };
  evidence_preview?: Evidence[];
  paths?: Record<string, string>;
  artifacts?: ReportArtifacts;
  citation_audit?: CitationAuditSummary;
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
  const [reviewJobs, setReviewJobs] = useState<ReviewJob[]>([]);
  const [activePaperId, setActivePaperId] = useState(() => window.localStorage.getItem("peerassist.activePaperId") || "");
  const [activeJobId, setActiveJobId] = useState(() => window.localStorage.getItem("peerassist.activeJobId") || "");
  const [jobWorkspace, setJobWorkspace] = useState<ConfirmationState | null>(null);
  const [activePdfUrl, setActivePdfUrl] = useState(() => {
    const storedPaperId = window.localStorage.getItem("peerassist.activePaperId") || "";
    return storedPaperId ? `/api/papers/${storedPaperId}/source` : initial.assets.pdf_url || "";
  });

  const state = activeJobId && jobWorkspace ? jobWorkspace : bootstrap.state || {};
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

  const refreshJobs = useCallback(async () => {
    const response = await fetch("/api/jobs", { cache: "no-store" });
    if (!response.ok) throw new Error("无法读取后台审稿任务");
    const payload = (await response.json()) as { jobs?: ReviewJob[] };
    const jobs = payload.jobs || [];
    jobs.sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
    setReviewJobs(jobs);
    return jobs;
  }, []);

  const refreshJobWorkspace = useCallback(async (jobId: string) => {
    if (!jobId) return null;
    const response = await fetch(`/api/jobs/${jobId}/workspace`, { cache: "no-store" });
    if (!response.ok) throw new Error("无法读取当前审稿任务工作区");
    const payload = (await response.json()) as { state?: ConfirmationState };
    const nextState = payload.state || {};
    setJobWorkspace(nextState);
    return nextState;
  }, []);

  useEffect(() => {
    Promise.all([refresh(), refreshJobs()]).catch(() =>
      setStreamLines((lines) => [...lines, "初始状态读取失败，保留本地壳"]),
    );
  }, [refresh, refreshJobs]);

  useEffect(() => {
    const timer = window.setInterval(() => refreshJobs().catch(() => undefined), 3000);
    return () => window.clearInterval(timer);
  }, [refreshJobs]);

  useEffect(() => {
    if (!activeJobId) {
      setJobWorkspace(null);
      return;
    }
    refreshJobWorkspace(activeJobId).catch(() => setJobWorkspace(null));
    const timer = window.setInterval(() => refreshJobWorkspace(activeJobId).catch(() => undefined), 3000);
    return () => window.clearInterval(timer);
  }, [activeJobId, refreshJobWorkspace]);

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

  const openPaperById = (paperId: string, jobId = "") => {
    const normalized = paperId.trim();
    if (!/^[0-9a-f]{64}$/.test(normalized)) return;
    window.localStorage.setItem("peerassist.activePaperId", normalized);
    if (jobId) {
      window.localStorage.setItem("peerassist.activeJobId", jobId);
      setActiveJobId(jobId);
    }
    setActivePaperId(normalized);
    setActivePdfUrl(`/api/papers/${normalized}/source`);
    navigate("paper", "/paper");
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
      const response = await fetch(activeJobId ? `/api/jobs/${activeJobId}/decisions` : "/api/decision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          concern_id: concern.id,
          finding_lineage_id: concern.finding_lineage_id || "",
          finding_id: concern.finding_id || "",
          finding_revision: concern.revision || 1,
          confirmation_revision: state.confirmation_revision || 0,
          action,
          reviewer_id: "local-reviewer",
          timestamp: new Date().toISOString(),
          previous_text: concern.author_action || "",
          new_text: concern.author_action || "",
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || payload.result?.error_code || "确认失败");
      showToast(`已记录：${labelAction(action)}`);
      if (activeJobId) {
        setJobWorkspace(payload.state || null);
        await refreshJobs();
      } else {
        await refresh();
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : "确认失败");
    } finally {
      setBusy(false);
    }
  }

  async function runJobAction(job: ReviewJob, action: "cancel" | "retry" | "consent" | "finalize") {
    setBusy(true);
    try {
      const path =
        action === "consent"
          ? `/api/jobs/${job.id}/consents/model`
          : `/api/jobs/${job.id}/${action}`;
      const payload =
        action === "consent"
          ? { decision: "granted", actor: "local-reviewer" }
          : action === "finalize"
            ? { confirmation_revision: job.confirmation_revision || 0 }
            : {};
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "任务操作失败");
      showToast(jobActionLabel(action));
      await refreshJobs();
      if (action === "finalize" && result.job?.status === "completed") {
        window.localStorage.setItem("peerassist.activePaperId", job.paper_id);
        window.localStorage.setItem("peerassist.activeJobId", job.id);
        setActivePaperId(job.paper_id);
        setActiveJobId(job.id);
        await refreshJobWorkspace(job.id);
        navigate("artifacts", "/artifacts");
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : "任务操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function uploadPaper(file: File) {
    setBusy(true);
    setStreamLines((lines) => [...lines.slice(-80), `upload: 正在上传 ${file.name}`]);
    try {
      const body = new FormData();
      body.append("file", file, file.name);
      const response = await fetch("/api/papers/upload", { method: "POST", body });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "论文上传失败");
      showToast("论文已上传，后台审稿任务已启动");
      setStreamLines((lines) => [
        ...lines.slice(-80),
        `upload: 已创建任务 ${String(result.job?.id || "")}`,
      ]);
      await refreshJobs();
      openPaperById(String(result.paper?.paper_id || ""), String(result.job?.id || ""));
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "论文上传失败";
      showToast(message);
      setStreamLines((lines) => [...lines.slice(-80), `upload-error: ${message}`]);
      return false;
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
              title={item.label}
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
          <span>证据约束 · 人工确认</span>
        </div>
      </aside>

      <main className={`workspace ${activeWindow === "paper" ? "paper-workspace" : ""}`}>
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

        {activeWindow !== "paper" && <StatusStrip state={state} model={bootstrap.model_config.model || ""} />}

        {activeWindow === "paper" && (
          <PaperWindow
            pdfUrl={activePdfUrl}
            queueItems={queueItems}
            busy={busy}
            linkedReviewJob={Boolean(activePaperId)}
            readyForConfirmation={Boolean(state.ready_for_confirmation)}
            citationAudit={state.citation_audit}
            onRunReview={runAgentReview}
            onSubmitManual={submitManualConcern}
            onDecision={submitDecision}
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
            reviewJobs={reviewJobs}
            onRunReview={runAgentReview}
            onJobAction={runJobAction}
            onUploadPaper={uploadPaper}
            onOpenPaper={(job) => openPaperById(job.paper_id, job.id)}
          />
        )}
        {activeWindow === "queue" && (
          <QueueWindow items={queueItems} onDecision={submitDecision} onOpenConfirm={() => navigate("confirm", "/confirm")} />
        )}
        {activeWindow === "trace" && <TraceWindow events={events} streamLines={streamLines} />}
        {activeWindow === "confirm" && <ConfirmWindow items={queueItems} busy={busy} onDecision={submitDecision} />}
        {activeWindow === "artifacts" && <ArtifactsWindow paths={paths} reports={state.artifacts} />}
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
  linkedReviewJob,
  readyForConfirmation,
  citationAudit,
  onRunReview,
  onSubmitManual,
  onDecision,
}: {
  pdfUrl: string;
  queueItems: Concern[];
  busy: boolean;
  linkedReviewJob: boolean;
  readyForConfirmation: boolean;
  citationAudit?: CitationAuditSummary;
  onRunReview: (selectedText?: string, reviewMode?: string) => void;
  onSubmitManual: (selectedText: string, note: string, page: string) => void;
  onDecision: (concern: Concern, action: string) => void;
}) {
  const [selectedText, setSelectedText] = useState("");
  const [note, setNote] = useState("");
  const [page, setPage] = useState("1");
  const [currentPage, setCurrentPage] = useState(1);
  const [targetPage, setTargetPage] = useState(1);
  const [citationHighlight, setCitationHighlight] = useState<Evidence | null>(null);
  const [activeConcernId, setActiveConcernId] = useState("");
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [inspectorTab, setInspectorTab] = useState<"review" | "concerns" | "citations">("review");
  const [inspectorWidth, setInspectorWidth] = useState(372);
  const resizeState = useRef<{ startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    const handlePointerMove = (event: PointerEvent) => {
      if (!resizeState.current) return;
      const delta = resizeState.current.startX - event.clientX;
      setInspectorWidth(Math.min(520, Math.max(310, resizeState.current.startWidth + delta)));
    };
    const stopResize = () => {
      resizeState.current = null;
      document.body.classList.remove("is-resizing-panel");
    };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
    };
  }, []);

  const handlePdfSelection = useCallback((payload: { text: string; page: number }) => {
    setSelectedText(payload.text);
    setPage(String(payload.page));
    setInspectorOpen(true);
    setInspectorTab("review");
  }, []);

  const handlePdfPageChange = useCallback((nextPage: number) => {
    setCurrentPage(nextPage);
    setPage(String(nextPage));
    setActiveConcernId((currentId) => {
      if (!currentId) return "";
      const currentConcern = queueItems.find((item) => item.id === currentId);
      const remainsOnPage = (currentConcern?.evidence || []).some(
        (evidence) => Number(evidence.page || 0) === nextPage,
      );
      return remainsOnPage ? currentId : "";
    });
  }, [queueItems]);

  const pageConcerns = queueItems.filter((item) =>
    (item.evidence || []).some((evidence) => Number(evidence.page || 0) === currentPage),
  );
  const citationLinks = citationAudit?.links || [];
  const pageCitations = citationLinks.filter((item) => Number(item.mention?.page || 0) === currentPage);
  const concernAnnotations = useMemo(() => {
    const result: PdfConcernAnnotation[] = [];
    for (const concern of queueItems) {
      const evidenceByPage = new Map<number, Evidence>();
      for (const evidence of concern.evidence || []) {
        const evidencePage = Number(evidence.page || 0);
        if (evidencePage <= 0) continue;
        const current = evidenceByPage.get(evidencePage);
        if (!current || (!current.bbox?.length && evidence.bbox?.length)) evidenceByPage.set(evidencePage, evidence);
      }
      for (const [evidencePage, evidence] of evidenceByPage) {
        result.push({
          id: `${concern.id}:${evidencePage}`,
          concernId: concern.id,
          page: evidencePage,
          title: concern.title || concern.id,
          level: concern.level || "",
          status: concern.status || "",
          evidence,
        });
      }
    }
    return result;
  }, [queueItems]);

  const locateEvidence = useCallback((evidence?: Evidence) => {
    if (!evidence) return;
    const evidencePage = Number(evidence.page || 0);
    if (evidencePage > 0) setTargetPage(evidencePage);
    setCitationHighlight(evidence);
  }, []);

  const openConcernAnnotation = useCallback((annotation: PdfConcernAnnotation) => {
    setActiveConcernId(annotation.concernId);
    setInspectorOpen(true);
    setInspectorTab("concerns");
    locateEvidence(annotation.evidence);
  }, [locateEvidence]);

  const baseConcerns = pageConcerns.length ? pageConcerns : queueItems.slice(0, 5);
  const activeConcern = queueItems.find((item) => item.id === activeConcernId);
  const displayedConcerns = activeConcern
    ? [activeConcern, ...baseConcerns.filter((item) => item.id !== activeConcern.id)]
    : baseConcerns;

  return (
    <section
      className={`paper-grid ${inspectorOpen ? "" : "inspector-collapsed"}`}
      style={{ "--inspector-width": `${inspectorWidth}px` } as React.CSSProperties}
    >
      <div className="panel pdf-panel">
        {pdfUrl ? (
          <PdfReviewReader
            pdfUrl={pdfUrl}
            onSelection={handlePdfSelection}
            onPageChange={handlePdfPageChange}
            targetPage={targetPage}
            citationHighlight={citationHighlight}
            annotations={concernAnnotations}
            activeConcernId={activeConcernId}
            onAnnotationOpen={openConcernAnnotation}
          />
        ) : (
          <div className="empty-pdf">当前运行目录没有发现原始 PDF。</div>
        )}
      </div>
      {inspectorOpen ? (
        <aside className="paper-inspector">
          <div
            className="inspector-resizer"
            role="separator"
            aria-label="调整审稿侧栏宽度"
            aria-orientation="vertical"
            onPointerDown={(event) => {
              resizeState.current = { startX: event.clientX, startWidth: inspectorWidth };
              document.body.classList.add("is-resizing-panel");
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
          />
          <div className="inspector-head">
            <div>
              <p className="inspector-kicker">智能审稿助手</p>
              <strong>第 {currentPage} 页</strong>
            </div>
            <button className="icon-button" type="button" title="收起审稿侧栏" onClick={() => setInspectorOpen(false)}>
              <PanelRightClose size={17} />
            </button>
          </div>
          <div className="inspector-tabs" role="tablist" aria-label="审稿侧栏">
            <button type="button" role="tab" aria-selected={inspectorTab === "review"} onClick={() => setInspectorTab("review")}>
              审稿助手
            </button>
            <button type="button" role="tab" aria-selected={inspectorTab === "concerns"} onClick={() => setInspectorTab("concerns")}>
              本页关注 {pageConcerns.length}
            </button>
            <button type="button" role="tab" aria-selected={inspectorTab === "citations"} onClick={() => setInspectorTab("citations")}>
              引用核查 {citationLinks.length}
            </button>
          </div>
          {inspectorTab === "review" ? (
            <div className="inspector-body selection-box">
              {linkedReviewJob && (
                <div className="linked-job-banner">
                  <GitBranch size={17} />
                  <div><strong>已连接后台审稿任务</strong><span>{readyForConfirmation ? "候选意见已生成，等待逐条确认" : "本地解析与核查按持久化阶段运行"}</span></div>
                </div>
              )}
              <button className="primary-button wide" type="button" disabled={busy || linkedReviewJob} onClick={() => onRunReview("", "fast")}>
                {busy ? <Loader2 className="spin" size={16} /> : <Bot size={16} />} {linkedReviewJob ? "后台任务审稿中" : "快速审阅全文"}
              </button>
              <div className="selection-summary">
                <span>PDF 选区</span>
                <small>{selectedText ? `${selectedText.length} 字 · 第 ${page} 页` : "请在论文中拖选文字"}</small>
              </div>
              <textarea
                className="selection-text"
                value={selectedText}
                onChange={(event) => setSelectedText(event.target.value)}
                placeholder="选中的论文原文会自动出现在这里"
              />
              <button className="ghost-button wide" type="button" disabled={busy || linkedReviewJob || !selectedText.trim()} onClick={() => onRunReview(selectedText, "fast")}>
                <Send size={15} /> 基于选区智能审稿
              </button>
              <div className="selection-summary">
                <span>人工批注</span>
                <small>写入证据队列后逐条确认</small>
              </div>
              <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="例如：请作者解释统计显著性阈值与多重比较校正。" />
              <div className="field-row">
                <input value={page} onChange={(event) => setPage(event.target.value)} aria-label="PDF 页码" inputMode="numeric" />
                <button className="primary-button" type="button" disabled={busy || linkedReviewJob || (!selectedText.trim() && !note.trim())} onClick={() => onSubmitManual(selectedText, note, page)}>
                  <ClipboardCheck size={15} /> 加入证据队列
                </button>
              </div>
            </div>
          ) : inspectorTab === "concerns" ? (
            <div className="inspector-body concern-list">
              {displayedConcerns.map((item) => (
                <ConcernCard
                  concern={item}
                  compact
                  selected={item.id === activeConcernId}
                  disabled={busy}
                  onDecision={readyForConfirmation && item.status === "pending_human_confirmation" ? onDecision : undefined}
                  onLocate={(evidence) => {
                    setActiveConcernId(item.id);
                    locateEvidence(evidence);
                  }}
                  key={item.id}
                />
              ))}
              {!queueItems.length && <p className="muted">当前还没有审稿关注点。</p>}
            </div>
          ) : (
            <CitationAuditPanel
              audit={citationAudit}
              links={pageCitations.length ? pageCitations : citationLinks}
              concerns={queueItems}
              readyForConfirmation={readyForConfirmation}
              busy={busy}
              onLocate={locateEvidence}
              onDecision={onDecision}
            />
          )}
        </aside>
      ) : (
        <button className="inspector-reopen" type="button" title="打开审稿侧栏" onClick={() => setInspectorOpen(true)}>
          <PanelRightOpen size={18} />
        </button>
      )}
    </section>
  );
}

type PdfViewMode = "single" | "spread";

function PdfReviewReader({
  pdfUrl,
  onSelection,
  onPageChange,
  targetPage,
  citationHighlight,
  annotations,
  activeConcernId,
  onAnnotationOpen,
}: {
  pdfUrl: string;
  onSelection: (payload: { text: string; page: number }) => void;
  onPageChange: (page: number) => void;
  targetPage?: number;
  citationHighlight?: Evidence | null;
  annotations: PdfConcernAnnotation[];
  activeConcernId: string;
  onAnnotationOpen: (annotation: PdfConcernAnnotation) => void;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [pdfDoc, setPdfDoc] = useState<PdfDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1.18);
  const [renderScale, setRenderScale] = useState(1);
  const [fitWidth, setFitWidth] = useState(true);
  const [viewportWidth, setViewportWidth] = useState(0);
  const [status, setStatus] = useState("正在加载 PDF");
  const [loadProgress, setLoadProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [retryToken, setRetryToken] = useState(0);
  const [pageInput, setPageInput] = useState("1");
  const [viewMode, setViewMode] = useState<PdfViewMode>(() =>
    window.localStorage.getItem("peerassist.pdfViewMode") === "spread" ? "spread" : "single",
  );

  const effectiveViewMode: PdfViewMode = viewMode === "spread" && viewportWidth >= 860 ? "spread" : "single";
  const spreadStart = pageNumber <= 1 ? 1 : pageNumber % 2 === 0 ? pageNumber : pageNumber - 1;
  const visiblePages = useMemo(() => {
    if (!pageCount) return [];
    if (effectiveViewMode === "single" || spreadStart === 1) return [effectiveViewMode === "single" ? pageNumber : 1];
    return [spreadStart, spreadStart + 1].filter((value) => value <= pageCount);
  }, [effectiveViewMode, pageCount, pageNumber, spreadStart]);
  const pageAvailableWidth = Math.max(
    260,
    effectiveViewMode === "spread"
      ? ((viewportWidth || 1100) - 66) / 2
      : (viewportWidth || 900) - 48,
  );

  useEffect(() => {
    if (!scrollRef.current || !window.ResizeObserver) return;
    const observer = new ResizeObserver(([entry]) => setViewportWidth(Math.round(entry.contentRect.width)));
    observer.observe(scrollRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setStatus("正在加载 PDF");
    setError("");
    setLoadProgress(null);
    const loadingTask = pdfjsLib.getDocument({ url: pdfUrl, rangeChunkSize: 64 * 1024 });
    loadingTask.onProgress = ({ loaded, total }: { loaded: number; total: number }) => {
      if (!cancelled && total > 0) setLoadProgress(Math.min(100, Math.round((loaded / total) * 100)));
    };
    loadingTask.promise
      .then((document) => {
        if (cancelled) return;
        setPdfDoc(document);
        setPageCount(document.numPages);
        setPageNumber(1);
        setPageInput("1");
        setLoadProgress(100);
        setStatus(`已载入 ${document.numPages} 页`);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : "未知错误";
        setError(message);
        setStatus("PDF 加载失败");
      });
    return () => {
      cancelled = true;
      loadingTask.destroy();
    };
  }, [pdfUrl, retryToken]);

  useEffect(() => {
    if (!pdfDoc || !visiblePages.length) return;
    const before = Math.min(...visiblePages) - 1;
    const after = Math.max(...visiblePages) + 1;
    const adjacentPages = [before, after].filter((value) => value >= 1 && value <= pdfDoc.numPages);
    void Promise.allSettled(
      adjacentPages.map((value) => pdfDoc.getPage(value).then((nextPage) => nextPage.getOperatorList())),
    );
  }, [pdfDoc, visiblePages]);

  const goToPage = (nextPage: number) => {
    const boundedPage = Math.max(1, Math.min(pageCount || 1, nextPage));
    setPageNumber(boundedPage);
    setPageInput(String(boundedPage));
  };

  const commitPageInput = () => goToPage(Number(pageInput) || pageNumber);

  const navigatePages = (direction: -1 | 1) => {
    if (effectiveViewMode === "single") {
      goToPage(pageNumber + direction);
      return;
    }
    if (direction < 0) {
      goToPage(spreadStart <= 2 ? 1 : spreadStart - 2);
      return;
    }
    goToPage(spreadStart === 1 ? 2 : spreadStart + 2);
  };

  const canGoPrevious = effectiveViewMode === "single" ? pageNumber > 1 : spreadStart > 1;
  const canGoNext = effectiveViewMode === "single"
    ? pageNumber < pageCount
    : spreadStart === 1
      ? pageCount > 1
      : spreadStart + 1 < pageCount;

  const selectViewMode = (nextMode: PdfViewMode) => {
    setViewMode(nextMode);
    window.localStorage.setItem("peerassist.pdfViewMode", nextMode);
  };

  useEffect(() => {
    if (!targetPage || !pageCount) return;
    const boundedPage = Math.max(1, Math.min(pageCount, targetPage));
    setPageNumber(boundedPage);
    setPageInput(String(boundedPage));
  }, [pageCount, targetPage]);

  const handlePageRendered = useCallback((renderedPage: number, pageScale: number) => {
    if (renderedPage === pageNumber || visiblePages.length === 1) setRenderScale(pageScale);
    const pageLabel = visiblePages.length > 1
      ? `${visiblePages[0]}–${visiblePages[visiblePages.length - 1]}`
      : String(visiblePages[0] || pageNumber);
    setStatus(`第 ${pageLabel} / ${pdfDoc?.numPages || pageCount} 页 · ${Math.round(pageScale * 100)}%`);
    if (renderedPage === pageNumber || visiblePages.length === 1) onPageChange(pageNumber);
  }, [onPageChange, pageCount, pageNumber, pdfDoc, visiblePages]);

  const handlePageError = useCallback((message: string) => {
    setError(message);
    setStatus("PDF 渲染失败");
  }, []);

  return (
    <div className="pdf-reader">
      <div className="pdf-toolbar">
        <div className="pdf-toolbar-group">
          <button className="icon-button" title="上一页" type="button" disabled={!canGoPrevious} onClick={() => navigatePages(-1)}>
            <ChevronLeft size={18} />
          </button>
          <label className="page-jump">
            <input
              value={pageInput}
              onChange={(event) => setPageInput(event.target.value.replace(/\D/g, ""))}
              onBlur={commitPageInput}
              onKeyDown={(event) => event.key === "Enter" && commitPageInput()}
              aria-label="当前 PDF 页码"
              inputMode="numeric"
            />
            <span>/ {pageCount || "--"}</span>
          </label>
          <button className="icon-button" title="下一页" type="button" disabled={!canGoNext} onClick={() => navigatePages(1)}>
            <ChevronRight size={18} />
          </button>
        </div>
        <span className="pdf-status" aria-live="polite">{status}</span>
        <div className="pdf-toolbar-group">
          <button className={`icon-button view-mode-control ${effectiveViewMode === "single" ? "active" : ""}`} title="单页阅读" type="button" aria-pressed={effectiveViewMode === "single"} onClick={() => selectViewMode("single")}>
            <Square size={15} />
          </button>
          <button className={`icon-button view-mode-control ${effectiveViewMode === "spread" ? "active" : ""}`} title="双页阅读" type="button" aria-pressed={effectiveViewMode === "spread"} disabled={viewportWidth > 0 && viewportWidth < 860} onClick={() => selectViewMode("spread")}>
            <Columns2 size={17} />
          </button>
          <span className="toolbar-divider" aria-hidden="true" />
          <button className="icon-button" title="缩小" type="button" onClick={() => { setFitWidth(false); setScale(Math.max(0.62, renderScale - 0.12)); }}>
            <Minus size={17} />
          </button>
          <button className={`icon-button ${fitWidth ? "active" : ""}`} title="适应宽度" type="button" onClick={() => setFitWidth(true)}>
            <Maximize2 size={16} />
          </button>
          <button className="icon-button" title="放大" type="button" onClick={() => { setFitWidth(false); setScale(Math.min(2.4, renderScale + 0.12)); }}>
            <Plus size={17} />
          </button>
          <a className="icon-button" title="下载原始 PDF" href={pdfUrl} download><Download size={16} /></a>
          <a className="icon-button" title="在新窗口打开" href={pdfUrl} target="_blank" rel="noreferrer"><ExternalLink size={16} /></a>
        </div>
      </div>
      <div className="pdf-scroll" ref={scrollRef}>
        <div className="pdf-page-grid" data-view-mode={effectiveViewMode} data-page-count={visiblePages.length}>
          {pdfDoc && visiblePages.map((visiblePage) => (
            <PdfPageView
              key={visiblePage}
              pdfDoc={pdfDoc}
              pageNumber={visiblePage}
              availableWidth={pageAvailableWidth}
              fitWidth={fitWidth}
              scale={scale}
              highlight={citationHighlight?.page === visiblePage ? citationHighlight : null}
              annotations={annotations.filter((annotation) => annotation.page === visiblePage)}
              activeConcernId={activeConcernId}
              onSelection={onSelection}
              onAnnotationOpen={onAnnotationOpen}
              onRendered={handlePageRendered}
              onError={handlePageError}
            />
          ))}
        </div>
        {(!pdfDoc || error) && (
          <div className={`pdf-loading-state ${error ? "error" : ""}`}>
            {error ? <FileText size={28} /> : <Loader2 className="spin" size={28} />}
            <strong>{error ? "PDF 暂时无法显示" : "正在准备论文"}</strong>
            <span>{error || (loadProgress === null ? "正在连接文档服务" : `已加载 ${loadProgress}%`)}</span>
            {!error && loadProgress !== null && <div className="loading-track"><span style={{ width: `${loadProgress}%` }} /></div>}
            {error && <button className="ghost-button" type="button" onClick={() => setRetryToken((value) => value + 1)}>重新加载</button>}
          </div>
        )}
      </div>
    </div>
  );
}

function PdfPageView({
  pdfDoc,
  pageNumber,
  availableWidth,
  fitWidth,
  scale,
  highlight,
  annotations,
  activeConcernId,
  onSelection,
  onAnnotationOpen,
  onRendered,
  onError,
}: {
  pdfDoc: PdfDocumentProxy;
  pageNumber: number;
  availableWidth: number;
  fitWidth: boolean;
  scale: number;
  highlight?: Evidence | null;
  annotations: PdfConcernAnnotation[];
  activeConcernId: string;
  onSelection: (payload: { text: string; page: number }) => void;
  onAnnotationOpen: (annotation: PdfConcernAnnotation) => void;
  onRendered: (page: number, scale: number) => void;
  onError: (message: string) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const [pageScale, setPageScale] = useState(1);
  const [pageHeight, setPageHeight] = useState(0);
  const annotationPlacements = useMemo(() => {
    let nextTop = 12;
    const maxTop = Math.max(12, pageHeight - 30);
    return [...annotations]
      .sort((left, right) => Number(left.evidence.bbox?.[1] || 0) - Number(right.evidence.bbox?.[1] || 0))
      .map((annotation, index) => {
        const rawTop = annotation.evidence.bbox?.length === 4
          ? Number(annotation.evidence.bbox[1]) * pageScale
          : 18 + index * 30;
        const top = Math.min(maxTop, Math.max(nextTop, rawTop));
        nextTop = top + 28;
        return { annotation, top };
      });
  }, [annotations, pageHeight, pageScale]);

  useEffect(() => {
    if (!canvasRef.current || !textLayerRef.current) return;
    let cancelled = false;
    let renderTask: ReturnType<Awaited<ReturnType<typeof pdfDoc.getPage>>["render"]> | null = null;
    let textLayerTask: { cancel: () => void; render: () => Promise<void> } | null = null;
    const canvas = canvasRef.current;
    const textLayer = textLayerRef.current;
    const context = canvas.getContext("2d");
    if (!context) return;
    textLayer.replaceChildren();

    pdfDoc
      .getPage(pageNumber)
      .then(async (page) => {
        if (cancelled) return;
        const naturalViewport = page.getViewport({ scale: 1 });
        const targetScale = fitWidth
          ? Math.min(2.2, Math.max(0.5, availableWidth / naturalViewport.width))
          : scale;
        const viewport = page.getViewport({ scale: targetScale });
        const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
        setPageScale(targetScale);
        setPageHeight(viewport.height);
        canvas.width = Math.floor(viewport.width * pixelRatio);
        canvas.height = Math.floor(viewport.height * pixelRatio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        textLayer.style.width = `${viewport.width}px`;
        textLayer.style.height = `${viewport.height}px`;
        context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
        renderTask = page.render({ canvas, canvasContext: context, viewport });
        await renderTask.promise;
        const textContent = await page.getTextContent();
        if (cancelled) return;
        textLayer.replaceChildren();
        textLayerTask = new pdfjsLib.TextLayer({ textContentSource: textContent, container: textLayer, viewport });
        await textLayerTask.render();
        const highlightText = highlight?.text?.trim() || "";
        if (highlightText) {
          for (const node of textLayer.querySelectorAll("span")) {
            if ((node.textContent || "").includes(highlightText)) node.classList.add("citation-highlight-text");
          }
        }
        onRendered(pageNumber, targetScale);
      })
      .catch((error: unknown) => {
        if (cancelled || (error instanceof Error && error.name === "RenderingCancelledException")) return;
        onError(error instanceof Error ? error.message : "未知错误");
      });
    return () => {
      cancelled = true;
      renderTask?.cancel();
      textLayerTask?.cancel();
    };
  }, [availableWidth, fitWidth, highlight, onError, onRendered, pageNumber, pdfDoc, scale]);

  const captureSelection = () => {
    const selection = window.getSelection();
    const text = selection?.toString().replace(/\s+/g, " ").trim() || "";
    if (!text || !textLayerRef.current || !selection?.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (!textLayerRef.current.contains(range.commonAncestorContainer)) return;
    onSelection({ text, page: pageNumber });
  };

  return (
    <div className="pdf-page-shell" aria-label={`PDF 第 ${pageNumber} 页`} onMouseUp={captureSelection}>
      <canvas ref={canvasRef} className="pdf-canvas" />
      {highlight?.bbox?.length === 4 && (
        <div
          className="citation-highlight-box"
          aria-label="当前证据定位"
          style={{
            left: `${highlight.bbox[0] * pageScale}px`,
            top: `${highlight.bbox[1] * pageScale}px`,
            width: `${(highlight.bbox[2] - highlight.bbox[0]) * pageScale}px`,
            height: `${(highlight.bbox[3] - highlight.bbox[1]) * pageScale}px`,
          }}
        />
      )}
      {annotationPlacements.map(({ annotation, top }) => (
        <button
          className={`pdf-annotation-pin ${annotation.level === "major_concern" ? "danger" : annotation.status === "confirmed" ? "good" : "warn"} ${annotation.concernId === activeConcernId ? "active" : ""}`}
          key={annotation.id}
          type="button"
          title={`打开关注：${annotation.title}`}
          aria-label={`打开关注：${annotation.title}`}
          style={{ top: `${top}px` }}
          onClick={(event) => {
            event.stopPropagation();
            onAnnotationOpen(annotation);
          }}
        >
          <MessageSquareText size={13} />
        </button>
      ))}
      <div ref={textLayerRef} className="pdf-text-layer" />
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
  reviewJobs,
  onRunReview,
  onJobAction,
  onUploadPaper,
  onOpenPaper,
}: {
  busy: boolean;
  model: string;
  baseUrl: string;
  agentRuns: AgentRun[];
  streamLines: string[];
  lastReviewDraft: string;
  reviewJobs: ReviewJob[];
  onRunReview: (selectedText?: string, reviewMode?: string) => void;
  onJobAction: (job: ReviewJob, action: "cancel" | "retry" | "consent" | "finalize") => void;
  onUploadPaper: (file: File) => Promise<boolean>;
  onOpenPaper: (job: ReviewJob) => void;
}) {
  const [mode, setMode] = useState("fast");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const uploadInput = useRef<HTMLInputElement>(null);

  const submitUpload = async () => {
    if (!uploadFile) return;
    if (await onUploadPaper(uploadFile)) {
      setUploadFile(null);
      if (uploadInput.current) uploadInput.current.value = "";
    }
  };

  return (
    <section className="agent-grid">
      <div className="panel full-span">
        <div className="panel-head">
          <h2>后台审稿任务</h2>
          <span className="tag">{reviewJobs.length} 个 Review Job</span>
        </div>
        <div className="panel-body">
          <div className="upload-bar">
            <div className="upload-copy">
              <FileText size={20} />
              <div>
                <strong>上传真实论文</strong>
                <span>上传 PDF 后自动解析全文、建立证据索引并启动审稿任务</span>
              </div>
            </div>
            <div className="upload-actions">
              <input
                ref={uploadInput}
                type="file"
                accept="application/pdf,.pdf"
                hidden
                onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
              />
              <button className="ghost-button upload-picker" type="button" disabled={busy} onClick={() => uploadInput.current?.click()}>
                <FileUp size={16} /> <span>{uploadFile?.name || "选择 PDF"}</span>
              </button>
              <button className="primary-button" type="button" disabled={busy || !uploadFile} onClick={submitUpload}>
                {busy ? <Loader2 className="spin" size={16} /> : <Bot size={16} />}
                {busy ? "正在处理" : "上传并开始审稿"}
              </button>
            </div>
          </div>
          <div className="job-list">
            {reviewJobs.length ? reviewJobs.map((job) => (
              <ReviewJobCard job={job} busy={busy} onAction={onJobAction} onOpenPaper={onOpenPaper} key={job.id} />
            )) : <div className="draft-empty">暂无持久化审稿任务，请先上传一篇 PDF。</div>}
          </div>
        </div>
      </div>
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

const reviewStages = ["validate", "parse", "evidence", "profile", "plan", "deterministic", "citation", "agents", "integrate", "await_confirmation", "finalize", "complete"];

function ReviewJobCard({ job, busy, onAction, onOpenPaper }: { job: ReviewJob; busy: boolean; onAction: (job: ReviewJob, action: "cancel" | "retry" | "consent" | "finalize") => void; onOpenPaper: (job: ReviewJob) => void }) {
  const current = Math.max(0, reviewStages.indexOf(job.stage));
  const canCancel = !["completed", "cancelled", "cancel_requested", "failed", "awaiting_human_confirmation"].includes(job.status);
  const timelineItems = job.timeline?.items || [];
  const latestEvent = timelineItems[timelineItems.length - 1];
  return (
    <article className="job-card">
      <div className="job-card-head">
        <div>
          <span className={`tag ${job.status === "failed" ? "danger" : job.status === "blocked" ? "warn" : "good"}`}>{labelJobStatus(job.status)}</span>
          <h3>{job.paper_id.slice(0, 16)}</h3>
          <code>{job.id}</code>
        </div>
        <div className="job-actions">
          <button className="ghost-button" type="button" onClick={() => onOpenPaper(job)}><FileText size={15} /> 阅读论文</button>
          {job.status === "blocked" && job.required_consents?.includes("model") && <button className="primary-button" disabled={busy} type="button" onClick={() => onAction(job, "consent")}>授权模型并继续</button>}
          {canCancel && <button className="ghost-button" disabled={busy} type="button" onClick={() => onAction(job, "cancel")}>取消</button>}
          {["failed", "cancelled", "interrupted"].includes(job.status) && <button className="ghost-button" disabled={busy} type="button" onClick={() => onAction(job, "retry")}>重试</button>}
          {job.status === "awaiting_human_confirmation" && <button className="primary-button" disabled={busy} type="button" onClick={() => onAction(job, "finalize")}>导出最终报告</button>}
        </div>
      </div>
      <div className="job-stage-track" aria-label="审稿任务阶段">
        {reviewStages.map((stage, index) => <span key={stage} data-state={index < current ? "done" : index === current ? "active" : "pending"} title={labelJobStage(stage)} />)}
      </div>
      <div className="job-meta">
        <span>当前阶段：{labelJobStage(job.stage)}</span>
        <span>状态版本：{job.revision}</span>
        {latestEvent && <span>最新：{timelineEventLabel(latestEvent)}</span>}
        {job.degradation_code && <span>本地降级：{(job.degraded_services || []).join("、")}</span>}
        {job.error && <span className="job-error">{job.error}</span>}
      </div>
      {timelineItems.length > 0 && (
        <details className="job-timeline">
          <summary>
            <History size={15} />
            <span>运行时间线</span>
            <small>{job.timeline?.event_count || timelineItems.length} 条持久事件</small>
          </summary>
          <div className="job-timeline-list">
            {timelineItems.slice(-8).map((event) => (
              <div className="job-timeline-item" data-tone={timelineEventTone(event)} key={event.event_id}>
                <span className="job-timeline-dot" />
                <div>
                  <strong>{timelineEventLabel(event)}</strong>
                  <small>
                    {formatTimelineTime(event.timestamp)}
                    {event.attempt > 1 ? ` · 第 ${event.attempt} 次尝试` : ""}
                    {event.duration_ms !== null && event.duration_ms !== undefined ? ` · ${formatDuration(event.duration_ms)}` : ""}
                  </small>
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
    </article>
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

function ArtifactsWindow({ paths, reports }: { paths: Record<string, string>; reports?: ReportArtifacts }) {
  const reportItems = reports?.items || [];
  const entries = Object.entries(paths);
  const count = reportItems.length || entries.length;
  return (
    <section className="artifact-grid">
      <div className="panel">
        <div className="panel-head">
          <h2>产物导出</h2>
          <span className={`tag ${reports?.ready ? "good" : ""}`}>{count} 个产物</span>
        </div>
        <div className="panel-body artifact-list">
          {reportItems.map((item) => (
            <div className="artifact-card" key={item.name}>
              <div className="tag-row">
                <span className="tag good">{artifactLabel(item.name)}</span>
                <span className="tag">{formatBytes(item.size_bytes)}</span>
              </div>
              <h3>{item.filename}</h3>
              <code>SHA-256 {item.sha256.slice(0, 20)}...</code>
              <div className="artifact-actions">
                <a className="ghost-button" href={`${item.download_url}?disposition=inline`} target="_blank" rel="noreferrer"><ExternalLink size={15} /> 在线查看</a>
                <a className="primary-button" href={item.download_url} download><Download size={15} /> 下载</a>
              </div>
            </div>
          ))}
          {!reportItems.length && entries.map(([key, value]) => (
            <div className="artifact-card" key={key}>
              <div className="tag-row">
                <span className="tag">{artifactLabel(key)}</span>
              </div>
              <h3>{key}</h3>
              <code>{value}</code>
            </div>
          ))}
          {!count && <div className="draft-empty">当前任务尚未导出最终报告。</div>}
        </div>
      </div>
      <aside className="panel">
        <div className="panel-head">
          <h2>导出说明</h2>
        </div>
        <div className="panel-body">
          {reports?.ready ? (
            <div className="timeline">
              <div><strong>报告版本</strong><p className="muted">{reports.report_version || "--"}</p></div>
              <div><strong>确认版本</strong><p className="muted">revision {reports.confirmation_revision || 0}</p></div>
              <div><strong>完整性</strong><p className="muted">下载时按 manifest SHA-256 校验。</p></div>
            </div>
          ) : <p className="muted">完成逐条确认后，从后台任务卡导出最终报告。</p>}
        </div>
      </aside>
    </section>
  );
}

function CitationAuditPanel({
  audit,
  links,
  concerns,
  readyForConfirmation,
  busy,
  onLocate,
  onDecision,
}: {
  audit?: CitationAuditSummary;
  links: CitationLinkSummary[];
  concerns: Concern[];
  readyForConfirmation: boolean;
  busy: boolean;
  onLocate: (evidence?: Evidence) => void;
  onDecision: (concern: Concern, action: string) => void;
}) {
  const coverage = audit?.coverage || {};
  const metrics = [
    ["参考文献", coverage.records ?? audit?.records?.length ?? 0],
    ["正文引用", coverage.links ?? audit?.links?.length ?? 0],
    ["外部核验", coverage.verifications ?? 0],
    ["待核问题", coverage.findings ?? 0],
  ] as const;

  if (!audit?.available) {
    return (
      <div className="inspector-body citation-audit-panel">
        <div className="citation-empty">
          <Search size={22} />
          <strong>引用核查结果尚未生成</strong>
          <span>后台任务运行到“引用核查”阶段后，这里会显示正文引用与参考文献的对应关系。</span>
        </div>
      </div>
    );
  }

  return (
    <div className="inspector-body citation-audit-panel">
      <div className="citation-metrics" aria-label="引用核查覆盖统计">
        {metrics.map(([label, value]) => (
          <div key={label}>
            <strong>{value}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>

      {(audit.warnings || []).map((warning) => (
        <div className="citation-warning" key={warning}>
          <strong>解析提示</strong>
          <span>{citationWarningLabel(warning)}</span>
        </div>
      ))}

      {links.map((link) => {
        const concern = concerns.find((item) => item.id === link.concern_id);
        const verification = link.verification;
        return (
          <article className="citation-card" key={link.id}>
            <div className="citation-card-head">
              <div>
                <span className="citation-number">[{link.reference_number}]</span>
                <strong>{link.mention.text || `正文引用 ${link.reference_number}`}</strong>
              </div>
              <span className={`tag ${citationStatusTone(link.status)}`}>{citationStatusLabel(link.status)}</span>
            </div>

            <button className="citation-mention" type="button" disabled={!link.mention.page} onClick={() => onLocate(link.mention)}>
              <span>定位正文</span>
              <strong>{link.mention.locator || `第 ${link.mention.page || "?"} 页`}</strong>
            </button>

            {link.references.map((reference) => (
              <div className="citation-reference" key={reference.id}>
                <div className="citation-section-label">参考文献</div>
                <strong>{reference.title || reference.raw_text || "题名未解析"}</strong>
                {reference.title && reference.raw_text && <p>{reference.raw_text}</p>}
                <div className="citation-metadata">
                  {reference.year && <span>{reference.year}</span>}
                  {reference.doi && <code>DOI {reference.doi}</code>}
                </div>
                {(reference.evidence || []).map((evidence) => (
                  <button className="evidence-link" type="button" disabled={!evidence.page} onClick={() => onLocate(evidence)} key={evidence.id || evidence.locator}>
                    查看参考文献原文 · {evidence.locator || `第 ${evidence.page} 页`}
                  </button>
                ))}
              </div>
            ))}

            {verification ? (
              <div className="citation-verification">
                <div className="citation-verification-head">
                  <div>
                    <span className="citation-section-label">外部核验</span>
                    <strong>{citationVerificationLabel(verification.source)}</strong>
                  </div>
                  <span className={`tag ${citationStatusTone(verification.status)}`}>{citationStatusLabel(verification.status)}</span>
                </div>
                {verification.source_url && (
                  <a href={verification.source_url} target="_blank" rel="noreferrer">
                    打开核验来源 <ExternalLink size={13} />
                  </a>
                )}
                {verification.field_differences.length > 0 && (
                  <div className="citation-differences">
                    {verification.field_differences.map((difference, index) => (
                      <div key={`${difference.field}-${index}`}>
                        <strong>{citationFieldLabel(difference.field)}不一致</strong>
                        <dl>
                          <dt>论文</dt><dd>{difference.manuscript_value || "空"}</dd>
                          <dt>来源</dt><dd>{difference.external_value || "空"}</dd>
                        </dl>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="citation-verification is-empty">尚无外部核验记录</div>
            )}

            {link.findings.map((finding) => (
              <div className="citation-finding" key={finding.id}>
                <div className="tag-row">
                  <span className={`tag ${citationStatusTone(finding.status)}`}>{citationStatusLabel(finding.status)}</span>
                  <span className="tag">{labelLevel(finding.severity)}</span>
                </div>
                <p>{finding.message}</p>
              </div>
            ))}

            {concern && (
              <div className="citation-human-action">
                <div>
                  <span className="citation-section-label">人工结论</span>
                  <strong>{labelStatus(concern.status || link.concern_status || "")}</strong>
                </div>
                {readyForConfirmation && concern.status === "pending_human_confirmation" && (
                  <div className="button-row">
                    {["confirm", "rewrite", "downgrade", "delete"].map((action) => (
                      <button className={action === "delete" ? "danger-button" : "ghost-button"} disabled={busy} key={action} type="button" onClick={() => onDecision(concern, action)}>
                        {labelAction(action)}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </article>
        );
      })}

      {!links.length && (
        <div className="citation-empty">
          <Search size={22} />
          <strong>没有可展示的引用链接</strong>
          <span>{audit.records?.length ? "已解析参考文献，但没有建立正文引用链接。" : "当前解析器未识别出可核验的数字编号引用。"}</span>
        </div>
      )}
    </div>
  );
}

function ConcernCard({
  concern,
  compact,
  selected,
  disabled,
  onDecision,
  onLocate,
}: {
  concern: Concern;
  compact?: boolean;
  selected?: boolean;
  disabled?: boolean;
  onDecision?: (concern: Concern, action: string) => void;
  onLocate?: (evidence: Evidence) => void;
}) {
  const evidence = concern.evidence || [];
  return (
    <article className={`concern-card ${selected ? "selected" : ""}`} aria-current={selected ? "true" : undefined}>
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
          <button className="evidence-link" type="button" disabled={!onLocate || !item.page} onClick={() => onLocate?.(item)} key={item.id || item.locator}>
            {item.id || "证据"} · {item.locator || "无定位"}
          </button>
        ))}
      </div>
      {onDecision && concern.status === "pending_human_confirmation" && (
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
  return ({
    pending_human_confirmation: "待人工确认",
    confirmed: "已确认",
    downgraded: "已降级",
    rewritten: "已改写",
    deleted: "已删除",
  } as Record<string, string>)[value] || value || "未知状态";
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

function citationStatusLabel(value: string) {
  return ({
    linked: "已关联",
    missing_reference: "缺少参考文献",
    ambiguous: "匹配不唯一",
    completed: "核验完成",
    not_found: "外部未检索到",
    unavailable: "核验源不可用",
    failed: "核验失败",
    verified: "核验通过",
    metadata_mismatch: "字段不一致",
    insufficient_evidence: "证据不足",
    verification_failed: "核验失败",
    uncited_reference: "正文未引用",
    malformed_reference: "格式异常",
    duplicate_reference_metadata: "疑似重复",
  } as Record<string, string>)[value] || value || "未知状态";
}

function citationStatusTone(value: string) {
  if (["linked", "completed", "verified"].includes(value)) return "good";
  if (["missing_reference", "not_found", "failed", "verification_failed"].includes(value)) return "danger";
  return "warn";
}

function citationVerificationLabel(value: string) {
  return ({
    crossref: "Crossref 元数据",
    openalex: "OpenAlex 文献记录",
    pubmed: "PubMed 文献记录",
    semantic_scholar: "Semantic Scholar",
  } as Record<string, string>)[value] || value || "外部文献源";
}

function citationFieldLabel(value: string) {
  return ({
    title: "题名",
    author: "作者",
    authors: "作者",
    year: "年份",
    doi: "DOI",
    venue: "期刊或会议",
    volume: "卷号",
    issue: "期号",
    pages: "页码",
  } as Record<string, string>)[value] || value;
}

function citationWarningLabel(value: string) {
  if (value.startsWith("unsupported_author_year_citation_marker")) return "检测到作者-年份制引用，当前版本暂未建立自动链接，请人工检查正文与参考文献。";
  if (value.startsWith("unsupported_citation_marker")) return "检测到当前解析器暂不支持的引用格式，请人工检查。";
  if (value.startsWith("duplicate_doi:")) return `检测到重复 DOI：${value.slice("duplicate_doi:".length)}`;
  return value.replace(/_/g, " ");
}

function labelMode(value: string) {
  return ({ fast: "快速", standard: "标准", deep: "深入" } as Record<string, string>)[value] || value;
}

function labelJobStatus(value: string) {
  return ({
    queued: "等待调度",
    validating_input: "校验论文",
    parsing: "解析 PDF",
    evidence_building: "构建证据",
    profiling: "理解论文",
    planning_review: "规划审稿",
    deterministic_checking: "确定性核查",
    citation_checking: "引用核查",
    agents_running: "智能体审稿",
    integrating: "整合意见",
    blocked: "等待授权",
    awaiting_human_confirmation: "等待人工确认",
    exporting_report: "导出报告",
    completed: "已完成",
    failed: "失败",
    cancel_requested: "正在取消",
    cancelled: "已取消",
    interrupted: "等待恢复",
  } as Record<string, string>)[value] || value;
}

function labelJobStage(value: string) {
  return ({
    validate: "文件校验",
    parse: "文档解析",
    evidence: "证据台账",
    profile: "论文画像",
    plan: "审稿计划",
    deterministic: "确定性核查",
    citation: "引用核查",
    agents: "智能体评审",
    integrate: "意见整合",
    await_confirmation: "人工确认",
    finalize: "报告导出",
    complete: "完成",
  } as Record<string, string>)[value] || value;
}

function timelineEventLabel(event: ReviewJobTimelineEvent) {
  const stage = event.stage ? labelJobStage(event.stage) : "任务";
  return ({
    stage_started: `开始${stage}`,
    stage_completed: `完成${stage}`,
    stage_failed: `${stage}失败`,
    consent_required: "等待模型授权",
    consent_granted: "模型授权已记录",
    consent_denied_degraded: "模型已拒绝，切换本地降级",
    job_retried: "任务已重新排队",
    cancel_requested: "已请求取消任务",
    job_cancelled: "任务已取消",
    report_export_started: "开始导出最终报告",
    report_export_completed: "最终报告导出完成",
    report_export_blocked: "最终报告导出受阻",
    report_export_failed: "最终报告导出失败",
    event_tail_recovered: "事件日志尾部已恢复",
  } as Record<string, string>)[event.event_type] || event.event_type;
}

function timelineEventTone(event: ReviewJobTimelineEvent) {
  if (["stage_failed", "report_export_failed"].includes(event.event_type)) return "danger";
  if (["consent_required", "cancel_requested", "report_export_blocked"].includes(event.event_type)) return "warn";
  if (event.event_type === "job_cancelled") return "danger";
  if (["stage_completed", "consent_granted", "report_export_completed"].includes(event.event_type)) return "good";
  return "active";
}

function formatTimelineTime(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDuration(value: number) {
  if (value < 1000) return `${value} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} 秒`;
  return `${Math.floor(value / 60_000)} 分 ${Math.round((value % 60_000) / 1000)} 秒`;
}

function jobActionLabel(value: string) {
  return ({
    cancel: "已请求取消任务",
    retry: "任务已重新排队",
    consent: "模型授权已记录",
    finalize: "最终报告已导出",
  } as Record<string, string>)[value] || "任务状态已更新";
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
    report_en_md: "英文 Markdown",
    report_zh_md: "中文 Markdown",
    report_en_json: "英文结构化报告",
    report_zh_json: "中文结构化报告",
  } as Record<string, string>)[value] || "产物";
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
