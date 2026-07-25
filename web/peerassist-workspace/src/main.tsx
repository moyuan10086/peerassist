import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as pdfjsLib from "pdfjs-dist";
import {
  Bot,
  Building2,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Copy,
  Columns2,
  Download,
  ExternalLink,
  FileText,
  FileUp,
  FolderDown,
  GitBranch,
  History,
  Loader2,
  LogIn,
  LogOut,
  Maximize2,
  MessageSquareText,
  Minus,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Square,
  TerminalSquare,
  Users,
  X,
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

type WindowId = "paper" | "agent" | "queue" | "trace" | "confirm" | "artifacts" | "admin" | "login";

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
  warning_count?: number;
  warnings?: string[];
  drafts?: Concern[];
  metadata?: {
    responsibility?: string;
    evidence_count?: number;
    review_engine?: string;
    model_status?: string;
    model_configured?: boolean;
    model_usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number };
    model_context_budget?: { selected_blocks?: number; selected_chars?: number; max_blocks?: number; max_chars?: number };
  };
};

type ReviewJob = {
  id: string;
  paper_id: string;
  paper_version_id?: string;
  project_id?: string;
  status: string;
  stage: string;
  revision: number;
  version?: number;
  source_url?: string;
  platform?: boolean;
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
  paper_overview?: PaperOverview;
  state: ConfirmationState;
  model_config: {
    provider?: string;
    model?: string;
    base_url?: string;
    api_key_configured?: string | boolean;
  };
  assets: {
    pdf_url?: string;
    legacy_url?: string;
  };
  windows: WorkspaceWindow[];
};

type ModelSettingsView = {
  provider: "openai-codex" | "openai-compatible";
  base_url: string;
  model: string;
  api_mode?: string;
  api_key_configured: boolean;
  api_key_hint?: string;
};

type PaperOverview = {
  available?: boolean;
  title?: string;
  summary?: string;
  abstract?: string;
  objective?: string;
  method?: string;
  result?: string;
  limitation?: string;
};

function parsePaperSummaryMarkdown(markdown: string): PaperOverview {
  const sections = new Map<string, string[]>();
  let currentHeading = "";
  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    const heading = line.match(/^#{1,3}\s+(.+)$/);
    if (heading) {
      currentHeading = heading[1].trim();
      if (!sections.has(currentHeading)) sections.set(currentHeading, []);
      continue;
    }
    if (!line || !currentHeading) continue;
    sections.get(currentHeading)?.push(line.replace(/^[-*]\s+/, ""));
  }
  const read = (...labels: string[]) => labels
    .map((label) => sections.get(label)?.join(" ").trim() || "")
    .find(Boolean) || "";
  const knownHeadings = new Set([
    "这篇论文讲了什么",
    "一句话总结",
    "论文概要",
    "研究问题",
    "研究目标",
    "研究方法",
    "方法",
    "方法概览",
    "主要发现",
    "主要结果",
    "结果概览",
    "局限",
    "局限性",
    "阅读提示",
  ]);
  const title = [...sections.keys()].find((heading) => !knownHeadings.has(heading)) || "";
  const summary = read("一句话总结", "论文概要") || markdown.replace(/^#{1,3}\s+.*$/gm, "").replace(/\s+/g, " ").trim();
  return {
    available: Boolean(summary),
    title,
    summary,
    objective: read("研究问题", "研究目标"),
    method: read("研究方法", "方法", "方法概览"),
    result: read("主要发现", "主要结果", "结果概览"),
    limitation: read("局限", "局限性"),
  };
}

const REVIEW_DRAFT_STORAGE_PREFIX = "peerassist.reviewDraft.";

function reviewDraftStorageKey(jobId: string) {
  return `${REVIEW_DRAFT_STORAGE_PREFIX}${jobId || "local"}`;
}

function restoreReviewDraft(jobId: string, fallback: string) {
  try {
    const saved = window.localStorage.getItem(reviewDraftStorageKey(jobId));
    return saved === null ? fallback : saved;
  } catch {
    return fallback;
  }
}

function persistReviewDraft(jobId: string, draft: string) {
  try {
    window.localStorage.setItem(reviewDraftStorageKey(jobId), draft);
  } catch {
    // Private browsing or storage quotas should not block editing or export.
  }
}

type AuthUser = { id: string; display_name: string; status: string };
type AuthSession = {
  authenticated: boolean;
  user: AuthUser | null;
  available: boolean;
  roles: string[];
  is_admin: boolean;
};
type OrganizationRow = { id: string; slug: string; name: string; status: string; version: number };
type ProjectRow = { id: string; organization_id: string; name: string; status: string; version: number };
type ProjectOption = ProjectRow & { organization_name: string };
type MembershipRow = {
  id: string;
  organization_id: string;
  project_id?: string;
  user_id: string;
  role: string;
  status: string;
  version: number;
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
    { id: "paper", label: "阅读论文", path: "/paper" },
    { id: "agent", label: "生成审稿", path: "/agent" },
    { id: "queue", label: "证据与意见", path: "/queue" },
    { id: "trace", label: "处理记录", path: "/trace" },
    { id: "confirm", label: "待我确认", path: "/confirm" },
    { id: "artifacts", label: "导出报告", path: "/artifacts" },
    { id: "login", label: "登录", path: "/login" },
    { id: "admin", label: "设置", path: "/admin" },
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
  if (path.includes("login")) return "login";
  if (path.includes("admin")) return "admin";
  if (path.includes("agent")) return "agent";
  if (path.includes("queue")) return "queue";
  if (path.includes("trace")) return "trace";
  if (path.includes("confirm")) return "confirm";
  if (path.includes("artifacts")) return "artifacts";
  return "paper";
}

function cookieValue(name: string) {
  const prefix = `${name}=`;
  const value = document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith(prefix))
    ?.slice(prefix.length);
  return value ? decodeURIComponent(value) : "";
}

function apiErrorMessage(payload: unknown, fallback: string) {
  if (!payload || typeof payload !== "object" || !("error" in payload)) return fallback;
  const error = (payload as { error?: unknown }).error;
  const messages: Record<string, string> = {
    invalid_upload: "只支持有效的 PDF 文件，请重新选择论文。",
    payload_too_large: "PDF 文件超过 100 MB，请压缩后重新上传。",
    authentication_required: "登录状态已失效，请重新登录。",
    forbidden: "当前账号没有执行此操作的权限，请联系管理员。",
    dependency_unavailable: "服务暂时不可用，请稍后重试。",
    stale_version: "任务已被其他操作更新，请刷新后重试。",
    idempotency_conflict: "该操作已经提交，请刷新页面查看最新结果。",
    not_found: "目标内容不存在或已被删除，请刷新页面。",
  };
  if (typeof error === "string" && error.trim()) {
    if (error === "The operation could not be completed.") return fallback;
    if (error === "The request could not be validated.") return `${fallback}：请求参数不完整，请刷新后重试。`;
    return error;
  }
  if (error && typeof error === "object") {
    const code = String((error as { code?: unknown }).code || "").trim();
    if (messages[code]) return messages[code];
    const message = String((error as { message?: unknown }).message || "").trim();
    if (message && message !== "The operation could not be completed." && message !== "The request could not be validated.") return message;
    if (message === "The request could not be validated.") return `${fallback}：请求参数不完整，请刷新后重试。`;
  }
  return fallback;
}

const ACTIVE_PROJECT_STORAGE_KEY = "peerassist.activeProjectId";
const MAX_PDF_SIZE_BYTES = 100 * 1024 * 1024;

