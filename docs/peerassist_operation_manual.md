# PeerAssist 论文审核辅助系统操作手册

本文档面向系统维护者和审稿使用者，说明 PeerAssist 的启动、访问、审稿流程、人工确认、产物导出和常见故障处理。文档只记录运行方法和占位配置，不保存任何真实 API Key、GitHub token 或审稿私密材料。

## 1. 系统入口

默认工作区监听 `127.0.0.1:8766`。M1 已接入 Keycloak 登录、服务端会话、CSRF、组织/项目/成员管理和租户权限；远程使用仍必须由受信任的反向代理提供 TLS 和请求限制。

登录后，成员管理入口位于 `/admin`，模型设置位于主页顶部。智能审稿页上传 PDF 后会写入 MinIO、创建 PostgreSQL 审阅任务，并由 Worker 生成 `paper_summary.md` 与 `review.md`。论文阅读页会把前者显示为“这篇论文讲了什么”。模型状态只有在模型列表接口验证成功后才显示可用；仅保存过失效凭据时会显示“连接失败”。

主要页面：

| 路径 | 用途 |
| --- | --- |
| `/`、`/paper` | PDF 原文阅读与批注式审稿入口 |
| `/agent` | 智能审稿、模型配置状态、审稿草稿预览 |
| `/queue` | 证据审稿队列与关注点处理 |
| `/trace` | MCP/Skills/工具调用追踪 |
| `/confirm` | 人工逐条确认入口 |
| `/artifacts` | 产物导出与文件路径查看 |
| `/legacy` | 历史兼容入口，使用 `308` 自动跳转到 `/paper` |
| `/paper.pdf` | 当前 run 绑定的原始 PDF |

## 2. 本地目录与关键文件

| 路径 | 说明 |
| --- | --- |
| `src/peerassist/confirmation_server.py` | PeerAssist 审稿工作台后端服务 |
| `src/peerassist/confirmation_cli.py` | 人工确认命令行工具 |
| `web/peerassist-workspace` | React + TypeScript 前端源码 |
| `web/peerassist-workspace/dist` | 前端生产构建产物，后端以 `/workspace/` 静态路径提供 |
| `/var/lib/peerassist/workspace/run` | systemd 示例使用的工作区 run 目录 |
| `docs/peerassist_lark_sync.md` | 飞书同步文档固定入口和本地追加记录 |
| `deploy/nginx/peerassist-subdomains.conf` | 子域名/前后端分离部署示例 |

PeerAssist 产物位于所选 run 的阶段目录：

```text
<run-dir>/stages/peerassist/
```

常用产物：

| 文件 | 说明 |
| --- | --- |
| `evidence_ledger.json` | 证据台账 |
| `deterministic_checks.json` | 确定性核查结果 |
| `agent_results.json` | 多代理审稿结果 |
| `peerassist_concerns.json` | 系统生成的审稿关注点 |
| `confirmation_review_queue.json` | 待人工确认队列 |
| `human_confirmations.json` | 人工确认动作记录 |
| `tool_trace.jsonl` | MCP/Skills/工具调用追踪 |
| `citation_audit.json` | 正文引用、参考文献、核验状态和可追溯 finding |
| `citation_verifications/attempt-*.json` | 不可变的引用核验原始响应快照 |
| `agent_review_draft.md` | 大模型生成的审稿草稿 |
| `peerassist_report.zh.md` | 中文审稿报告 |

## 3. 后端服务启动

从仓库根目录执行：

```bash
PYTHONPATH=src .venv/bin/python -m peerassist.review_job_api \
  --data-dir data \
  --host 127.0.0.1 \
  --port 8767

PYTHONPATH=src \
PEERASSIST_REVIEW_API_URL=http://127.0.0.1:8767 \
.venv/bin/python -m peerassist.confirmation_server \
  --run-dir <run-dir> \
  --paper-id <paper-id> \
  --host 127.0.0.1 \
  --port 8766
```

生产环境示例固定安装到 `/opt/peerassist`，Review API 和工作区分别以无登录权限的 `peerassist-api`、`peerassist-ui` 身份运行，并将各自可写数据隔离在 `/var/lib/peerassist/data` 与 `/var/lib/peerassist/workspace`。`peerassist` 仅作为读取配置文件的共享组。先创建环境文件；仓库中的 `peerassist.env.example` 不含密钥：

