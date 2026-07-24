# PeerAssist 证据驱动 AI 审稿工作台定位与产品设计

**状态：** 已确认，待实施计划
**日期：** 2026-07-24
**适用范围：** 教师端产品、聚合 API、平台数据模型与现有审稿流水线

本规格覆盖 `2026-07-20-peerassist-editorial-workspace-ui-design.md` 中“阅读/审稿/证据/交付”导航、本地浏览器草稿和确认后才从旧 Artifact 导出的约定；PDF 阅读、响应式布局、焦点管理与证据定位行为继续沿用。它也覆盖 `2026-07-13-peerassist-end-to-end-review-design.md` 中教师端直接暴露任务时间线、工具调用和多模式选择的约定；底层可恢复任务与不可变产物协议继续沿用。

## 1. 产品定位

PeerAssist 是面向高校教师和科研人员的证据驱动 AI 审稿工作台。它帮助用户快速理解论文、定位值得核查的问题、形成有原文依据的审稿意见，并导出到现有期刊、会议或教学流程。

PeerAssist 的价值不是代替审稿人作判断，也不是重建期刊或会议管理系统，而是缩短从“收到一篇论文”到“形成一份可交付审稿意见”的时间，同时让每条重要意见可核查、可修改、可追溯。

## 2. 第一性原理与硬约束

### 2.1 真正目标

老师最终需要的是一份自己愿意负责、能够交付的审稿意见。系统只有同时做到以下四件事才产生价值：

1. 快速说明论文讲了什么，以及优先读哪里。
2. 把值得核查的问题与原文页码、片段或结构化证据关联起来。
3. 允许老师接受、改写、删除和补充 AI 建议。
4. 把结果复制或导出到老师已经使用的期刊、会议、Word、邮件或教学流程。

### 2.2 硬约束

- 未登录用户不能读取论文、摘要、意见或导出物。
- PDF 默认先在本地解析；只有用户对当前审稿任务明确同意后，才可把稿件内容发给外部模型。
- AI 输出是审稿线索和草稿，不是自动接收、拒稿、学术不端或研究诚信判定。
- 主要意见必须有可定位证据；证据不足时必须标为“待核查”，不能伪装成确定结论。
- 上传、解析、模型、保存或导出部分失败时，已上传论文和已编辑内容不能丢失。
- 兼容现有 `Paper`、`PaperVersion`、`ReviewJob`、`Artifact`、`ReviewEvent`、候选 finding 与老师决策事件，以及 Project 数据隔离，不为产品改名而重建底层。平台 `Decision` 是权限判定枚举，不是审稿意见对象。
- 普通老师不需要理解 Project、Agent、队列、事件流、对象存储或内部产物路径。

### 2.3 事实与假设

已确认的事实：系统已有 PDF 上传与阅读、任务状态、证据产物、人工决策事件、草稿保存、导出基础、OIDC、PostgreSQL、对象存储和 Worker；旧文件任务模型已有逐任务外部服务授权，但 M1 平台模型尚未形成同等的权威授权记录；当前草稿以 `review_job.draft_saved` 事件载荷保存，缺少独立可并发编辑的审稿文档；当前平台 `finalize()` 会先把任务改为 completed，尚未以导出 Artifact 成功登记作为完成条件。

当前产品假设：教师最常见的使用方式是单人审阅一篇 PDF，再把意见提交到外部系统。团队指派、编辑部工作流和多人实时协作只有在试用数据证明必要后再进入范围。

## 3. 产品边界

### 3.1 当前要做

- 单篇 PDF 的上传、恢复和历史找回。
- 论文概要、研究问题、方法、主要发现、局限和重点阅读路线。
- 证据与意见双向定位。
- 结构化审稿文档编辑、自动保存、冲突处理、复制和导出。
- 明确的外部模型授权、降级和失败恢复。
- 管理员模型配置与可用性检查，但不进入老师主导航。

### 3.2 当前不做

- 不做期刊投稿、编委分稿、审稿人邀请、截稿期和正式出版。
- 不做会议大规模审稿人匹配、利益冲突计算或领域主席决策。
- 不做自动接收、拒稿、评分或学术不端判定。
- 不把无限画布、Agent 控制台、队列、trace 或产物目录作为老师主界面。
- 不优先建设复杂组织治理、多人实时协同、计费和完整出版生态。

## 4. 开源项目的选择性借鉴