function idempotencyKey() {
  const webCrypto = globalThis.crypto;
  if (typeof webCrypto?.randomUUID === "function") return webCrypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof webCrypto?.getRandomValues === "function") {
    webCrypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function listPlatformProjects(): Promise<ProjectOption[]> {
  const organizationsResponse = await fetch("/api/v1/organizations", { cache: "no-store" });
  if (organizationsResponse.status === 401) throw new Error("AUTH_REQUIRED");
  if (!organizationsResponse.ok) {
    const payload = await organizationsResponse.json().catch(() => ({}));
    throw new Error(apiErrorMessage(payload, "无法读取当前账号的项目，请刷新后重试。"));
  }
  const organizations = await organizationsResponse.json() as OrganizationRow[];
  const projectGroups = await Promise.all(organizations.map(async (organization) => {
    const response = await fetch(`/api/v1/organizations/${organization.id}/projects`, { cache: "no-store" });
    if (response.status === 401) throw new Error("AUTH_REQUIRED");
    if (!response.ok) return [];
    const projects = await response.json() as ProjectRow[];
    return projects.map((project) => ({ ...project, organization_name: organization.name }));
  }));
  return projectGroups.flat().filter((project) => project.status === "active");
}

function App() {
  const initial = useMemo(() => ({ ...fallbackBootstrap, ...readServerBootstrap() }), []);
  const [bootstrap, setBootstrap] = useState<Bootstrap>(initial as Bootstrap);
  const [activeWindow, setActiveWindow] = useState<WindowId>(currentWindowFromPath());
  const [streamLines, setStreamLines] = useState<string[]>(["等待 PeerAssist 运行态事件流"]);
  const [lastReviewDraft, setLastReviewDraft] = useState("");
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState(false);
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [modelReachable, setModelReachable] = useState<boolean | null>(null);
  const [authSession, setAuthSession] = useState<AuthSession>({
    authenticated: false,
    user: null,
    available: false,
    roles: [],
    is_admin: false,
  });
  const [reviewJobs, setReviewJobs] = useState<ReviewJob[]>([]);
  const [platformProjects, setPlatformProjects] = useState<ProjectOption[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState(() => window.localStorage.getItem(ACTIVE_PROJECT_STORAGE_KEY) || "");
  const [projectsReady, setProjectsReady] = useState(false);
  const [activePaperId, setActivePaperId] = useState(() => window.localStorage.getItem("peerassist.activePaperId") || "");
  const [activeJobId, setActiveJobId] = useState(() => window.localStorage.getItem("peerassist.activeJobId") || "");
  const [activeContextReady, setActiveContextReady] = useState(() => !window.localStorage.getItem("peerassist.activePaperId"));
  const [jobWorkspace, setJobWorkspace] = useState<ConfirmationState | null>(null);
  const draftSaveRef = useRef<{ jobId: string; draft: string }>({ jobId: "", draft: "" });
  const [activePdfUrl, setActivePdfUrl] = useState(() => {
    const storedPdfUrl = window.localStorage.getItem("peerassist.activePdfUrl") || "";
    if (storedPdfUrl) return storedPdfUrl;
    const storedPaperId = window.localStorage.getItem("peerassist.activePaperId") || "";
    return storedPaperId ? `/api/papers/${storedPaperId}/source` : "";
  });

  const state = activeJobId && jobWorkspace ? jobWorkspace : bootstrap.state || {};
  const queueItems = state.queue?.items || [];
  const events = state.tool_trace?.events || [];
  const agentRuns = state.agent_runs || [];
  const paths = state.paths || {};
  const modelConfigured =
    bootstrap.model_config.api_key_configured === "true" ||
    bootstrap.model_config.api_key_configured === true;
  const modelEnabled = modelConfigured && modelReachable === true;
  const modelDisplay = bootstrap.model_config.model
    ? `${bootstrap.model_config.model}${
      modelEnabled
        ? ""
        : !modelConfigured
          ? "（未启用）"
          : modelReachable === false
            ? "（连接失败）"
            : "（验证中）"
    }`
    : "模型未配置";
  const activeReviewJob = reviewJobs.find((job) => job.id === activeJobId)
    || reviewJobs.find((job) => job.paper_id === activePaperId);
  const selectedProject = platformProjects.find((project) => project.id === selectedProjectId) || null;
  const hasActivePaper = Boolean(activeContextReady && activePaperId);
  const draftStorageKey = reviewDraftStorageKey(activeJobId);

  const clearActivePaper = useCallback(() => {
    window.localStorage.removeItem("peerassist.activePaperId");
    window.localStorage.removeItem("peerassist.activeJobId");
    window.localStorage.removeItem("peerassist.activePdfUrl");
    setActivePaperId("");
    setActiveJobId("");
    setActivePdfUrl("");
    setJobWorkspace(null);
    setActiveContextReady(true);
    setBootstrap((current) => ({ ...current, paper_overview: undefined }));
  }, []);

  const chooseProject = useCallback((projectId: string) => {
    if (!projectId || projectId === selectedProjectId) return;
    window.localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY, projectId);
    setSelectedProjectId(projectId);
    setReviewJobs([]);
    clearActivePaper();
  }, [clearActivePaper, selectedProjectId]);

  useEffect(() => {
    if (!modelConfigured || !bootstrap.model_config.base_url || !bootstrap.model_config.model) {
      setModelReachable(false);
      return;
    }
    const controller = new AbortController();
    setModelReachable(null);
    fetch("/api/model-settings/discover", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": cookieValue("peerassist_csrf"),
      },
      body: JSON.stringify({
        provider: bootstrap.model_config.provider,
        base_url: bootstrap.model_config.base_url,
        model: bootstrap.model_config.model,
        api_key: "",
      }),
      signal: controller.signal,
    })
      .then((response) => setModelReachable(response.ok))
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setModelReachable(false);
      });
    return () => controller.abort();
  }, [
    bootstrap.model_config.api_key_configured,
    bootstrap.model_config.base_url,
    bootstrap.model_config.model,
    bootstrap.model_config.provider,
    modelConfigured,
  ]);

  useEffect(() => {
    if (!authSession.authenticated || !activePaperId || !activePdfUrl) {
      if (!activePaperId) setActiveContextReady(true);
      return;
    }
    let cancelled = false;
    setActiveContextReady(false);
    fetch(activePdfUrl, { method: "HEAD", cache: "no-store" })
      .then((response) => {
        if (cancelled) return;
        if ([404, 410].includes(response.status)) {
          clearActivePaper();
          return;
        }
        setActiveContextReady(true);
      })
      .catch(() => setActiveContextReady(true));
    return () => {
      cancelled = true;
    };
  }, [activePaperId, activePdfUrl, authSession.authenticated, clearActivePaper]);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 2400);
  }, []);

  const refresh = useCallback(async () => {
    // Authenticated sessions use the v1 APIs even when the account has no
    // project yet. Never fall back to the retired bootstrap endpoint.
    if (authSession.authenticated) {
      return bootstrap;
    }
    const response = await fetch("/api/bootstrap");
    if (!response.ok) throw new Error("无法读取 PeerAssist 状态");
    const payload = (await response.json()) as Bootstrap;
    setBootstrap(payload);
    return payload;
  }, [authSession.authenticated, bootstrap]);

  const refreshJobs = useCallback(async () => {
    const project = selectedProject;
    if (project) {
      const [jobsResponse, papersResponse] = await Promise.all([
        fetch(`/api/v1/projects/${project.id}/review-jobs`, { cache: "no-store" }),
        fetch(`/api/v1/projects/${project.id}/papers`, { cache: "no-store" }),
      ]);
      if (jobsResponse.ok && papersResponse.ok) {
        const rows = await jobsResponse.json() as Array<ReviewJob & { paper_version_id: string; version: number }>;
        const papers = await papersResponse.json() as Array<{ id: string; current_version_id: string }>;
        const paperByVersion = new Map(papers.map((paper) => [paper.current_version_id, paper.id]));
        const jobs = rows.map((row) => {
          const paperId = paperByVersion.get(row.paper_version_id) || row.paper_version_id;
          return {
            ...row,
            paper_id: paperId,
            revision: row.version,
            platform: true,
            source_url: `/api/v1/projects/${project.id}/papers/${paperId}/source`,
          };
        });
        jobs.sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
        setReviewJobs(jobs);
        return jobs;
      }
    }
    // A logged-in platform user without a project has an empty workspace, not
    // a legacy job store. Calling /api/jobs here produced a misleading 404.
    if (authSession.authenticated) {
      setReviewJobs([]);
      return [];
    }
    const response = await fetch("/api/jobs", { cache: "no-store" });
    if (!response.ok) throw new Error("无法读取后台审稿任务");
    const payload = (await response.json()) as { jobs?: ReviewJob[] };
    const jobs = (payload.jobs || []).map((job) => ({ ...job, platform: false }));
    jobs.sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
    setReviewJobs(jobs);
    return jobs;
  }, [authSession.authenticated, selectedProject]);

  const refreshSession = useCallback(async () => {
    try {
      const response = await fetch("/api/v1/auth/session", { cache: "no-store" });
      if (!response.ok) throw new Error("身份服务不可用");
      const payload = (await response.json()) as {
        authenticated?: boolean;
        user?: (AuthUser & { roles?: string[]; is_admin?: boolean }) | null;
      };
      const next = {
        authenticated: Boolean(payload.authenticated),
        user: payload.user || null,
        available: true,
        roles: payload.user?.roles || [],
        is_admin: Boolean(payload.user?.is_admin),
      };
      setAuthSession(next);
      return next;
    } catch {
      const next = { authenticated: false, user: null, available: false, roles: [], is_admin: false };
      setAuthSession(next);
      return next;
    }
  }, []);

  const refreshJobWorkspace = useCallback(async (jobId: string) => {
    if (!jobId) return null;
    const platformJob = reviewJobs.find((job) => job.id === jobId && job.platform);
    if (platformJob?.project_id) {
      const base = `/api/v1/projects/${platformJob.project_id}/review-jobs/${jobId}`;
      const [artifactResponse, eventResponse] = await Promise.all([
        fetch(`${base}/artifacts`, { cache: "no-store" }),
        fetch(`${base}/events`, { cache: "no-store" }),
      ]);
      if (!artifactResponse.ok || !eventResponse.ok) throw new Error("无法读取平台审稿产物");
      const draftResponse = await fetch(`${base}/draft`, { cache: "no-store" });
      const artifacts = await artifactResponse.json() as Array<{ id: string; logical_name: string; object: { media_type: string; size_bytes: number; sha256: string } }>;
      const eventRows = await eventResponse.json() as Array<{ event_type: string; created_at: string; payload?: Record<string, unknown> }>;
      const artifactUrl = (id: string) => `${base}/artifacts/${id}`;
      const summaryArtifact = artifacts.find((item) => item.logical_name === "paper_summary.md");
      const reviewArtifact = artifacts.find((item) => item.logical_name === "review.md");
      const resultArtifact = artifacts.find((item) => item.logical_name === "review_result.json");
      const [summaryMarkdown, reviewMarkdown, resultJson] = await Promise.all([
        summaryArtifact ? fetch(artifactUrl(summaryArtifact.id)).then((response) => response.ok ? response.text() : "") : "",
        reviewArtifact ? fetch(artifactUrl(reviewArtifact.id)).then((response) => response.ok ? response.text() : "") : "",
        resultArtifact ? fetch(artifactUrl(resultArtifact.id)).then((response) => response.ok ? response.text() : "") : "",
      ]);
      let structuredResult: {
        concerns?: Concern[];
        agent_runs?: AgentRun[];
        citation_audit?: CitationAuditSummary;
      } = {};
      try {
        const parsed = resultJson ? JSON.parse(resultJson) : {};
        if (parsed && typeof parsed === "object") structuredResult = parsed;
      } catch {
        // Keep Markdown/report viewing available even if a result artifact is malformed.
      }
      const parsedSummary = parsePaperSummaryMarkdown(summaryMarkdown);
      if (parsedSummary.available) {
        setBootstrap((current) => ({
          ...current,
          paper_overview: {
            ...(current.paper_overview || {}),
            available: true,
            title: parsedSummary.title || current.paper_overview?.title || "当前论文",
            summary: parsedSummary.summary,
            objective: parsedSummary.objective || current.paper_overview?.objective,
            method: parsedSummary.method || current.paper_overview?.method,
            result: parsedSummary.result || current.paper_overview?.result,
            limitation: parsedSummary.limitation || current.paper_overview?.limitation,
          },
        }));
      }
      const savedDraft = draftResponse.ok
        ? String(((await draftResponse.json()) as { draft?: string }).draft || "")
        : null;
      const localDraft = restoreReviewDraft(jobId, reviewMarkdown);
      const hydratedDraft = savedDraft || localDraft;
      // When the server had no draft, allow a locally recovered draft to be
      // written back after connectivity returns. Avoid saving an empty draft
      // merely because the server returned an empty snapshot.
      draftSaveRef.current = {
        jobId,
        draft: savedDraft !== null && hydratedDraft === savedDraft ? hydratedDraft : "",
      };
      setLastReviewDraft(hydratedDraft);
      const nextState: ConfirmationState = {
        queue: { items: Array.isArray(structuredResult.concerns) ? structuredResult.concerns : [] },
        pending_count: Array.isArray(structuredResult.concerns)
          ? structuredResult.concerns.filter((item) => item.status === "pending_human_confirmation").length
          : 0,
        ready_for_confirmation: Boolean(structuredResult.concerns?.length),
        agent_runs: Array.isArray(structuredResult.agent_runs) ? structuredResult.agent_runs : [],
        citation_audit: structuredResult.citation_audit,
        artifacts: {
          ready: artifacts.length > 0,
          items: artifacts.map((item) => ({
            name: item.logical_name,
            filename: item.logical_name,
            media_type: item.object.media_type,
            size_bytes: item.object.size_bytes,
            sha256: item.object.sha256,
            download_url: artifactUrl(item.id),
          })),
        },
        tool_trace: {
          events: eventRows.map((event, index) => ({
            call_id: `${jobId}-${index}`,
            source: "platform",
            tool: event.event_type,
            status: "completed",
            ts: event.created_at,
            output_summary: JSON.stringify(event.payload || {}),
          })),
        },
      };
      setJobWorkspace(nextState);
      return nextState;
    }
    const response = await fetch(`/api/jobs/${jobId}/workspace`, { cache: "no-store" });
    if (!response.ok) throw new Error("无法读取当前审稿任务工作区");
    const payload = (await response.json()) as { state?: ConfirmationState };
    const nextState = payload.state || {};
    setJobWorkspace(nextState);
    return nextState;
  }, [reviewJobs]);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    if (!authSession.authenticated) {
      setPlatformProjects([]);
      setProjectsReady(false);
      return;
    }
    let cancelled = false;
    setProjectsReady(false);
    listPlatformProjects()
      .then((projects) => {
        if (cancelled) return;
        setPlatformProjects(projects);
        const stored = window.localStorage.getItem(ACTIVE_PROJECT_STORAGE_KEY) || "";
        const nextProjectId = projects.some((project) => project.id === stored) ? stored : projects[0]?.id || "";
        setSelectedProjectId(nextProjectId);
        if (nextProjectId) window.localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY, nextProjectId);
        else window.localStorage.removeItem(ACTIVE_PROJECT_STORAGE_KEY);
      })
      .catch((cause) => {
        if (cancelled) return;
        if (cause instanceof Error && cause.message === "AUTH_REQUIRED") {
          setAuthSession({ authenticated: false, user: null, available: true, roles: [], is_admin: false });
          return;
        }
        showToast(cause instanceof Error ? cause.message : "无法读取当前账号的项目");
        setPlatformProjects([]);
      })
      .finally(() => {
        if (!cancelled) setProjectsReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [authSession.authenticated, showToast]);

  useEffect(() => {
    if (!authSession.authenticated) {
      setReviewJobs([]);
      setJobWorkspace(null);
      return;
    }
    Promise.all([refresh(), refreshJobs()]).catch(() =>
      setStreamLines((lines) => [...lines, "初始状态读取失败，保留本地壳"]),
    );
  }, [authSession.authenticated, refresh, refreshJobs]);

  useEffect(() => {
    if (!authSession.authenticated) return;
    const timer = window.setInterval(() => refreshJobs().catch(() => undefined), 3000);
    return () => window.clearInterval(timer);
  }, [authSession.authenticated, refreshJobs]);

  useEffect(() => {
    // Wait until the authenticated job list has resolved before deciding
    // whether a restored ID belongs to the platform or legacy API. This
    // prevents a transient legacy 404 during page reload.
    if (!activeJobId || !reviewJobs.some((job) => job.id === activeJobId)) {
      setJobWorkspace(null);
      return;
    }
    refreshJobWorkspace(activeJobId).catch(() => setJobWorkspace(null));
    const timer = window.setInterval(() => refreshJobWorkspace(activeJobId).catch(() => undefined), 3000);
    return () => window.clearInterval(timer);
  }, [activeJobId, refreshJobWorkspace]);

  useEffect(() => {
    if (!activeReviewJob?.platform || !activeReviewJob.project_id || !activeJobId) return;
    if (draftSaveRef.current.jobId === activeJobId && draftSaveRef.current.draft === lastReviewDraft) return;
    const timer = window.setTimeout(() => {
      void fetch(`/api/v1/projects/${activeReviewJob.project_id}/review-jobs/${activeJobId}/draft`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey(),
          "X-CSRF-Token": cookieValue("peerassist_csrf"),
        },
        body: JSON.stringify({ draft: lastReviewDraft, expected_version: activeReviewJob.revision }),
      })
        .then(async (response) => {
          if (!response.ok) return;
          const saved = await response.json() as { version?: number };
          draftSaveRef.current = { jobId: activeJobId, draft: lastReviewDraft };
          const nextVersion = saved.version;
          if (typeof nextVersion === "number") {
            setReviewJobs((jobs) => jobs.map((job) => job.id === activeJobId ? { ...job, revision: nextVersion } : job));
          }
        })
        .catch(() => undefined);
    }, 700);
    return () => window.clearTimeout(timer);
  }, [activeJobId, activeReviewJob?.platform, activeReviewJob?.project_id, activeReviewJob?.revision, lastReviewDraft]);

  useEffect(() => {
    // v1 exposes a job-scoped replay stream. The old global stream is only
    // available to legacy jobs and must not be opened for platform projects.
    if (!authSession.authenticated || !window.EventSource || !reviewJobs.length || reviewJobs.every((job) => job.platform)) return;
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
  }, [authSession.authenticated, reviewJobs]);

  useEffect(() => {
    if (!authSession.available || authSession.authenticated || activeWindow === "login") return;
    window.history.replaceState({}, "", "/login");
    setActiveWindow("login");
  }, [activeWindow, authSession.available, authSession.authenticated]);

  const navigate = (windowId: WindowId, path: string) => {
    window.history.pushState({}, "", path);
    setActiveWindow(windowId);
  };

  const openPaperById = (paperId: string, jobId = "", sourceUrl = "") => {
    const normalized = paperId.trim();
    if (!/^[0-9a-f-]{32,64}$/i.test(normalized)) return;
    window.localStorage.setItem("peerassist.activePaperId", normalized);
    if (jobId) {
      window.localStorage.setItem("peerassist.activeJobId", jobId);
      setActiveJobId(jobId);
    }
    setActivePaperId(normalized);
    setActiveContextReady(false);
    setLastReviewDraft("");
    setBootstrap((current) => ({ ...current, paper_overview: undefined }));
    const resolvedSource = sourceUrl || `/api/papers/${normalized}/source`;
    window.localStorage.setItem("peerassist.activePdfUrl", resolvedSource);
    setActivePdfUrl(resolvedSource);
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
      if (activeReviewJob?.platform) {
        await refreshJobWorkspace(activeReviewJob.id);
        showToast("平台审稿任务已在运行，请在任务卡查看进度");
        return;
      }
      const response = await fetch("/api/agent-review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_text: selectedText, review_mode: reviewMode }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(payload, "智能审稿失败，请刷新后重试"));
      const structuredCount = Number(payload.structured_concern_count || 0);
      const queueItems = Number(payload.queue_items || 0);
      const suggestion = String(payload.suggestion || "");
      showToast(`已写入 ${structuredCount} 条结构化意见`);
      setLastReviewDraft(suggestion);
      persistReviewDraft(activeJobId, suggestion);
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
      if (activeReviewJob?.platform && activeReviewJob.project_id) {
        const response = await fetch(`/api/v1/projects/${activeReviewJob.project_id}/review-jobs/${activeReviewJob.id}/decisions`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey(),
            "X-CSRF-Token": cookieValue("peerassist_csrf"),
          },
          body: JSON.stringify({
            decision_type: "concern",
            subject_id: `manual-${Date.now()}`,
            decision: `${note.trim()}${selectedText.trim() ? `\n原文：${selectedText.trim()}` : ""}${page ? `\n页码：${page}` : ""}`.trim(),
            expected_version: activeReviewJob.revision,
          }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(apiErrorMessage(payload, "加入队列失败，请检查当前论文和登录状态"));
        showToast("人工意见已记录到平台任务");
        await refreshJobs();
        await refreshJobWorkspace(activeReviewJob.id);
        navigate("queue", "/queue");
        return;
      }
      const response = await fetch("/api/manual-concern", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_text: selectedText, note, page: Number(page) || 0 }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(payload, "加入队列失败，请刷新后重试"));
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
      if (activeReviewJob?.platform && activeReviewJob.project_id) {
        const response = await fetch(`/api/v1/projects/${activeReviewJob.project_id}/review-jobs/${activeReviewJob.id}/decisions`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey(),
            "X-CSRF-Token": cookieValue("peerassist_csrf"),
          },
          body: JSON.stringify({
            decision_type: "concern",
            subject_id: concern.id,
            decision: action,
            expected_version: activeReviewJob.revision,
          }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(apiErrorMessage(payload, "确认失败，请刷新任务后重试"));
        showToast(`已记录：${labelAction(action)}`);
        await refreshJobs();
        await refreshJobWorkspace(activeReviewJob.id);
        return;
      }
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
        if (!response.ok) throw new Error(apiErrorMessage(payload, "确认失败，请刷新任务后重试"));
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

  async function runJobAction(job: ReviewJob, action: "cancel" | "retry" | "consent" | "finalize" | "delete") {
    setBusy(true);
    try {
      if (job.platform && job.project_id) {
        const platformAction = action === "consent" ? "decisions" : action;
        const response = await fetch(
          action === "delete"
            ? `/api/v1/projects/${job.project_id}/review-jobs/${job.id}`
            : `/api/v1/projects/${job.project_id}/review-jobs/${job.id}/${platformAction}`,
          {
            method: action === "delete" ? "DELETE" : "POST",
            headers: {
              "Content-Type": "application/json",
              "Idempotency-Key": idempotencyKey(),
              "X-CSRF-Token": cookieValue("peerassist_csrf"),
            },
            ...(action === "delete" ? {} : {
              body: JSON.stringify(
                action === "consent"
                  ? { decision_type: "consent", subject_id: "model", decision: "granted", expected_version: job.revision }
                  : { expected_version: job.revision },
              ),
            }),
          },
        );
        const result = response.status === 204 ? {} : await response.json();
        if (!response.ok) throw new Error(apiErrorMessage(result, action === "delete" ? "删除失败，请刷新后重试" : "任务操作失败，请刷新后重试"));
        showToast(jobActionLabel(action));
        await refreshJobs();
        if (action === "delete" && activeJobId === job.id) clearActivePaper();
        return;
      }
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
      if (!response.ok) throw new Error(apiErrorMessage(result, "任务操作失败，请刷新后重试"));
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
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      showToast("请选择 PDF 文件，Word 或图片暂不支持。");
      return false;
    }
    if (file.size > MAX_PDF_SIZE_BYTES) {
      showToast("PDF 文件超过 100 MB，请压缩后重新上传。");
      return false;
    }
    setBusy(true);
    setStreamLines((lines) => [...lines.slice(-80), `upload: 正在上传 ${file.name}`]);
    try {
      if (authSession.authenticated) {
        const project = selectedProject;
        if (!project) throw new Error("当前账号还没有可用项目，请联系管理员将你加入项目。");
        const body = new FormData();
        body.append("file", file, file.name);
        const uploadedResponse = await fetch(`/api/v1/projects/${project.id}/papers`, {
          method: "POST",
          headers: {
            "Idempotency-Key": idempotencyKey(),
            "X-CSRF-Token": cookieValue("peerassist_csrf"),
          },
          body,
        });
        const uploaded = await uploadedResponse.json();
        if (!uploadedResponse.ok) throw new Error(apiErrorMessage(uploaded, "论文上传失败，请检查 PDF 格式和登录状态"));
        const reviewResponse = await fetch(`/api/v1/projects/${project.id}/review-jobs`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey(),
            "X-CSRF-Token": cookieValue("peerassist_csrf"),
          },
          body: JSON.stringify({ paper_version_id: uploaded.version.id, mode: "full" }),
        });
        const job = await reviewResponse.json();
        if (!reviewResponse.ok) throw new Error(apiErrorMessage(job, "审阅任务创建失败，请稍后重试"));
        showToast("论文已上传，平台审阅任务已启动");
        await refreshJobs();
        openPaperById(
          uploaded.paper.id,
          job.id,
          `/api/v1/projects/${project.id}/papers/${uploaded.paper.id}/source`,
        );
        return true;
      }
      const body = new FormData();
      body.append("file", file, file.name);
      const response = await fetch("/api/papers/upload", { method: "POST", body });
      const result = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(result, "论文上传失败，请检查文件和服务状态"));
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

  async function logout() {
    const csrf = cookieValue("peerassist_csrf");
    if (!csrf) {
      clearActivePaper();
      await refreshSession();
      navigate("login", "/login");
      return;
    }
    await fetch("/api/v1/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
      redirect: "manual",
    }).catch(() => undefined);
    await refreshSession();
    clearActivePaper();
    navigate("login", "/login");
  }

  const visibleWindows = bootstrap.windows.filter((item) => {
    if (item.id === "login") return !authSession.authenticated;
    if (!authSession.authenticated) return false;
    return item.id !== "admin" || authSession.is_admin;
  });
  const roleLabel = authSession.is_admin
    ? "组织管理员"
    : authSession.roles.length
      ? "项目成员"
      : "待授权成员";
  const activeLabel = activeWindow === "login"
    ? "登录"
    : bootstrap.windows.find((item) => item.id === activeWindow)?.label || "论文阅读";

  if (activeWindow === "login" && !authSession.authenticated) {
    return <LoginLayout available={authSession.available} />;
  }

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
          {visibleWindows.map((item) => (
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
          {authSession.is_admin ? (
            <button className="model-status-button" type="button" onClick={() => setModelSettingsOpen(true)} title="模型设置">
              <Settings2 size={14} />
              <span>{modelDisplay}</span>
            </button>
          ) : (
            <span className="sidebar-access-state">{authSession.authenticated ? roleLabel : "登录后可用"}</span>
          )}
          <span>证据约束 · 人工确认</span>
        </div>
      </aside>

      <main className={`workspace ${activeWindow === "paper" ? "paper-workspace" : ""}`}>
        <header className="workspace-topbar">
          <div>
            <p className="eyebrow">{bootstrap.paper_overview?.title || "当前审稿项目"}</p>
            <h1>{activeWindow === "paper" ? "阅读论文" : activeLabel}</h1>
          </div>
          <div className="topbar-actions">
            {authSession.authenticated ? (
              <>
                {platformProjects.length > 0 && (
                  <label className="project-switcher">
                    <Building2 size={15} />
                    <select aria-label="当前审稿项目" value={selectedProjectId} onChange={(event) => chooseProject(event.target.value)}>
                      {platformProjects.map((project) => (
                        <option value={project.id} key={project.id}>{project.organization_name} / {project.name}</option>
                      ))}
                    </select>
                  </label>
                )}
                {authSession.is_admin && <button
                  className="ghost-button"
                  type="button"
                  title={`当前账号：${authSession.user?.display_name || "已登录"}`}
                  onClick={() => navigate("admin", "/admin")}
                >
                  <Users size={16} /> 成员与权限
                </button>}
                <span className="role-chip"><ShieldCheck size={14} /> {roleLabel}</span>
                <button className="icon-button" type="button" onClick={() => void logout()} title="退出登录" aria-label="退出登录">
                  <LogOut size={16} />
                </button>
              </>
            ) : (
              <>
                <button className="ghost-button" type="button" onClick={() => navigate("login", "/login")}>
                  <LogIn size={16} /> 登录 / 注册
                </button>
                <span className="role-chip"><ShieldCheck size={14} /> 登录后查看工作区</span>
              </>
            )}
            {authSession.is_admin && <button className="ghost-button" type="button" onClick={() => setModelSettingsOpen(true)}>
              <Settings2 size={16} /> 模型设置
            </button>}
            {authSession.authenticated && <>
              <button className="ghost-button" type="button" onClick={() => refresh().then(() => showToast("状态已刷新"))}>
                <RefreshCw size={16} /> 刷新状态
              </button>
              <button className="primary-button" type="button" disabled={busy} onClick={() => navigate("agent", "/agent")}>
                <Bot size={16} /> 上传论文 / 智能审稿
              </button>
            </>}
          </div>
        </header>

        {activeWindow !== "paper" && authSession.authenticated && <StatusStrip state={state} model={modelDisplay} />}

        {activeWindow === "paper" && (authSession.authenticated ? (
          <>
            {!projectsReady ? (
              <WorkspaceLoading label="正在读取你的审稿项目" />
            ) : !selectedProject ? (
              <ProjectOnboarding isAdmin={authSession.is_admin} onOpenAdmin={() => navigate("admin", "/admin")} />
            ) : hasActivePaper ? (
              <>
                <PaperOverviewPanel overview={bootstrap.paper_overview} jobStatus={activeReviewJob?.status} />
                <PaperWindow
                  pdfUrl={activePdfUrl}
                  queueItems={queueItems}
                  busy={busy}
                  linkedReviewJob={Boolean(activeReviewJob)}
                  reviewStatus={activeReviewJob?.status}
                  readyForConfirmation={Boolean(state.ready_for_confirmation)}
                  citationAudit={state.citation_audit}
                  onRunReview={runAgentReview}
                  onSubmitManual={submitManualConcern}
                  onDecision={submitDecision}
                />
              </>
            ) : (
              <PaperEmptyState onUpload={() => navigate("agent", "/agent")} />
            )}
          </>
        ) : <AccessGate title="论文内容已锁定" detail="登录并获得项目成员授权后，才能查看论文、摘要和审稿证据。" />)}
        {activeWindow === "agent" && (
          authSession.authenticated ?
          !projectsReady ? <WorkspaceLoading label="正在读取你的审稿项目" /> : !selectedProject ? (
            <ProjectOnboarding isAdmin={authSession.is_admin} onOpenAdmin={() => navigate("admin", "/admin")} />
          ) : <AgentWindow
            busy={busy}
            model={modelDisplay}
            modelEnabled={modelEnabled}
            baseUrl={bootstrap.model_config.base_url || ""}
            agentRuns={agentRuns}
            streamLines={streamLines}
            lastReviewDraft={lastReviewDraft}
            draftStorageKey={draftStorageKey}
            onDraftChange={(draft) => {
              setLastReviewDraft(draft);
              persistReviewDraft(activeJobId, draft);
            }}
            reviewJobs={reviewJobs}
            onRunReview={runAgentReview}
            onJobAction={runJobAction}
            onUploadPaper={uploadPaper}
              onOpenPaper={(job) => openPaperById(job.paper_id, job.id, job.source_url)}
          /> : <AccessGate title="智能审稿需要登录" detail="登录后才能上传论文、启动任务和查看模型结果。" />
        )}
        {activeWindow === "queue" && (authSession.authenticated ? <QueueWindow items={queueItems} onDecision={submitDecision} onOpenConfirm={() => navigate("confirm", "/confirm")} /> : <AccessGate title="证据队列需要登录" detail="登录后才能查看项目证据和待确认项。" />)}
        {activeWindow === "trace" && (authSession.authenticated ? <TraceWindow events={events} streamLines={streamLines} /> : <AccessGate title="工具追踪需要登录" detail="登录后才能查看审稿事件和工具调用。" />)}
        {activeWindow === "confirm" && (authSession.authenticated ? <ConfirmWindow items={queueItems} busy={busy} onDecision={submitDecision} /> : <AccessGate title="人工确认需要登录" detail="登录后才能处理审稿意见。" />)}
        {activeWindow === "artifacts" && (authSession.authenticated ? <ArtifactsWindow paths={paths} reports={state.artifacts} /> : <AccessGate title="产物导出需要登录" detail="登录后才能下载审阅报告和产物。" />)}
        {activeWindow === "admin" && (
          authSession.is_admin
            ? <AdminWindow showToast={showToast} />
            : <AccessGate title="需要管理员权限" detail="只有组织管理员可以维护项目、成员和角色权限。" />
        )}
        <ModelSettingsDrawer
          open={modelSettingsOpen}
          initial={bootstrap.model_config}
          onClose={() => setModelSettingsOpen(false)}
          onSaved={(settings) => {
            setModelReachable(null);
            setBootstrap((current) => ({
              ...current,
              model_config: {
                provider: settings.provider,
                base_url: settings.base_url,
                model: settings.model,
                api_key_configured: settings.api_key_configured,
              },
            }));
            showToast(`模型已切换为 ${settings.model}`);
          }}
        />
        {toast && <div className="toast">{toast}</div>}
      </main>
    </div>
  );
}

function AccessGate({ title, detail }: { title: string; detail: string }) {
  return (
    <section className="access-gate" role="status">
      <span className="access-gate-icon"><ShieldCheck size={28} /></span>
      <p className="eyebrow">访问控制</p>
      <h2>{title}</h2>
      <p>{detail}</p>
      <button className="primary-button" type="button" onClick={() => window.location.assign("/api/v1/auth/login?return_path=/paper")}>
        <LogIn size={16} /> 登录后继续
      </button>
    </section>
  );
}

function WorkspaceLoading({ label }: { label: string }) {
  return (
    <section className="project-onboarding" role="status">
      <Loader2 className="spin" size={30} />
      <h2>{label}</h2>
      <p>马上就好。</p>
    </section>
  );
}

function ProjectOnboarding({ isAdmin, onOpenAdmin }: { isAdmin: boolean; onOpenAdmin: () => void }) {
  return (
    <section className="project-onboarding" role="status">
      <span className="project-onboarding-icon"><Building2 size={30} /></span>
      <p className="eyebrow">开始第一次智能审稿</p>
      <h2>还没有可用的审稿项目</h2>
      <p>{isAdmin ? "先创建一个项目，用来保存论文、审稿意见和最终报告。" : "联系管理员将你加入项目，加入后即可上传论文开始审稿。"}</p>
      {isAdmin ? (
        <button className="primary-button" type="button" onClick={onOpenAdmin}><Plus size={16} /> 创建第一个项目</button>
      ) : (
        <div className="project-onboarding-tip"><Users size={16} /> 联系管理员将你加入项目</div>
      )}
    </section>
  );
}

function PaperEmptyState({ onUpload }: { onUpload: () => void }) {
  return (
    <section className="paper-empty-state" role="status">
      <div className="paper-empty-icon"><FileText size={30} /></div>
      <p className="eyebrow">论文工作台</p>
      <h2>先上传一篇论文</h2>
      <p>上传 PDF 后，这里会显示论文概要、重点阅读路线和有证据的审稿意见。</p>
      <button className="primary-button" type="button" onClick={onUpload}><FileUp size={16} /> 上传论文</button>
    </section>
  );
}

function PaperOverviewPanel({ overview, jobStatus }: { overview?: PaperOverview; jobStatus?: string }) {
  const facts = [
    ["研究问题", overview?.objective],
    ["研究方法", overview?.method],
    ["主要结果", overview?.result],
    ["局限性", overview?.limitation],
  ].filter((item) => item[1]);
  return (
    <details className="paper-overview" open>
      <summary>
        <span>
          <small>论文概览</small>
          <strong>{overview?.title || "这篇论文讲了什么"}</strong>
        </span>
        <span className="paper-overview-toggle">展开 / 收起</span>
      </summary>
      <div className="paper-overview-body">
        <div className="paper-overview-summary">
          <small>这篇论文讲了什么 · 一句话结论</small>
            <p>{overview?.summary || (jobStatus ? `摘要正在生成：当前${labelJobStatus(jobStatus)}。完成后会在这里显示问题、方法、发现与局限。` : "论文概要将在上传后生成。")}</p>
        </div>
        {facts.map(([label, value]) => (
          <div className="paper-overview-fact" key={label}>
            <small>{label}</small>
            <p>{value}</p>
          </div>
        ))}
        {overview?.abstract && (
          <details className="paper-abstract">
            <summary>查看英文摘要原文</summary>
            <p>{overview.abstract}</p>
          </details>
        )}
      </div>
    </details>
  );
}

function LoginLayout({ available }: { available: boolean }) {
  return (
    <main className="login-entry">
      <section className="login-entry-brand" aria-label="PeerAssist">
        <span className="login-entry-mark">PA</span>
        <div>
          <strong>PeerAssist</strong>
          <p>智能审稿工作台</p>
        </div>
      </section>
      <section className="login-entry-card" aria-labelledby="login-entry-title">
        <p className="login-entry-kicker">欢迎回来</p>
        <h1 id="login-entry-title">登录账号</h1>
        <p className="login-entry-copy">进入你的审稿项目，继续阅读、审阅和确认论文意见。</p>
        <div className="login-entry-actions">
          <button
            className="login-entry-primary"
            type="button"
            disabled={!available}
            onClick={() => window.location.assign("/api/v1/auth/login?return_path=/paper")}
          >
            <LogIn size={17} /> {available ? "登录" : "身份服务启动中"}
          </button>
          <button
            className="login-entry-register"
            type="button"
            disabled={!available}
            onClick={() => window.location.assign("/api/v1/auth/register?return_path=/paper")}
          >
            没有账号？创建一个
          </button>
        </div>
      </section>
      <p className="login-entry-note">论文与审稿记录仅对登录后的项目成员开放。</p>
    </main>
  );
}

function AdminWindow({ showToast }: { showToast: (message: string) => void }) {
  const [organizations, setOrganizations] = useState<OrganizationRow[]>([]);
  const [organizationId, setOrganizationId] = useState("");
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [projectId, setProjectId] = useState("");
  const [organizationMembers, setOrganizationMembers] = useState<MembershipRow[]>([]);
  const [projectMembers, setProjectMembers] = useState<MembershipRow[]>([]);
  const [projectName, setProjectName] = useState("");
  const [newUserId, setNewUserId] = useState("");
  const [projectUserId, setProjectUserId] = useState("");
  const [projectRole, setProjectRole] = useState("reviewer");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const api = useCallback(async (path: string, init?: RequestInit) => {
    const method = String(init?.method || "GET").toUpperCase();
    const headers = new Headers(init?.headers);
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const csrf = cookieValue("peerassist_csrf");
      if (csrf) headers.set("X-CSRF-Token", csrf);
    }
    const response = await fetch(path, { cache: "no-store", ...init, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(apiErrorMessage(payload, "管理操作失败，请刷新后重试"));
    return payload;
  }, []);

  const loadOrganizations = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const rows = await api("/api/v1/organizations") as OrganizationRow[];
      setOrganizations(rows);
      setOrganizationId((current) => current || rows[0]?.id || "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取组织");
    } finally {
      setLoading(false);
    }
  }, [api]);

  const loadOrganization = useCallback(async (id: string) => {
    if (!id) {
      setProjects([]);
      setOrganizationMembers([]);
      return;
    }
    try {
      const [projectRows, memberRows] = await Promise.all([
        api(`/api/v1/organizations/${id}/projects`) as Promise<ProjectRow[]>,
        api(`/api/v1/organizations/${id}/members`) as Promise<MembershipRow[]>,
      ]);
      setProjects(projectRows);
      setOrganizationMembers(memberRows);
      setProjectId((current) => projectRows.some((row) => row.id === current) ? current : projectRows[0]?.id || "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取组织数据");
    }
  }, [api]);

  const loadProjectMembers = useCallback(async (id: string) => {
    if (!id) {
      setProjectMembers([]);
      return;
    }
    try {
      setProjectMembers(await api(`/api/v1/projects/${id}/members`) as MembershipRow[]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取项目成员");
    }
  }, [api]);

  useEffect(() => { void loadOrganizations(); }, [loadOrganizations]);
  useEffect(() => { void loadOrganization(organizationId); }, [loadOrganization, organizationId]);
  useEffect(() => { void loadProjectMembers(projectId); }, [loadProjectMembers, projectId]);

  async function createProject(event: React.FormEvent) {
    event.preventDefault();
    if (!organizationId || !projectName.trim()) return;
    try {
      await api(`/api/v1/organizations/${organizationId}/projects`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify({ name: projectName.trim() }),
      });
      setProjectName("");
      await loadOrganization(organizationId);
      showToast("项目已创建");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "项目创建失败");
    }
  }

  async function grantOrganizationMember(event: React.FormEvent) {
    event.preventDefault();
    if (!organizationId || !newUserId.trim()) return;
    try {
      await api(`/api/v1/organizations/${organizationId}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify({ user_id: newUserId.trim(), expected_version: null }),
      });
      setNewUserId("");
      await loadOrganization(organizationId);
      showToast("组织管理员已添加");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "成员添加失败");
    }
  }

  async function grantProjectMember(event: React.FormEvent) {
    event.preventDefault();
    if (!projectId || !projectUserId) return;
    try {
      await api(`/api/v1/projects/${projectId}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify({ user_id: projectUserId, role: projectRole, expected_version: null }),
      });
      await loadProjectMembers(projectId);
      showToast("项目成员已添加");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "项目成员添加失败");
    }
  }

  async function updateProjectMember(member: MembershipRow, role: string, status = member.status) {
    try {
      await api(`/api/v1/projects/${member.project_id}/members/${member.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify({ role, status, expected_version: member.version }),
      });
      await loadProjectMembers(projectId);
      showToast(status === "revoked" ? "成员已撤销" : "成员角色已更新");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "成员更新失败");
    }
  }

  const activeOrganization = organizations.find((row) => row.id === organizationId);
  return (
    <section className="admin-layout">
      <div className="admin-command-bar">
        <div>
          <p className="eyebrow">组织管理</p>
          <h2>{activeOrganization?.name || "成员与项目"}</h2>
        </div>
        <label>
          <span>当前组织</span>
          <select value={organizationId} onChange={(event) => setOrganizationId(event.target.value)}>
            {organizations.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select>
        </label>
      </div>
      {loading && <div className="admin-empty"><Loader2 className="spin" /> 正在读取组织</div>}
      {!loading && !organizations.length && (
        <div className="admin-empty">
          <Building2 size={28} />
          <strong>当前账号尚未加入组织</strong>
          <span>完成管理员初始化后，组织与成员会显示在这里。</span>
        </div>
      )}
      {error && <p className="settings-error" role="alert">{error}</p>}
      {organizationId && (
        <div className="admin-grid">
          <section className="admin-section">
            <header><h3>项目</h3><span>{projects.length}</span></header>
            <form className="admin-inline-form" onSubmit={createProject}>
              <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="新项目名称" />
              <button className="primary-button" type="submit"><Plus size={15} /> 创建</button>
            </form>
            <div className="admin-list">
              {projects.map((row) => (
                <button key={row.id} type="button" aria-pressed={row.id === projectId} onClick={() => setProjectId(row.id)}>
                  <span><FolderDown size={16} /> {row.name}</span><small>{row.status}</small>
                </button>
              ))}
            </div>
          </section>
          <section className="admin-section">
            <header><h3>组织管理员</h3><span>{organizationMembers.length}</span></header>
            <form className="admin-inline-form" onSubmit={grantOrganizationMember}>
              <input value={newUserId} onChange={(event) => setNewUserId(event.target.value)} placeholder="用户 ID" />
              <button className="ghost-button" type="submit"><Plus size={15} /> 添加</button>
            </form>
            <MembershipTable rows={organizationMembers} />
          </section>
          <section className="admin-section admin-project-members">
            <header><h3>项目成员</h3><span>{projectMembers.length}</span></header>
            <form className="admin-member-form" onSubmit={grantProjectMember}>
              <input value={projectUserId} onChange={(event) => setProjectUserId(event.target.value)} placeholder="普通用户 ID" />
              <select value={projectRole} onChange={(event) => setProjectRole(event.target.value)}>
                <option value="project_owner">项目所有者</option>
                <option value="reviewer">审稿人</option>
                <option value="viewer">只读成员</option>
              </select>
              <button className="primary-button" type="submit" disabled={!projectId}><Plus size={15} /> 添加成员</button>
            </form>
            <MembershipTable rows={projectMembers} onRoleChange={updateProjectMember} />
          </section>
        </div>
      )}
    </section>
  );
}

function MembershipTable({
  rows,
  onRoleChange,
}: {
  rows: MembershipRow[];
  onRoleChange?: (row: MembershipRow, role: string, status?: string) => void;
}) {
  if (!rows.length) return <div className="admin-list-empty">暂无成员</div>;
  return (
    <div className="member-table">
      {rows.map((row) => (
        <div className="member-row" key={row.id}>
          <span className="member-avatar">{row.user_id.slice(0, 2).toUpperCase()}</span>
          <code title={row.user_id}>{row.user_id}</code>
          {onRoleChange ? (
            <select value={row.role} onChange={(event) => onRoleChange(row, event.target.value)}>
              <option value="project_owner">项目所有者</option>
              <option value="reviewer">审稿人</option>
              <option value="viewer">只读成员</option>
            </select>
          ) : <span className="member-role">组织管理员</span>}
          {onRoleChange && row.status !== "revoked" && (
            <button className="icon-button" type="button" title="撤销成员" aria-label="撤销成员" onClick={() => onRoleChange(row, row.role, "revoked")}>
              <X size={14} />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function ModelSettingsDrawer({
  open,
  initial,
  onClose,
  onSaved,
}: {
  open: boolean;
  initial: Bootstrap["model_config"];
  onClose: () => void;
  onSaved: (settings: ModelSettingsView) => void;
}) {
  const [provider, setProvider] = useState<ModelSettingsView["provider"]>(
    initial.provider === "openai-compatible" ? "openai-compatible" : "openai-codex",
  );
  const [baseUrl, setBaseUrl] = useState(initial.base_url || "");
  const [model, setModel] = useState(initial.model || "");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyConfigured, setApiKeyConfigured] = useState(
    initial.api_key_configured === true || initial.api_key_configured === "true",
  );
  const [apiKeyHint, setApiKeyHint] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    fetch("/api/model-settings", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(apiErrorMessage(payload, "无法读取模型设置"));
        return payload.settings as ModelSettingsView;
      })
      .then((settings) => {
        setProvider(settings.provider === "openai-compatible" ? "openai-compatible" : "openai-codex");
        setBaseUrl(settings.base_url || "");
        setModel(settings.model || "");
        setApiKeyConfigured(Boolean(settings.api_key_configured));
        setApiKeyHint(settings.api_key_hint || "");
        setApiKey("");
        setError("");
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "无法读取模型设置"));
  }, [open]);

  const pullModels = useCallback(async (silent = false) => {
    if (!baseUrl.trim()) {
      if (!silent) setError("请先填写 Base URL。");
      return;
    }
    if (!apiKey.trim() && !apiKeyConfigured) {
      if (!silent) setError("请先填写 API Key。");
      return;
    }
    if (!silent) setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/model-settings/discover", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": cookieValue("peerassist_csrf"),
        },
        body: JSON.stringify({
          provider,
          base_url: baseUrl.trim(),
          model,
          api_key: apiKey,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(payload, "模型列表拉取失败"));
      const nextModels = Array.isArray(payload.models) ? payload.models.map(String) : [];
      setModels(nextModels);
      if (!model && nextModels.length) setModel(nextModels[0]);
    } catch (cause) {
      setModels([]);
      setError(cause instanceof Error ? cause.message : "模型列表拉取失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [apiKey, apiKeyConfigured, baseUrl, model, provider]);

  useEffect(() => {
    if (!open || !baseUrl.trim() || (!apiKey.trim() && !apiKeyConfigured)) return;
    const timer = window.setTimeout(() => void pullModels(true), 650);
    return () => window.clearTimeout(timer);
  }, [apiKey, apiKeyConfigured, baseUrl, open, provider, pullModels]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const response = await fetch("/api/model-settings", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": cookieValue("peerassist_csrf"),
        },
        body: JSON.stringify({
          provider,
          base_url: baseUrl.trim(),
          model: model.trim(),
          api_key: apiKey,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(payload, "模型设置保存失败"));
      const settings = payload.settings as ModelSettingsView;
      setModels(Array.isArray(payload.models) ? payload.models.map(String) : models);
      setApiKey("");
      setApiKeyConfigured(Boolean(settings.api_key_configured));
      setApiKeyHint(settings.api_key_hint || "");
      onSaved(settings);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "模型设置保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (!open) return null;
  const modelOptions = Array.from(new Set([model, ...models].filter(Boolean)));
  return (
    <div className="settings-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="settings-drawer" role="dialog" aria-modal="true" aria-labelledby="model-settings-title">
        <header className="settings-head">
          <div>
            <p className="eyebrow">运行配置</p>
            <h2 id="model-settings-title">模型设置</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} title="关闭模型设置" aria-label="关闭模型设置">
            <X size={18} />
          </button>
        </header>
        <form className="settings-form" onSubmit={save}>
          <fieldset className="settings-fieldset">
            <legend>接口模式</legend>
            <div className="segmented-control">
              <button type="button" aria-pressed={provider === "openai-codex"} onClick={() => setProvider("openai-codex")}>
                Responses
              </button>
              <button type="button" aria-pressed={provider === "openai-compatible"} onClick={() => setProvider("openai-compatible")}>
                Chat Completions
              </button>
            </div>
          </fieldset>
          <label className="settings-field">
            <span>Base URL</span>
            <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://provider.example/v1" required />
          </label>
          <label className="settings-field">
            <span>API Key</span>
            <input
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={apiKeyConfigured ? apiKeyHint || "已配置，留空保持不变" : "sk-..."}
              autoComplete="new-password"
            />
          </label>
          <label className="settings-field">
            <span>模型</span>
            {modelOptions.length ? (
              <select value={model} onChange={(event) => setModel(event.target.value)} required>
                {modelOptions.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            ) : (
              <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="选择或输入模型" required />
            )}
          </label>
          <div className="model-discovery-row">
            <span className={apiKeyConfigured ? "connection-state ready" : "connection-state"}>
              {apiKeyConfigured ? "凭据已配置" : "等待凭据"}
            </span>
            <button className="ghost-button" type="button" disabled={loading} onClick={() => void pullModels()}>
              <RefreshCw size={15} className={loading ? "spin" : ""} />
              {loading ? "正在拉取" : "自动拉取模型"}
            </button>
          </div>
          {models.length > 0 && <p className="settings-result">已发现 {models.length} 个模型</p>}
          {error && <p className="settings-error" role="alert">{error}</p>}
          <footer className="settings-actions">
            <button className="ghost-button" type="button" onClick={onClose}>取消</button>
            <button className="primary-button" type="submit" disabled={saving || !model.trim()}>
              {saving ? <Loader2 size={16} className="spin" /> : <ShieldCheck size={16} />}
              {saving ? "正在保存" : "保存并启用"}
            </button>
          </footer>
        </form>
      </aside>
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
  reviewStatus,
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
  reviewStatus?: string;
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
  const [inspectorOpen, setInspectorOpen] = useState(() => window.innerWidth >= 1180);
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
                {busy ? <Loader2 className="spin" size={16} /> : <Bot size={16} />} {linkedReviewJob ? paperReviewStatusLabel(reviewStatus || "") : "快速审阅全文"}
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
        <button className="inspector-reopen" type="button" title="打开证据与意见" aria-label="打开证据与意见" onClick={() => setInspectorOpen(true)}>
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
  modelEnabled,
  baseUrl,
  agentRuns,
  streamLines,
  lastReviewDraft,
  draftStorageKey,
  onDraftChange,
  reviewJobs,
  onRunReview,
  onJobAction,
  onUploadPaper,
  onOpenPaper,
}: {
  busy: boolean;
  model: string;
  modelEnabled: boolean;
  baseUrl: string;
  agentRuns: AgentRun[];
  streamLines: string[];
  lastReviewDraft: string;
  draftStorageKey: string;
  onDraftChange: (draft: string) => void;
  reviewJobs: ReviewJob[];
  onRunReview: (selectedText?: string, reviewMode?: string) => void;
  onJobAction: (job: ReviewJob, action: "cancel" | "retry" | "consent" | "finalize" | "delete") => void;
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
          <h2>我的论文</h2>
          <span className="tag">{reviewJobs.length} 个审稿项目</span>
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
          <h2>生成审稿意见</h2>
          <span className="tag good">{model || "模型未配置"}</span>
        </div>
        <div className="panel-body review-run-box">
          <p className="muted">系统会结合论文证据和检查结果，生成可追溯的中文审稿意见。</p>
          <div className="segmented">
            {["fast", "standard", "deep"].map((item) => (
              <button key={item} type="button" aria-pressed={mode === item} onClick={() => setMode(item)}>
                {labelMode(item)}
              </button>
            ))}
          </div>
          <button className="primary-button wide" type="button" disabled={busy || !modelEnabled} onClick={() => onRunReview("", mode)}>
            {busy ? <Loader2 className="spin" size={16} /> : <Bot size={16} />} {modelEnabled ? "开始全篇智能审稿" : "模型未启用"}
          </button>
          <div className="model-box">
            <code>{baseUrl || "未配置 Base URL"}</code>
          </div>
        </div>
      </div>
      <div className="panel">
        <div className="panel-head">
          <h2>处理进度</h2>
          <span className="tag">实时进度</span>
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
          <h2>可编辑的审稿草稿</h2>
          <span className="tag">审稿草稿</span>
        </div>
        <ReviewDraftEditor draft={lastReviewDraft} storageKey={draftStorageKey} onChange={onDraftChange} />
      </div>
      <div className="panel full-span">
        <div className="panel-head">
          <h2>检查结果</h2>
          <span className="tag">{agentRuns.length} 项检查</span>
        </div>
        <div className="panel-body agent-list">
          {agentRuns.map((run) => (
            <div className="agent-card" key={run.agent_id}>
              <div className="agent-card-head">
                <span className={`agent-state-dot ${run.status === "completed" ? "good" : run.status === "failed" ? "danger" : "warn"}`} />
                <div>
                  <h3>{labelAgent(run.agent_id || "")}</h3>
                  <span>{run.metadata?.responsibility || agentResponsibility(run.agent_id || "")}</span>
                </div>
              </div>
              <div className="agent-metrics">
                <span><strong>{run.metadata?.evidence_count || 0}</strong> 条证据</span>
                <span><strong>{run.draft_count || 0}</strong> 个发现</span>
                <span><strong>{labelModelStatus(run.metadata?.model_status || "not_requested")}</strong> 推理</span>
              </div>
              <div className="tag-row">
                <span className={`tag ${run.status === "completed" ? "good" : "warn"}`}>{labelAgentStatus(run.status || "")}</span>
                <span className="tag">{labelReviewEngine(run.metadata?.review_engine || "local_evidence_rules")}</span>
                {Boolean(run.metadata?.model_usage?.total_tokens) && <span className="tag">{run.metadata?.model_usage?.total_tokens} tokens</span>}
              </div>
              <p className={`agent-note ${(run.warnings || []).length ? "warn" : ""}`}>
                {(run.warnings || []).map(labelAgentWarning).join("；") || (run.draft_count ? "候选问题已进入人工确认队列。" : "本轮证据范围内未生成候选问题。")}
              </p>
            </div>
          ))}
          {!agentRuns.length && <div className="draft-empty">论文解析和证据检查完成后，这里会显示检查结果。</div>}
        </div>
      </div>
    </section>
  );
}

const reviewStages = ["queued", "prepare", "validate", "parse", "evidence", "profile", "plan", "deterministic", "citation", "agents", "integrate", "report", "await_confirmation", "finalize", "completed", "complete"];

function ReviewJobCard({ job, busy, onAction, onOpenPaper }: { job: ReviewJob; busy: boolean; onAction: (job: ReviewJob, action: "cancel" | "retry" | "consent" | "finalize" | "delete") => void; onOpenPaper: (job: ReviewJob) => void }) {
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
          {job.platform && ["failed", "cancelled"].includes(job.status) && <button className="danger-button" disabled={busy} type="button" onClick={() => onAction(job, "delete")}>删除记录</button>}
          {(job.status === "awaiting_human_confirmation" || (job.status === "blocked" && job.stage === "finalize")) && <button className="primary-button" disabled={busy} type="button" onClick={() => onAction(job, "finalize")}>确认并完成报告</button>}
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

function ReviewDraftEditor({
  draft,
  storageKey,
  onChange,
}: {
  draft: string;
  storageKey: string;
  onChange: (draft: string) => void;
}) {
  const cleanDraft = draft.trim();
  if (!cleanDraft) {
    return (
      <div className="panel-body draft-empty">
        启动智能审稿后，完整草稿会显示在这里；实时数据流只保留状态事件和简短摘要。
      </div>
    );
  }
  return (
    <div className="draft-editor">
      <div className="draft-toolbar">
        <span className="draft-save-note">自动保存在当前浏览器 · {storageKey.endsWith("local") ? "当前草稿" : "当前任务"}</span>
        <div className="button-row">
          <button
            className="ghost-button"
            type="button"
            title="复制审稿草稿"
            onClick={() => {
              const copy = navigator.clipboard?.writeText
                ? navigator.clipboard.writeText(draft)
                : Promise.resolve().then(() => {
                  const input = document.createElement("textarea");
                  input.value = draft;
                  input.setAttribute("readonly", "true");
                  input.style.position = "fixed";
                  input.style.opacity = "0";
                  document.body.appendChild(input);
                  input.select();
                  document.execCommand("copy");
                  input.remove();
                });
              void copy.catch(() => undefined);
            }}
          ><Copy size={15} /> 复制</button>
          <button
            className="primary-button"
            type="button"
            title="下载 Markdown 草稿"
            onClick={() => {
              const blob = new Blob([draft.endsWith("\n") ? draft : `${draft}\n`], { type: "text/markdown;charset=utf-8" });
              const url = URL.createObjectURL(blob);
              const link = document.createElement("a");
              link.href = url;
              link.download = "peerassist-review-draft.md";
              link.click();
              URL.revokeObjectURL(url);
            }}
          ><Download size={15} /> 下载 Markdown</button>
        </div>
      </div>
      <textarea
        className="draft-editor-input"
        aria-label="审稿草稿"
        value={draft}
        onChange={(event) => onChange(event.target.value)}
        spellCheck={false}
      />
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
          <h2>待处理意见</h2>
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
          <h2>处理记录</h2>
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
    admin: <Users size={17} />,
    login: <LogIn size={17} />,
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

function labelAgent(value: string) {
  return ({
    structure_agent: "结构与论证代理",
    methodology_agent: "方法学代理",
    experiment_agent: "实验设计代理",
    statistics_agent: "统计核查代理",
    citation_agent: "引用核查代理",
    ethics_agent: "伦理与风险代理",
    reproducibility_agent: "可复现性代理",
    figure_table_agent: "图表核查代理",
    defense_agent: "反方解释代理",
    integrator_agent: "意见整合代理",
    novelty_agent: "创新性代理",
  } as Record<string, string>)[value] || value || "未命名代理";
}

function agentResponsibility(value: string) {
  return ({
    structure_agent: "研究问题、贡献、论证结构与结论边界",
    methodology_agent: "方法假设、适用条件与核心主张支撑",
    experiment_agent: "数据、基线、指标、消融与实验覆盖",
    statistics_agent: "样本量、不确定性、显著性与效应报告",
    citation_agent: "引用链接、文献字段与正文主张支持关系",
    ethics_agent: "数据来源、隐私、安全、偏差与伦理披露",
    reproducibility_agent: "代码、配置、随机性、依赖与复现实验条件",
    figure_table_agent: "图表标签、正文引用与数值一致性",
    defense_agent: "为候选问题寻找合理的善意解释",
    integrator_agent: "合并重复发现并保留代理来源",
  } as Record<string, string>)[value] || "等待专业审稿范围信息";
}

function labelAgentStatus(value: string) {
  return ({ completed: "已完成", incomplete: "需人工补充", failed: "运行失败" } as Record<string, string>)[value] || value || "等待运行";
}

function labelAgentWarning(value: string) {
  return ({
    "No figure/table deterministic leads found.": "未发现需要升级的图表确定性线索。",
    "No deterministic leads to pressure-test.": "没有需要反方压力测试的确定性线索。",
  } as Record<string, string>)[value] || value;
}

function labelModelStatus(value: string) {
  return ({
    completed: "模型增强",
    unavailable: "本地",
    failed: "降级",
    not_requested: "本地",
  } as Record<string, string>)[value] || "本地";
}

function labelReviewEngine(value: string) {
  return ({
    local_evidence_rules: "证据规则",
    local_rules_plus_batched_model: "规则 + 模型批审",
    batched_model_review: "模型批审",
  } as Record<string, string>)[value] || value;
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

function paperReviewStatusLabel(value: string) {
  return ({
    queued: "审稿任务排队中",
    validating_input: "正在校验论文",
    parsing: "正在解析论文",
    evidence_building: "正在建立证据",
    profiling: "正在理解论文",
    planning_review: "正在规划审稿",
    deterministic_checking: "正在核查论文",
    citation_checking: "正在核查引用",
    agents_running: "正在生成审稿意见",
    integrating: "正在整合审稿意见",
    blocked: "等待继续审稿",
    awaiting_human_confirmation: "等待你确认意见",
    exporting_report: "正在导出报告",
    completed: "审稿已完成",
    failed: "审稿失败，请到任务页重试",
    cancelled: "任务已取消，可重新审稿",
    interrupted: "任务等待恢复",
  } as Record<string, string>)[value] || "后台任务处理中";
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
    delete: "失败记录已删除",
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