```bash
sudo groupadd --system peerassist
sudo useradd --system --user-group --groups peerassist \
  --home-dir /nonexistent --shell /usr/sbin/nologin peerassist-api
sudo useradd --system --user-group --groups peerassist \
  --home-dir /nonexistent --shell /usr/sbin/nologin peerassist-ui
sudo install -d -m 0755 /opt/peerassist
sudo install -d -m 0750 -o peerassist-api -g peerassist-api \
  /var/lib/peerassist/data
sudo install -d -m 0750 -o peerassist-ui -g peerassist-ui \
  /var/lib/peerassist/workspace/data /var/lib/peerassist/workspace/run
# 将干净的已验证版本及其运行依赖安装到 /opt/peerassist。
sudo install -d -m 0750 -o root -g peerassist /etc/peerassist
sudo install -m 0640 -o root -g peerassist \
  deploy/systemd/peerassist.env.example /etc/peerassist/peerassist.env
sudo install -m 0644 deploy/systemd/peerassist-review-api.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/peerassist-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now peerassist-review-api.service peerassist-ui.service
systemctl status peerassist-review-api.service peerassist-ui.service
```

两个服务都只监听回环地址：`8767` 是私有 Review Job API，`8766` 是工作区入口。不要为任一端口配置公网防火墙放行规则；反向代理与 PeerAssist 同机部署并代理到 `http://127.0.0.1:8766`。当前服务没有内置认证，反向代理认证不是可选项。

交付前静态检查 unit；在不会覆盖已有系统资源的专用 systemd 测试机上运行实际启动 smoke：

```bash
systemd-analyze verify /etc/systemd/system/peerassist-review-api.service \
  /etc/systemd/system/peerassist-ui.service
sudo bash deploy/systemd/smoke_systemd.sh
```

实际 smoke 会安装并启动交付的两个 unit 本身；如果目标用户、目录、unit 或端口已存在，它会拒绝执行。

健康检查：

```bash
curl -I http://127.0.0.1:8766/
curl -I http://127.0.0.1:8766/paper.pdf
curl -I -H 'Range: bytes=0-1023' http://127.0.0.1:8766/paper.pdf
curl http://127.0.0.1:8766/api/state
curl http://127.0.0.1:8766/api/health
```

## 4. 前端开发与构建

前端源码已经上传到仓库：

```text
web/peerassist-workspace/
  src/main.tsx
  src/styles.css
  package.json
  vite.config.ts
```

开发启动：

```bash
cd web/peerassist-workspace
npm install
npm run dev
```

Vite 开发服务会将 `/api`、`/paper.pdf`、`/legacy` 代理到 `http://127.0.0.1:8766`。因此联调时需要先启动后端服务，再打开 Vite 输出的开发地址。

生产构建：

```bash
cd web/peerassist-workspace
npm run build
```

构建产物写入 `web/peerassist-workspace/dist`。后端服务会把这些文件挂载在 `/workspace/` 路径下，`/`、`/paper`、`/agent` 等页面会加载同一个前端应用。

## 5. 审稿操作流程

1. 本机打开 `http://127.0.0.1:8766/`，或通过已配置认证和 TLS 的反向代理地址进入论文阅读窗口。
2. 在大尺寸 PDF 阅读区浏览原文。阅读器支持单页/双页、适应宽度、放大缩小、页码输入、翻页、下载和在新窗口打开原始 PDF。
3. 拖选 PDF 文字后，原文和页码会自动进入右侧“审稿助手”；可直接发起选区智能审稿，或补充人工批注后加入证据队列。页边消息图标表示绑定到当前页的 concern，点击可反向打开对应关注卡片。
4. 右侧审稿栏可拖动左侧边缘调整宽度，也可通过右上角图标收起；收起后 PDF 自动扩展到可用宽度。
5. 右侧“引用核查”标签显示当前任务的正文引用、参考文献、外部核验来源和字段差异。点击定位按钮可在 PDF 与引用卡片之间跳转。
6. 点击“快速审阅全文”或进入“智能审稿”窗口后，系统读取证据台账、确定性核查和多代理结果，调用配置的大模型生成审稿草稿。
7. 在“审稿草稿预览”查看完整模型输出。实时数据流只显示阶段状态，不承载完整报告。
8. 在“证据队列”逐条查看系统提出的 concern、证据来源、严重度和建议处理方式。
9. 在“人工确认”中对每条 concern 执行确认、改写、降级、删除或标记待定；引用 concern 也可以直接在引用卡片中处理。
10. 在“工具追踪”查看每次 MCP/Skills/确定性检查/代理调用的状态、时间和产物 ID，在“产物导出”在线查看或下载最终报告。

