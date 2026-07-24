# PeerAssist 真实论文端到端审稿设计

**状态说明：** 底层 Paper、ReviewJob、检查点、人工确认和不可变产物协议继续有效；教师端导航、默认快速模式、服务端审稿文档、平台模型授权和导出完成语义以 `2026-07-24-peerassist-evidence-review-workspace-positioning-design.md` 为准。

## 1. 目标

把 PeerAssist 从“绑定固定 run 目录的审稿展示页”升级为可上传真实论文、持续执行、刷新恢复并最终进入人工确认与报告导出的审稿系统。

第一里程碑以可复制文本的 PDF 为主要输入，跑通以下闭环：

```text
上传 PDF
→ 解析论文
→ 建立论文画像
→ 提取核心主张与实验清单
→ 建立证据台账
→ 确定性与引用核查
→ 专业代理审核
→ 反方解释与意见整合
→ 人工逐条确认
→ 中英文报告导出
```

Word、LaTeX、补充材料、扫描件 OCR、作者年份制和外部科研诚信数据源作为后续里程碑接入同一任务与产物协议。

## 2. 现状与核心问题

当前系统已经具备 PDF.js 阅读器、证据台账、确定性检查、数字型引用核查、人工确认、报告产物和工具追踪，但存在四个结构性问题：

1. 服务启动时固定绑定一个 `run_dir` 和 `paper_id`，网页没有真实论文上传与论文切换入口。
2. 智能审稿按钮只发起一次整合模型调用，其他代理结果来自旧产物，不是本次任务的真实专业代理执行。
3. 模型上下文按证据台账顺序截取前 80 条，缺少论文画像、核心主张、实验清单和重要性排序。
4. 当前 SSE 只返回瞬时状态快照，没有持久化后台任务、取消、阶段重试和服务重启恢复。

## 3. 架构决策

### 3.1 Paper 与 ReviewJob 的目录所有权

第一里程碑不新增 Redis、Celery 或数据库依赖，但会把现有 `common.storage` 升级为明确的 Paper 与 ReviewJob 两层协议：

```text
<DATA_DIR>/papers/<paper_id>/
  paper.json
  source/
    source.pdf

<DATA_DIR>/jobs/<job_uuid>/
  job.json
  events.jsonl
  run/
    _full_pipeline_context.json
    inputs/source_pdf/
    stages/
      parse/
      peerassist/
      ...
  attempts/<attempt_id>/
    stages/<stage>/
      checkpoint.json
      outputs/
  reports/<report_version>/
```

`paper_id` 的规范身份是源文件完整 SHA-256；界面可以显示前 16 位短 ID，但仓储、API 和冲突判断始终保存完整摘要。同一论文可以拥有多个 ReviewJob。ReviewJob UUID 是一次审稿任务的身份；`job.json` 是当前状态的权威快照，`events.jsonl` 是只追加事件历史，`job/run` 是该任务唯一的流水线目录。

`pipeline_full` 增加预分配 `run_dir` 的入口。后台任务禁止调用会重新生成 `run_id` 的旧入口，恢复时必须继续使用 `job.run_dir`。MinerU 等解析服务自己的外部 `job_id` 只作为 `metadata.external_parse_job_id` 保存，不能替代 PeerAssist ReviewJob UUID。旧运行目录通过显式 import 生成 Paper/ReviewJob 映射，不进行隐式迁移。

未来需要多实例并发时，可以在不改变 HTTP API 和产物协议的前提下，将 Repository 实现替换为 SQLite/PostgreSQL，将 Worker 替换为独立队列消费者。

### 3.2 文件仓储并发协议

- 每个 job 使用基于 `fcntl.flock` 的跨进程锁；Windows 后续实现同接口锁适配器。
- `job.json` 包含单调递增 `revision`，更新使用 compare-and-swap，版本不匹配返回冲突并重新读取。
- 原子写入使用包含 UUID 的唯一临时文件，`fsync` 后再 `replace`，不复用共享 `.tmp`。
- 事件在 job 锁内分配单调 `event_id` 后追加；读取时忽略最后一个未完整换行的损坏尾记录并报告恢复警告。
- SSE 使用 `id: <event_id>`，接受 `Last-Event-ID`，先回放缺失事件再进入心跳。

### 3.3 组件边界

新增模块按职责拆分：