| 参考项目 | 借鉴 | 不照搬 | PeerAssist 落点 |
| --- | --- | --- | --- |
| OpenReview | 清晰的待审任务入口、结构化评审表单、问题分级 | 投稿分配、COI 匹配、公开讨论与会议治理 | “我的审稿”任务列表；总体评价、主要问题、次要问题、修改建议四段式文档 |
| JOSS | 检查清单、证据透明、审稿过程可追溯 | GitHub Issue 驱动和软件论文专用流程 | 每条意见显示状态、证据、影响和建议动作；导出保留证据引用 |
| Kotahi | 连续文档编辑、灵活工作流和多格式导出 | 出版编排、实时多人协作和 JATS 全链路 | 服务端审稿文档、自动保存、版本冲突提示、Word/Markdown/PDF 导出 |
| OJS | 稿件生命周期概念和对现有期刊流程的兼容 | 作者投稿、编委分稿、排版、DOI 与在线出版 | 保留清晰任务状态；结果通过复制和文件导出进入 OJS，而非替代 OJS |
| Janeway | 轻量角色界面、数字出版集成边界 | 期刊后台和出版角色体系 | 老师端与管理员设置分离；后续按真实需求增加导出适配器 |

结论：PeerAssist 采用“审稿工作台 + 外部流程出口”，不采用“全流程期刊/会议平台”。这比照搬任何一个参考系统更小，也更符合教师当前任务。

## 5. 信息架构

教师端只保留四个一级入口。

| 入口 | 用户问题 | 核心内容 | 主动作 |
| --- | --- | --- | --- |
| 我的审稿 | 我现在要审哪篇？ | 上传区、进行中任务、最近论文、失败恢复 | 上传论文 / 继续审稿 |
| 论文研读 | 这篇论文讲了什么，哪里值得看？ | PDF、概要、重点阅读路线、证据和待核查意见 | 查看证据 |
| 审稿意见 | 我要如何形成自己的意见？ | 四段式审稿文档、证据引用、AI 建议处理 | 保存并导出 |
| 历史记录 | 以前的论文和结果在哪里？ | 历史论文、任务状态、最近编辑、导出记录 | 重新打开 |

模型设置只对管理员显示在账号菜单的“系统设置”中。Project 继续承担数据隔离，但对只有一个默认工作区的老师隐藏。Agent、队列、trace、Artifact 等内部页面从普通导航移除，仅在错误诊断或管理员入口按需提供。

## 6. 主流程与页面行为

```text
上传 PDF
→ 本地解析
→ 明确选择是否使用外部模型
→ 查看摘要和重点阅读路线
→ 对照原文核查意见
→ 编辑结构化审稿文档
→ 复制或导出
```

### 6.1 我的审稿

- 首次进入直接显示 PDF 上传区，不要求先创建项目或配置 Agent。P0 默认启动快速审稿，不让首次用户选择运行模式；完整审稿模式在 P1 经过质量和耗时验证后再显示。
- 上传成功后立即创建可恢复的论文记录；重复上传相同文件时复用已有论文，并询问继续已有审稿还是新建一次审稿。
- 任务卡只显示老师能理解的状态：准备论文、生成概要、生成意见、待核查、可导出、失败。
- 每个非终态都说明当前发生了什么和下一步动作；失败任务可以重试或删除，删除失败记录不删除共享的原始论文，除非用户明确删除论文。

### 6.2 论文研读

- PDF 是主阅读面，概要和重点阅读路线在解析完成后尽早出现，不等待全部模型步骤。
- 概要至少包含研究问题、方法、主要发现、局限和建议优先阅读位置。
- 每条意见包含问题、影响、原文证据、定位信息和建议动作。
- 点击意见定位原文，点击原文证据返回对应意见。定位失败时保留证据文本并标为“位置待核查”。
- 外部模型未授权或不可用时，保留本地解析、确定性检查和可编辑文档，并明确标识未生成的能力。

### 6.3 审稿意见

- 默认结构为总体评价、主要问题、次要问题、修改建议。
- AI 建议可接受、改写、降级、删除；老师可新增完全手写的意见。
- 每条进入最终稿的主要意见可查看证据来源；删除或改写不改变历史 AI 产物。
- 文档由服务端自动保存，并使用版本号进行乐观并发控制。保存冲突必须提示用户选择保留当前编辑、载入服务器版本或合并，不能静默丢弃。
- “已完成”只在导出物成功生成并登记后出现。复制正文可以作为轻量出口，但不能伪造已经生成的文件。