### 5.1 PDF 加载与缓存

- 工具栏的单页和双页图标用于切换阅读布局。双页模式按封面单页、随后 `2–3、4–5…` 排列；上一页/下一页按跨页组移动，直接输入页码或点击证据仍精确聚焦目标页。
- 每个可见 PDF 页拥有独立 canvas 和文字层。在双页模式中拖选右页文字时，审稿助手保存右页页码；concern 或引用跳转只在目标页显示 bbox/文字高亮。
- 每个 concern 在同一页最多显示一个页边标记，并优先使用带 bbox 的 evidence 计算纵向位置；相邻标记自动保持最小间距。点击标记后切换到“本页关注”，目标 concern 排到首位并显示选中态。
- “本页关注”卡片的证据链接用于侧栏到原文的正向跳转，PDF 页边标记用于原文到 concern 的反向跳转。两者复用同一个 evidence ID、页码和 bbox。
- 阅读区宽度不足 `860px` 时自动使用单页模式，保留桌面双页偏好但不制造移动端横向溢出。
- `/paper.pdf` 支持 HTTP Range，PDF.js 可以按 `64 KB` 分段读取；合法分段请求返回 `206 Partial Content`。
- PDF 响应包含 `Accept-Ranges`、ETag、Last-Modified 和一小时私有缓存。
- 带哈希的前端 JS、CSS 和 PDF worker 使用一年不可变缓存；HTML 和 API 状态保持不缓存或协商更新。
- PDF worker 使用压缩构建，生产文件约 `1.25 MB`；当前页渲染完成后会预取相邻页的绘制数据。
- 加载阶段显示百分比，失败时显示错误原因和“重新加载”入口。

### 5.2 引用核查

PeerAssist 会从论文正文中提取 `[1]`、`[1, 3-5]` 等数字引用，并与参考文献编号建立确定性关联。引用 concern 至少回指正文引用位置；元数据差异还会同时回指参考文献条目和核验记录。

网页操作：

1. 在“论文阅读”窗口右侧选择“引用核查”。顶部四项统计分别表示参考文献、正文引用、外部核验和待核问题数量。
2. 点击“定位正文”，PDF 跳到引用页并以黄色 bbox/文字层高亮正文引用；点击“查看参考文献原文”，跳到参考文献页并高亮对应条目。
3. “外部核验”区域展示来源名称、可打开的来源 URL 和逐字段差异。界面不会暴露原始查询、私有响应快照路径或内部缓存路径。
4. 引用 finding 已绑定人工 concern 时，可在卡片底部确认、改写、降级或删除。操作成功后状态与待确认计数会按任务确认版本刷新。
5. 作者年份制或其他未支持标记会显示“解析提示”。没有建立链接不等于引用有效或无问题，审稿人仍需人工检查。

常见状态：

| 状态 | 含义 | 审稿人操作 |
| --- | --- | --- |
| `verified` | 已唯一关联，至少两个可比字段一致 | 通常无需生成 concern |
| `metadata_mismatch` | DOI、题名或年份存在可复现差异 | 查看原文与核验快照后确认或改写 |
| `missing_reference` | 正文编号未找到对应参考文献条目 | 检查解析和论文编号后要求作者澄清 |
| `ambiguous` | 编号或外部候选不能唯一确定 | 保持待定并人工选择正确条目 |
| `not_found` | 当前核验来源没有找到候选 | 不能据此认定文献不存在，需人工检索 |
| `insufficient_evidence` | 可比较字段不足或核验服务不可用 | 保持待人工核查 |
| `verification_failed` | 适配器、响应或文件核验失败 | 查看 `tool_trace.jsonl` 和错误码后重试 |