| 模块 | 职责 |
| --- | --- |
| `peerassist/job_repository.py` | 创建、读取、列出、更新任务，追加事件，维护取消标记和检查点 |
| `peerassist/job_runner.py` | 后台执行阶段、恢复中断任务、取消检查、失败重试和产物登记 |
| `peerassist/paper_profile.py` | 从解析产物和证据台账生成论文画像、核心主张、实验清单和审稿计划 |
| `peerassist/professional_agents.py` | 调度结构、方法、实验、统计、图表、引用、伦理、复现、反方和整合代理 |
| `peerassist/upload.py` | 校验文件名、MIME、PDF 头、大小限制并安全落盘 |
| `confirmation_server.py` | HTTP 路由、上传流式读取、任务控制、状态与 SSE 输出，不承载阶段业务逻辑 |

现有 `stage_runner.py` 保留为 PeerAssist 阶段实现；`job_runner.py` 负责把完整流水线和前端任务状态连接起来。

## 4. 任务状态机与阶段 DAG

扩展 `JobStatus`，使用稳定的阶段代码和独立的终态：

```text
queued
→ validating_input
→ parsing
→ evidence_building
→ profiling
→ planning_review
→ deterministic_checking
→ citation_checking
→ agents_running
→ integrating
→ awaiting_human_confirmation
→ exporting_report
→ completed
```

任一运行阶段可以进入：

- `cancel_requested`：用户已请求取消，Worker 在下一个检查点停止。
- `cancelled`：已安全停止，保留所有已完成产物。
- `failed`：记录失败阶段、错误码、可重试性和最近检查点。
- `interrupted`：服务启动时发现原任务处于运行态，但没有存活 Worker。

解析后先建立带稳定定位符的基础证据台账，再生成论文画像、核心主张、实验清单和审稿计划。任务阶段必须拆成可独立调用的 DAG 节点并保持幂等，不能继续把整个 PeerAssist 阶段视为单个不可恢复函数。

每个阶段检查点包含：

- `schema_version`、stage、attempt ID 和提交时间。
- 输入产物摘要、运行模式、提示/解析器版本。
- 输出产物清单、文件大小和 SHA-256。
- 状态、可重试性、取消观察点和错误码。
- `committed=true` 标志；只有全部产物写入并校验后才能原子提交。

阶段输出必须先写入 `attempts/<attempt_id>/stages/<stage>/outputs/`，不能直接覆盖 `job/run/stages`。校验完成后生成不可变 stage manifest，并在 job 锁内原子切换 `current_stages/<stage>.json` 指针；兼容旧代码所需的 `job/run/stages` 通过 committed manifest 物化或只读链接生成。

检查点记录上游 manifest 哈希。任一上游 committed manifest 变化时，DAG 中所有下游检查点标记 `invalidated`，但不删除旧 attempt；恢复只能复用输入哈希完全一致的 committed 阶段。

模型请求和外部解析请求设置超时；取消请求会终止可控子进程，并在外部请求返回后再次检查。重试从最后一个 committed 检查点继续。

### 4.1 候选审稿与最终报告分离

`integrating` 只生成候选 concerns、确认队列和标记为 `draft` 的审稿预览，然后停止在 `awaiting_human_confirmation`。它不能生成最终报告。

新增 finalize 操作：

1. 冻结当前 confirmation revision。
2. 检查所有 `core` concern 已确认、改写、降级或删除；待定项必须提供人工 override 理由。
3. 将确认动作与 finding revision 固化为报告 manifest。
4. 在 `reports/<version>/` 生成中英文 JSON/Markdown，更新 `current_final_report.json` 指针。

报告分为 `draft` 和 `final`，每次重新核验或重新审稿不会覆盖历史 final 版本。

## 5. 论文认知产物

### 5.1 `paper_profile.json`

包含：

- 标题、摘要、研究领域和论文类型。
- 研究问题与作者声称的贡献。
- 章节结构与每节作用。
- 方法总览、输入输出和关键假设。
- 主要结论及其适用边界。
- 解析警告和无法可靠识别的区域。

每个字段必须携带 `evidence_ids`，无法定位原文时标记 `needs_human_review`。

### 5.2 `claim_graph.json`

每条核心主张包含：

- `claim_id`、主张文本、主张类型和中心性。
- 摘要、引言、结果或结论中的证据位置。
- 支持该主张的方法、数据集、实验、图表和引用 ID。
- 支持状态：`supported`、`partially_supported`、`conflicting`、`insufficient_evidence`。
- 结论边界和可能的善意解释。

### 5.3 `experiment_inventory.json`