### 6.4 历史记录

- 默认按最近编辑排序，可按标题、状态和时间搜索。
- 只展示当前用户可访问的论文，不使用浏览器旧缓存补造服务器不存在的记录。
- 论文不存在、权限失效或数据已删除时清理本地选择并回到可行动的空状态。

## 7. 数据与 API 设计

### 7.1 复用与最小新增

继续复用：

- `Paper` / `PaperVersion`：论文身份和不可变版本。
- `ReviewJob`：一次审稿及其可恢复状态。
- `Artifact`：解析、概要、证据、候选意见和导出物。
- `ReviewEvent`：任务状态和可审计事件。
- 候选 finding Artifact：AI 意见及其 lineage、revision 和证据绑定。
- `ReviewEvent` 中的老师决策事件：接受、改写、降级和删除动作的追加事实；服务端按事件序列折叠出当前决策投影。
- `Project`：数据隔离基础设施，不作为教师工作概念。

平台侧只新增两个权威对象：

1. `ExternalServiceConsent`：包含不可变 record ID、单调 `generation`、`organization_id`、`project_id`、`review_job_id`、`paper_version_id`、`service`、`provider_config_revision`、`policy_version`、`data_scope`、`status`、`decided_by`、`decided_at`、`expires_at`、`superseded_at`、`version` 和时间戳。状态为 `pending/granted/denied/revoked/expired/not_required`；只有 granted、未过期、未撤销、未被 supersede、论文版本与任务一致且 provider 配置 revision 未被管理员停用时有效。唯一约束为 `(review_job_id, paper_version_id, service, generation)`，并以部分唯一约束保证同一 job/version/service 只有一个 `superseded_at IS NULL` 的 current record。变更使用独立乐观版本。grant/deny 只能从 pending 进入；granted 可进入 revoked/expired；denied/revoked/expired 后重新授权，或论文版本、策略、provider 配置和数据范围变化时，在同一事务中 supersede current record 并以 `generation + 1` 创建新的 pending，不能把旧决定改回 granted。
2. `ReviewDocument`：每个 `review_job_id` 唯一，使用独立于 `ReviewJob.version` 的 `document_version`。正文 schema 固定为总体评价、主要问题、次要问题、修改建议四个 section；每个 block 保存用户文本、可选 `finding_lineage_id + finding_id + finding_revision`、evidence ID/locator 和来源类型。对象还记录 `base_decision_event_id`、最后编辑人和时间。保存使用 `expected_document_version`，引用已被新 finding revision 取代时返回可合并冲突，而不是静默改写。

平台 `Decision` 权限枚举不参与审稿数据模型。老师决策的权威事实仍是追加的 `ReviewEvent`；读取时按单调 event ID 对同一 finding lineage 折叠当前值。最终导出冻结 `ReviewDocument.document_version + base_decision_event_id + finding revisions`，从而避免草稿与意见在导出过程中漂移。

旧文件任务迁移必须先通过 legacy registration 或内容摘要，把 legacy `paper_id` 映射到平台 `PaperVersion.id`。旧 `ExternalServiceConsent` 和平台已有 `review_job.decision_recorded` consent 事件只可在映射唯一、决定字段完整且目标 provider 配置仍启用时导入；无法唯一映射或字段不足时保持 blocked 并要求用户重新授权，绝不能推断为 granted。切换后平台对象是唯一可写真相，旧记录只读。

### 7.2 教师端聚合 API

现有底层项目 API 保持兼容，在其上提供教师端聚合层：

```text
GET    /api/v1/review-workspace
POST   /api/v1/papers
POST   /api/v1/papers/{paper_id}/reviews
GET    /api/v1/reviews/{review_id}
POST   /api/v1/reviews/{review_id}/cancel
POST   /api/v1/reviews/{review_id}/retry
DELETE /api/v1/reviews/{review_id}
POST   /api/v1/reviews/{review_id}/consents/model
GET    /api/v1/reviews/{review_id}/evidence
POST   /api/v1/reviews/{review_id}/findings/{lineage_id}/decision
GET    /api/v1/reviews/{review_id}/document
PUT    /api/v1/reviews/{review_id}/document
POST   /api/v1/reviews/{review_id}/export
GET    /api/v1/reviews/{review_id}/exports
GET    /api/v1/reviews/{review_id}/exports/{artifact_id}/download
```