引用核查历史与人工动作通过 finding ID 和 audit/parse 版本关联。论文重新解析后，如果原 finding 消失或证据版本变化，系统会标记 `needs_reconciliation`，不会静默沿用旧确认。

### 5.3 后台任务时间线

“智能审稿”窗口的每个 ReviewJob 卡片显示当前阶段、状态版本和最新事件。展开“运行时间线”可查看最近 8 条事件；服务端保存最近 24 条前端视图，并保留完整 JSONL 事件日志。

时间线包含阶段开始/完成、阶段耗时、模型授权、取消请求、取消完成、重试 attempt 和报告导出事件。旧版本 `stage_completed` 曾记录下一阶段，API 会结合相同 attempt 的 `stage_started` 归一化为实际完成阶段；新事件直接写入正确阶段。

公网时间线是脱敏视图，不返回原始 event payload、授权 actor、内部路径或私有产物内容。失败详情继续使用任务级受控错误字段和工具追踪查看。

操作规则：

1. 运行中或等待授权的任务可点击“取消”。取消请求先持久化为 `cancel_requested`，随后 worker 写入 `job_cancelled` 终态。
2. 已失败、已取消或中断任务可点击“重试”。系统生成新 attempt ID，但保留旧 attempt 的事件和产物用于追踪。
3. 页面每 3 秒刷新任务状态；浏览器刷新或服务重启后从仓储重新读取时间线，不依赖前端内存。
4. “实时数据流”用于当前页面的短状态提示；ReviewJob 时间线才是任务恢复和审计的持久依据。

### 5.4 专业代理与模型降级

打开“智能审稿”窗口，在“多代理结果”中查看结构、方法、实验、统计、引用、伦理、复现、图表、反方和整合代理。每张代理卡显示：

- 本轮读取的证据数量。
- 进入人工队列的候选问题数量。
- 代理职责与完成状态。
- 使用“证据规则”还是“规则 + 模型批审”。
- 模型是否完成、不可用或失败降级；模型完成时显示 token 用量。

模型配置只允许通过服务运行环境注入。`/api/bootstrap` 中 `api_key_configured=false` 时，页面显示 `模型名（未启用）`，独立模型审稿按钮不可点击；ReviewJob 获得授权后仍会继续本地专业代理，不会发起外部请求。

快速模式固定使用最多 `40` 个证据块、`12,000` 字符证据正文、`24,000` 字符完整序列化上下文和默认 `900` 输出 tokens。需要进一步降低费用时，可在服务运行环境中设置：

```bash
PEERASSIST_REVIEW_MAX_TOKENS=600
```

允许范围为 `256-1200`。不要把 API Key 写入该命令、仓库文档、飞书或 Git remote；密钥应由 systemd 凭据、受控环境注入或其他密钥管理设施提供。

判断本轮是否发生模型调用：

1. 代理卡显示“模型增强”，并展示 token 数。
2. `agent_results.json` 的 `model_enhancement.status` 为 `completed`。
3. `tool_trace.jsonl` 存在 `tool=chat_completions` 的开始和完成/失败事件。

若显示“本地推理”，只代表本地证据规则和确定性核查已经运行，不能宣称已经完成大模型专业审稿。

## 6. 人工确认动作

网页会调用 `/api/decision` 写入人工动作；也可以使用命令行：

```bash
PYTHONPATH=src .venv/bin/python -m peerassist.confirmation_cli \
  --run-dir runs/arxiv_real_data/runs/arxiv_2607_08522_v1 \
  --paper-id arxiv_2607_08522_v1 \
  --concern-id "<concern-id>" \
  --action confirm \
  --timestamp "2026-07-11T12:00:00+08:00" \
  --reviewer-id "local-reviewer"
```

查看当前确认状态：

```bash
PYTHONPATH=src .venv/bin/python -m peerassist.confirmation_cli \
  --run-dir runs/arxiv_real_data/runs/arxiv_2607_08522_v1 \
  --state
```

动作含义：

| action | 含义 |
| --- | --- |
| `confirm` | 接受该 concern，可进入最终审稿意见 |
| `rewrite` | 人工改写 concern 文本 |
| `downgrade` | 降低严重度或从核心意见转为次要建议 |
| `delete` | 删除不成立或无证据支撑的 concern |
| `mark_pending` | 暂不决定，后续复核 |