记录数据集、样本量、数据划分、基线、指标、随机种子、统计方法、消融实验、关键表格和关键图形。字段值必须关联证据 ID，并区分“论文明确报告”和“系统推断”。

### 5.4 `review_plan.json`

根据核心主张中心性、证据缺口、实验重要性和解析不确定性生成重点阅读路线，并为专业代理分配任务。低重要性写作问题默认折叠，不进入主要意见。

## 6. 专业代理设计

代理共享论文画像和核心主张，但只接收与职责相关的证据切片，避免每个代理重复阅读全部原文。

| 代理 | 核心职责 |
| --- | --- |
| 结构代理 | 研究问题、贡献、章节组织和论证结构 |
| 方法代理 | 方法定义、假设、适用性、对照和方法完整性 |
| 实验代理 | 数据、基线、消融、指标和实验设计 |
| 统计代理 | 样本量、统计方法、不确定性和报告一致性 |
| 图表代理 | 图表与正文结论一致性、标签和可读性 |
| 引用代理 | 引用元数据、相关工作定位和主张支持关系 |
| 伦理代理 | 数据来源、隐私、风险、局限和潜在影响 |
| 复现代理 | 环境、代码、随机种子、参数和复现实验信息 |
| 反方代理 | 为候选问题构造合理解释并检查是否过度推断 |
| 整合代理 | 去重、合并、分级并拒绝无证据或低价值意见 |

每次代理调用必须记录模型、提示版本、输入证据 ID、开始时间、耗时、状态、输出摘要、产物 ID、错误和重试次数。主要 concern 必须包含：

- `affected_claim_ids`
- `importance`: `core`、`supporting`、`minor`
- `evidence_strength`
- `impact`
- `benign_explanation`
- `author_action`

### 6.1 统一 Finding 协议

引用、方法、统计、图表、伦理、复现、规则检查和人工 concern 共用同一 Finding 身份协议：

- `finding_lineage_id` 由生产者命名空间、检查类型、受影响核心主张和规范化问题锚点生成，不包含可变证据内容、严重度或生产者版本。
- `finding_id` 由 `finding_lineage_id + revision content hash` 生成；content hash 包含证据集合、规范化问题语义、严重度和生产者版本。
- 同一问题的证据、措辞、严重度或生产者版本变化时，在同一 lineage 下递增 `revision`；如果核心语义已经变成另一个问题，则创建新 lineage 并通过 `supersedes` 关联。
- `supersedes` 指向被新版本替代的 finding，`reconciles` 记录人工动作如何迁移。
- 人工确认统一绑定 `finding_lineage_id + revision + finding_id`，不能绑定数组下标或本次生成序号。
- 仅措辞变化且证据与核心语义不变时保持 finding ID，通过 display revision 留痕。

里程碑 A 只开放 `fast`，使用论文画像、规则核查和单一证据整合模型，界面明确标记“快速单代理整合”。里程碑 C 完成后，快速模式运行结构、方法、实验、统计、反方与整合代理；标准模式增加图表、引用和伦理；深入模式增加复现和外部核验。在此之前 standard/deep API 返回 `409 mode_unavailable`，不得伪装为已执行。

## 7. HTTP API