`POST /papers` 返回 `created`、`duplicate_with_reviews` 或 `duplicate_without_review`。相同幂等键重放返回原响应；相同内容使用新幂等键时不重复存储 Paper/PaperVersion，但允许客户端用 `review_action=continue_existing|create_new` 明确选择审稿行为。重解析通过 review retry 在失败阶段恢复，不覆盖原论文。

`GET /review-workspace` 返回最近论文、审稿状态、当前 Project 解析结果和可执行动作。用户只有一个 active Project membership 时由服务端选为默认；没有时由 bootstrap 原子创建个人默认 Project，或在无法创建时返回 `workspace_unavailable`；有多个但没有服务端持久化的 default membership 时返回 `workspace_selection_required`，用户选择后保存默认值。绝不按列表顺序或浏览器缓存选择。所有资源读取仍按资源所属 Project 重新授权。

finding decision 请求必须携带准确的 `lineage_id`、`finding_id`、`finding_revision`、`action=accept|rewrite|downgrade|delete`、可选改写文本、`expected_review_version`、`expected_document_version` 和 `last_decision_event_id`。服务端在同一数据库事务中校验 finding revision、追加决策事件、把动作投影到 `ReviewDocument` 并更新 `base_decision_event_id`；任一版本过期则整体不写入并返回 `finding_revision_conflict` 或 `document_version_conflict`。自由撰写和段落编辑继续走 document PUT。

版本域必须显式区分：review cancel/retry/delete 使用 `expected_review_version`，授权使用 `expected_consent_version`，文档保存使用 `expected_document_version`，导出请求携带要冻结的 `document_version` 和 `decision_event_id`。model consent 端点接受 grant/deny/revoke/reapply，状态转换不合法时返回 `consent_state_conflict`。除 GET 和文件下载外，写操作使用幂等键。API 返回稳定错误码、`retryable`、当前版本和建议动作；异步 retry/export 返回 operation 状态并由 review/exports GET 轮询，前端不根据 HTTP 文案猜状态。

### 7.3 状态原则

- 论文状态、任务状态、文档状态和导出状态彼此独立，页面组合展示，不用一个 `completed` 覆盖所有事实。
- Worker 必须在每次外部调用前重新读取授权并校验租户、任务、论文版本、service、provider 配置、策略、数据范围、状态和有效期；排队时的旧检查不能代替执行前检查。
- 导出不新增第三个业务对象：复用 `CommandRecord` 提供幂等身份，`WorkItem/Outbox` 驱动异步执行，`Artifact` 保存权威结果。导出 logical name 由 `review_id + document_version + decision_event_id + format` 确定，并建立 `(job_id, logical_name)` 唯一约束。流程为 `draft_ready → exporting → exported`；对象先写 deterministic staging key，校验后在同一数据库事务登记 Artifact、追加事件并把任务标为 completed。若对象已发布但事务失败，重试按 command/logical name 对账并复用；若对象生成失败则清理 staging key。只有权威 Artifact 为 ready 后才能完成。
- 所有状态转换由服务器持有；浏览器缓存只优化体验，不是权威来源。

## 8. 隐私、授权与管理员边界

- 授权界面必须说明服务提供方、发送的数据、用途以及拒绝后的本地能力。
- 授权按审稿任务、论文版本、服务、策略和数据范围生效，不能用一次全局同意覆盖未来稿件；论文版本或发送范围变化后需要重新确认。管理员停用 provider 配置后，相关授权不再有效。
- 拒绝授权不是错误。系统进入本地模式并保留后续再次授权入口。
- API Key 只保存在服务端密钥存储；前端只显示掩码、提供连通性测试和模型列表拉取。
- 管理员可配置模型和查看服务状态，普通老师只看“可用/不可用/需授权”，不接触 Base URL、密钥或内部模型路由。
- 模型配置修改不能改变已经运行任务的提供方和模型记录；任务保存实际使用的配置快照标识。

## 9. 失败恢复与对抗性场景