## 7. API 速查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/bootstrap` | 前端启动所需的完整 bootstrap，包括 state、路径、模型配置状态 |
| `GET` | `/api/state` | 当前确认队列、追踪事件、产物路径和统计指标 |
| `GET` | `/api/events` | SSE 事件流，包含 state、heartbeat、done |
| `GET` | `/api/jobs` | 后台任务列表与脱敏持久时间线 |
| `GET` | `/api/jobs/<job-id>` | 单任务状态、attempt、阶段耗时与最近事件 |
| `POST` | `/api/jobs/<job-id>/cancel` | 请求取消并推进到取消终态 |
| `POST` | `/api/jobs/<job-id>/retry` | 新 attempt 重试可恢复任务 |
| `POST` | `/api/agent-review` | 触发智能审稿，输入选中文本和模式，输出审稿草稿与新增 concern |
| `POST` | `/api/manual-concern` | 将 PDF 选中文本保存为人工 concern |
| `POST` | `/api/decision` | 写入人工确认动作 |
| `GET` | `/paper.pdf` | 返回当前 run 的原始 PDF，支持 Range、ETag 和缓存 |

## 8. 飞书同步规则

飞书文档固定入口见 `docs/peerassist_lark_sync.md`。同步时遵循以下规则：

- 只追加新章节，禁止覆盖整篇文档。
- 不写真实 API Key、GitHub token、模型 key 或审稿私密内容。
- 每次同步记录开发内容、验证结果、服务 URL、提交信息和已知风险。
- 如果需要调整历史章节，优先用精确块级编辑，并在本地同步文件记录原因。

## 9. GitHub 提交流程

本仓库建议使用用户身份提交：

```bash
git config user.name "moyuan10086"
git config user.email "moyuan10086@users.noreply.github.com"
```

检查前端是否已被 git 跟踪：

```bash
git ls-files web/peerassist-workspace
```

文档或代码提交前执行：

```bash
git status --short
git diff --check
rg -l --hidden --glob '!runs/**' --glob '!.git/**' --glob '!web/**/node_modules/**' \
  'ghp_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{40,}' . || true
```

不要把 token 写入 remote URL、`.env`、README、飞书文档或 shell 历史。

## 10. 常见故障

**页面能打开但 PDF 不显示。** 先检查 `/paper.pdf` 是否返回 `200`，再用 Range 命令确认是否返回 `206`。如果是 `404`，说明 run 目录里没有发现源 PDF，需确认 `--run-dir` 和 `--paper-id` 是否对应；如果页面显示错误卡片，可先点击“重新加载”，再查看浏览器控制台和服务日志。

**访问 `/legacy` 后页面跳转。** 这是预期行为。旧版服务端页面已停止作为用户入口，`/legacy` 会跳转到统一的现代 `/paper` 工作台。

**点击智能审稿提示 API Key 未配置。** 服务启动环境缺少 `PEERASSIST_OPENAI_API_KEY`。重新启动服务并只通过运行时环境注入，不要写进仓库。

**事件流中断。** 前端会自动切换到 `/api/state` 轮询；如果状态长期不更新，查看 `/tmp/peerassist-confirm-server-8766.log`。

**GitHub 首页显示作者是 Codex。** 这是本地 git author 配置导致的历史提交显示。将仓库 `user.name` 和 `user.email` 改为 GitHub 用户后，新提交会显示为对应用户；已推送的历史提交不会自动改名，除非明确执行历史重写。

**GitHub 语言统计显示 Python 100%。** 语言统计可能延迟，也可能受文件体积和 Linguist 规则影响。前端源码在 `web/peerassist-workspace`，可通过 `git ls-files web/peerassist-workspace` 确认已上传。

## 11. 安全边界

- 不在文档、代码、提交信息、飞书或日志里保存真实密钥。
- 大模型输出必须经过人工逐条确认后才能进入正式审稿意见。
- 无证据新增事实应删除、改写或标记待定，不能直接保留。
- 审稿意见应绑定 PDF 原文、证据台账、确定性核查或工具追踪，不把模型自由发挥当作证据。