### 7.1 论文与任务

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/papers` | `multipart/form-data` 上传 PDF，返回 `paper_id`；`start_review=true` 时同时返回 `job_id` |
| `GET` | `/api/papers` | 返回最近论文和任务摘要 |
| `GET` | `/api/papers/{paper_id}` | 返回论文、当前任务和主要产物状态 |
| `POST` | `/api/papers/{paper_id}/review` | 以 fast/standard/deep 启动或重新启动审稿 |
| `GET` | `/api/jobs/{job_id}` | 返回持久化任务快照和阶段进度 |
| `GET` | `/api/jobs/{job_id}/events` | 返回 SSE 历史增量和心跳 |
| `POST` | `/api/jobs/{job_id}/cancel` | 设置取消请求 |
| `POST` | `/api/jobs/{job_id}/retry` | 从失败或中断检查点重试 |
| `POST` | `/api/jobs/{job_id}/finalize` | 冻结人工确认 revision 并生成版本化最终报告 |
| `DELETE` | `/api/papers/{paper_id}` | 按保留策略删除稿件及其任务，写入审计事件 |

上传限制：第一里程碑只接受 PDF，默认最大 100 MB；文件名不能决定落盘路径；必须流式限额并校验 `%PDF-` 文件头。`POST /review` 支持 Idempotency-Key；同一 paper、模式和 key 返回现有任务，没有 key 且存在非终态任务时返回 `409 review_in_progress`。上传成功不代表已允许把稿件发送给外部 OCR，外部上传仍需要逐任务明确同意。

### 7.2 兼容现有页面

服务不再由启动参数永久绑定唯一论文。`--run-dir/--paper-id` 保留为只读兼容演示模式；正常模式从 `paper_id` 或当前选中论文解析 PDF、状态和产物路径。旧 `JobState` 读取时迁移到 `peerassist.review_job.v2`，补齐 `paper_id`、`run_dir`、模式、当前阶段、revision、checkpoint 和错误码；迁移只更新状态索引，不移动或删除旧产物。

## 8. 前端流程

第一屏增加论文任务栏：

1. 上传或选择已有论文。
2. 选择快速、标准或深入模式。
3. 展示阶段时间线、当前阶段、耗时、工具调用和失败原因。
4. 支持取消、失败重试和页面刷新恢复。
5. profiling 完成后立即展示论文概览，不必等待所有代理结束。
6. evidence/citation 完成后在 PDF 中逐步显示证据和引用高亮。
7. agents/integrating 完成后展示主要意见，人工确认后解锁最终报告。

PDF 工作区继续保持主阅读面，支持单页/双页模式、适应宽度、缩放、文字选择、批注定位、引用卡片及 concern 与原文双向跳转。

## 9. 引用前端化与唯一所有权

`stages/peerassist/citation_audit.json` 是唯一权威引用审计产物。引用 finding 使用 §6.1 的统一 Finding 命名空间。RefCopilot/refcheck、Semantic Scholar、撤稿或 PubPeer 适配器只能写入版本化 verification observation，再由 citation pipeline 归一化合并；不能各自生成相互竞争的最终 finding。

引用 `finding_lineage_id` 使用正文引用稳定定位、参考文献稳定指纹、检查类型和规范化主张锚点生成，不依赖列表顺序；`finding_id` 再绑定该 lineage 的 revision content hash。引用证据或核验 observation 变化时在同一 lineage 产生新 revision；重新解析后使用 `supersedes`、`reconciles` 关联旧记录。人工动作绑定 lineage、finding ID 与 revision，证据变化后必须进入 reconciliation，不能静默沿用。

引用高亮数据由 `citation_audit.json` 和证据台账生成统一前端索引：

- 正文引用位置、页码和 bbox。
- 关联参考文献原文。
- DOI、题名、作者、年份字段差异。
- 外部核验来源、时间、状态和不可变响应快照 ID。
- 引用是否支持正文主张的状态与依据。
- 确认、改写、忽略、保持待定和重新核验操作。

点击 PDF 引用高亮打开右侧引用卡；从引用卡点击证据位置可回到 PDF；引用 concern 与人工确认队列共享同一 finding ID。

## 10. 恢复、取消与错误处理

- Worker 每个阶段开始和结束都在跨进程 job 锁内原子更新 `job.json` 并追加带 event ID 的事件。
- 服务启动时扫描非终态任务：有完整检查点的标记 `interrupted` 并允许恢复，没有安全检查点的标记失败并说明原因。
- 取消是协作式取消：阶段边界和长循环中检查取消标记；外部 HTTP 请求设置超时并在返回后再次检查。
- 重试禁止调用 `reset_job_dir()`。失败阶段写入新的 attempt 目录；只有 committed 指针切换成功后才成为当前产物。人工确认、引用响应快照和历史报告永不由重试清理。
- 前端 SSE 断开后携带最后事件序号重连；无法连接时退化为任务状态轮询。

## 11. 安全与边界

- 不把 API Key、GitHub token 或完整模型凭据写入任务、事件、文档和前端状态。
- 稿件默认只落本机；发送给 MinerU、百度 OCR 或其他外部服务必须由配置显式启用，并在任务事件中记录来源和审批信息。
- 不自动作出录用、拒稿或学术不端结论。
- 无证据意见只能进入待人工核查，不能进入确定性结论和最终报告。
- 服务默认只监听 loopback。绑定非 loopback 地址时必须配置认证；TLS 由反向代理终止，未配置认证时服务拒绝启动。
- 第一里程碑定义 `Principal`、`Session` 和 `ResourceGrant`：principal 有稳定 ID；访问令牌只用于换取短期 HttpOnly SameSite session；Paper 创建者是 owner；ResourceGrant 支持 owner、reviewer、read-only 三种角色并关联 paper/job；每次授权判断写入 principal、resource、action 和结果审计字段。
- 远程模式按 ResourceGrant 校验访问权限；状态变更请求验证 session、Origin 与 CSRF token。PDF 和产物下载使用授权接口或短期签名 URL；session 和签名 URL 均有过期时间并可撤销。
- `--unsafe-public-demo` 只能加载仓库内明确标记的合成演示数据，并强制只读；禁止读取真实上传 PDF、调用外部模型、写人工确认或启动任务。
- 上传和 JSON 请求体均有流式大小限制；解析在资源受限子进程中执行。稿件目录使用仅服务用户可读权限，日志脱敏并提供保留期限和删除接口。
- 每个任务独立记录是否同意发送到外部解析、检索或模型服务；未同意时只能运行本地能力或停在需要审批状态。

## 12. 验证策略

第一里程碑使用一篇可复制文本的真实 arXiv PDF 验证：

1. 上传后创建独立任务目录和持久化状态。
2. 服务刷新和重启后任务与事件可恢复。
3. 取消请求在阶段边界生效，失败任务能从检查点重试。
4. 论文画像、核心主张、实验清单和审稿计划均有可回指证据。
5. 里程碑 A 的解析、画像、规则核查、引用核查和单代理整合调用完整可追溯；真实专业多代理追踪在里程碑 C 验收。
6. 主要 concern 均关联核心主张和证据，次要格式问题不进入主要意见。
7. 人工确认后生成中英文 JSON/Markdown 报告。
8. 桌面和移动端可以观察任务、阅读 PDF、处理引用与 concern，无横向溢出或遮挡。
9. 仓储集成测试覆盖两个 Worker 抢占同一 job、CAS 冲突、event ID 单调性、损坏尾行恢复和 finalize 与 confirmation 并发；任一竞争只能产生一个 committed 结果。

测试分层：领域数据契约单元测试、任务仓储和恢复测试、HTTP API 测试、带替身解析器/模型的端到端测试，以及一篇真实论文的集中人工验收。不会用大规模无关回归替代核心流程验证。

## 13. 分阶段交付

### 里程碑 A：真实 PDF 任务底座

上传、Paper/ReviewJob 索引、预分配 run 目录、持久化任务、阶段 DAG、跨进程锁、事件续传、取消、重试、刷新恢复、论文画像和现有 PeerAssist 快速模式接入。只开放 fast，输出候选队列并等待人工确认；finalize 后生成版本化中英文报告。

验收：上传真实 PDF 后可从解析运行到 `awaiting_human_confirmation`；刷新和服务重启后状态恢复；取消和重试不破坏已有产物；所有画像字段回指证据；finalize 前无 final 报告，finalize 后报告 manifest 与确认 revision 一致；两个 Worker 抢占、CAS 冲突、event ID、损坏尾行和 finalize/confirmation 并发测试均通过。

### 里程碑 B：引用与 PDF 审稿闭环

引用高亮、引用卡片、字段差异、核验来源、人工操作、单双页模式和 concern 双向定位。

验收：正文引用、参考文献、finding、confirmation 使用统一 ID；PDF 与引用卡可双向跳转；重新核验触发 reconciliation；单双页和 concern 定位在桌面/移动端无溢出。

### 里程碑 C：真实专业代理

专业提示、证据切片、并行调用、反方审核、整合去重和完整调用追踪。

验收：fast/standard/deep 按定义运行真实代理集合；每次调用包含模型、证据、耗时、状态和产物；主要意见均绑定核心主张；低价值意见被整合代理抑制。

### 里程碑 D1：输入适配扩展

Word、LaTeX、补充材料和扫描件 OCR 接入统一解析与证据定位协议。

验收：每类输入都有成功、格式损坏、取消和解析器不可用契约测试；Word 段落、LaTeX 源文件、补充材料对象和 OCR bbox 均能形成稳定 locator；外部 OCR 未授权时进入 `approval_required`，拒绝后保留本地降级路径和原因。

### 里程碑 D2：深度引用与外部核验

作者年份制、撤稿、PubPeer、综述替代原始研究和引用语义支持关系。

验收：每个外部服务都有超时、限流、无结果、歧义、取消和未授权测试；任何外部异常只生成不确定状态，不直接认定引用错误；所有 observation 保留来源、时间、响应快照校验和及人工处理记录。