| 场景 | 必须行为 |
| --- | --- |
| 空文件、伪 PDF、超大文件、加密或损坏 PDF | 在有界流式校验或本地解析阶段失败；清理临时对象；给出可行动错误 |
| 同一 PDF 重复上传或网络重试 | 内容摘要去重；幂等请求返回同一结果；不产生孤立对象 |
| 上传完成但解析失败 | 保留论文；允许重新解析或删除；不显示假摘要 |
| 未授权、拒绝授权或授权过期 | 不调用外部模型；继续本地能力；清楚显示缺失结果 |
| 模型超时、限流或返回畸形数据 | 保留本地产物和已编辑文档；记录可重试失败；不把半成品标为完成 |
| 两个标签页同时编辑 | 第二个保存收到版本冲突并由用户处理；不得最后写入者静默覆盖 |
| 刷新、退出再登录或 Worker 重启 | 从服务器状态恢复；不依赖旧浏览器缓存伪造任务 |
| 导出生成成功但登记失败 | 用确定性 staging key 和 logical name 对账；重试复用已校验对象并登记 Artifact；不标记完成 |
| 删除失败任务时对象删除部分失败 | 先做权威软删除/墓碑，后台幂等清理；列表立即不再展示该失败记录 |
| 越权 ID、过期会话或普通账号访问管理 API | 返回无内容泄露的 401/403/404；不能从错误差异推断论文存在 |
| 原文包含 prompt injection | 作为不可信论文内容处理；不能改变系统规则、调用权限或导出边界 |

## 10. 实施阶段

### 阶段 A：让主流程真实可用

1. 统一四入口导航，隐藏老师不需要的技术入口。
2. 提供 `review-workspace` 聚合读取和默认工作区解析。
3. 打通上传、本地解析、真实概要、证据和候选意见。
4. 增加平台权威授权，Worker 在外部调用前强制校验。
5. 增加 `ReviewDocument`，修复自动保存和冲突提示。
6. 导出成功后再完成任务，失败任务可恢复和删除。

### 阶段 B：让结果值得使用

1. 提高概要完整性和重点阅读路线质量。
2. 统一意见、证据和 PDF 双向定位。
3. 完成 Word、Markdown、PDF 导出与外部流程模板。
4. 以 3 至 5 名教师的真实任务验证完成率、可定位率、保留率和恢复率。

### 阶段 C：由使用证据决定扩展

只在真实使用证明需要后增加批量审稿、模板库、团队协作、OJS/OpenReview 连接器、Next.js 渐进迁移或证据关系画布。

## 11. 验收标准

- 未登录访问任何论文、摘要、证据、审稿文档或导出地址均不可获得内容。
- 普通老师首次进入只需理解四个一级入口，无需配置 Project、Agent 或模型内部参数。
- 使用一篇可选择文本的 PDF 能完成“上传、本地解析、授权选择、概要、证据核查、编辑、导出”全流程。
- 拒绝外部模型后仍可阅读 PDF、使用本地解析结果、编辑并导出人工内容。
- 使用 5 篇不同结构的公开、可选择文本 PDF 做 P0 样本；主要 AI 意见证据可定位率以“带有效页码或原文 locator 的主要意见数 / 主要 AI 意见总数”计算，不低于 95%，其余必须明确标为待核查。
- 刷新、重登、普通模型失败和 Worker 重启不丢论文与审稿文档。
- 并发编辑不会静默覆盖，导出失败不会出现假完成。
- 重复上传、重复提交和重复导出不生成不可控的重复记录。
- 老师端不出现 Agent、队列、trace、Artifact 路径等实现术语。
- P1 邀请 5 名未参与开发的教师各完成至少 1 篇真实审稿；至少 4 人无需讲解完成主流程。意见保留率以“被接受或改写后进入冻结导出版本的 AI 意见数 / 该任务展示给用户的 AI 意见数”计算，汇总不低于 50%。该指标是试用目标，不作为 P0 发布阻断门禁。
- 契约测试覆盖重复上传与幂等重放、零/单/多 Project 解析、授权拒绝/撤销/过期/版本变化、两个标签页文档冲突、模型部分失败、导出对象与数据库部分失败、失败任务删除、Worker 重启、越权 ID 和过期会话。
- 安全测试使用包含 prompt injection 的公开或合成 PDF fixture，验证论文文本不能改变系统指令、扩大工具权限、自动授权外传或把隐藏指令写入最终报告。

## 12. 决策摘要

PeerAssist 的最小正确形态不是另一个 OJS 或 OpenReview，而是一张围绕单篇论文展开的证据审稿桌面：左边是原文，中间是理解和核查，右边或独立页是老师自己的审稿文档，最后通过复制和导出回到现有流程。底层平台能力继续存在，但产品设计以老师完成一次审稿为唯一组织原则。
