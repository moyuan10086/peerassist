# PeerAssist 全系统审阅

审阅日期：2026-07-20

审阅目标：从老师完成一次真实审稿的角度，检查 PeerAssist 当前产品闭环、平台实现、运行部署和交付门禁，明确下一阶段只做能提升真实可用性的工作。

## 一、结论先行

PeerAssist 当前已经不是“没有后端”的原型：OIDC 登录、会话、组织/项目/成员、PostgreSQL、MinIO、ReviewJob、Worker、模型设置和报告产物均已接通，公网 `8766` readiness 正常。

但系统还不能被定义为“老师可稳定使用的智能审稿系统”。原因是平台任务完成后主要只产生 `paper_summary.md` 和 `review.md` 两份 Markdown，前端没有从平台产物恢复结构化 concern、evidence、agent run 和 citation audit。因此“证据与意见”“待我确认”通常为空；草稿编辑只保存在浏览器 `localStorage`；最终 finalize 只改变任务状态，不把老师编辑后的意见写入新的不可变报告。

第一性原理判断：老师购买的不是登录、权限、任务状态或模型下拉框，而是“一篇论文从上传到一份有原文依据、可修改、可导出的审稿意见”。当前最大的工作不是增加更多平台模块，而是补齐这个最短价值链。

### 2026-07-20 增量进展

本轮已完成审稿闭环的核心补齐：Worker 发布 `review_result.json`，前端恢复带页码证据的结构化意见；ReviewJob 提供服务端草稿读写、版本号和过期写入保护；前端自动保存草稿；finalize 会把最新草稿固化为不可变 `final_report.md` 产物。平台测试 328 项通过，前端生产构建、Ruff 和差异检查通过；提交 `4090acc`、`b866fc8` 已部署到公网 `8766`。真实 Chromium 已验证结构化意见、刷新恢复、首次 finalize 和最终报告下载，全程无错误响应。

## 二、当前基线

| 项目 | 事实 |
|-|-|
| 公网入口 | `http://101.47.158.17:8766/` |
| 健康状态 | database、identity、object_store 均 ready |
| API | OpenAPI 当前 31 个路径，包含认证、组织、项目、论文、ReviewJob、产物和模型设置 |
| 代码规模 | pygount：Python 360 文件、约 75,507 行代码；TSX 约 2,921 行；CSS 约 2,509 行 |
| 前端结构 | `main.tsx` 和 `styles.css` 仍是集中式大文件，页面、API 调用和状态编排耦合在一起 |
| 最近修复 | `4771152` 恢复平台主流程；`0854242` 修复会话失效后的旧接口回退 |
| 浏览器验证 | 公网 Chromium 登录、管理员入口、模型设置读取、PDF 上传和 PDF 阅读均可进入；失效会话会回到登录页 |
| 当前门禁 | 前端构建和定向测试通过；统一 `verify fast` 当前因两个已有测试文件的 Ruff I001 import 排序失败 |

## 三、老师主流程审阅

| 流程 | 当前状态 | 判断 |
|-|-|-|
| 登录/退出/切换账号 | 可用 | 已支持 OIDC、服务端会话、CSRF；会话失效后已不再请求旧 `/api/jobs` |
| 上传 PDF | 可用但提示不够好 | 正常 PDF 可上传并创建任务；multipart 400 仍可能被包装成通用英文错误 |
| 阅读论文 | 可用 | PDF.js、Range、页码、选择文字和任务关联已接通 |
| “这篇论文讲了什么” | 部分可用 | Worker 能生成摘要；长论文只读取前 20 页、最多约 24,000 字符，且没有显著的截断提示 |
| 智能审稿 | 部分可用 | 上传后 Worker 自动运行；Agent 页的再次运行按钮在平台任务下实际上只是提示任务已运行，不能真正重跑 |
| 证据与意见 | 不完整 | 平台任务没有把 Markdown 结果转换为结构化 concern/evidence，队列会为空 |
| 待我确认 | 不完整 | 没有结构化 concern 时无法逐条确认、改写、降级或删除 |
| 草稿编辑 | 不完整 | 编辑内容只存在当前浏览器 localStorage，未写入服务端任务或报告版本 |
| 导出报告 | 部分可用 | 可下载 Worker 产物；finalize 不会把老师编辑后的草稿合并成新的最终报告 |
| 模型设置 | 可用但依赖凭据 | 配置和发现接口已接通；当前上游凭据曾返回 401，系统会降级到本地抽取 |
| 成员管理 | 可用但偏工程化 | 管理员可创建项目和成员，但只能填 UUID，没有用户搜索、邀请或明确 onboarding |

## 四、P0 阻断问题

### P0-1：平台审稿结果没有结构化进入证据队列（已解决）

`services/worker/main.py` 只发布 `paper_summary.md` 和 `review.md`。`web/peerassist-workspace/src/main.tsx` 读取 Markdown 后只填充草稿和产物，不填充 `queue.items`、`agent_runs`、`evidence_preview` 或 `citation_audit`。

该问题已通过 `review_result.json` 和前端 hydration 解决；仍需公网回归确认真实 Worker 产物在不同任务状态下都能恢复。

### P0-2：编辑意见与最终报告不是同一份事实（已解决）

当前草稿通过 ReviewEvent 服务端持久化，带 `expected_version` CAS；finalize 读取最新草稿并发布 `final_report.md`。前端仍保留离线 localStorage 兜底，网络恢复策略属于后续优化。

### P0-3：Worker 只消费固定租户范围

Compose 通过 `PEERASSIST_WORKER_SCOPE_FILE` 给 Worker 注入一个 organization/project scope。管理员可以创建多个项目，但新增项目没有独立 Worker 消费范围；任务可能成功入队却永远没有 Worker 处理。这在单演示项目中不明显，在真实组织中会直接造成“任务一直排队”。

### P0-4：公网仍是 HTTP

Keycloak 日志明确提示非安全上下文，Cookie 不会按 HTTPS 方式保护。论文稿件、会话 Cookie、模型配置和报告都不应通过公网明文传输。当前地址只能用于演示或测试，不应承载真实未发表稿件。

## 五、P1 高优先级问题

1. **多组织/多项目选择缺失。**前端 `resolvePlatformProject()` 默认取第一个组织的第一个项目，用户无法选择当前项目，多个项目下会读错数据。
2. **正常用户 onboarding 断裂。**普通成员没有创建项目权限，但页面提示“请到成员管理创建项目”；管理员需要先拿 UUID 再手工授权，老师无法自然开始第一篇审稿。
3. **错误信息不可执行。**服务器对部分 Starlette 400 返回 `The operation could not be completed.`，用户不知道是文件格式、请求体、会话还是权限问题。
4. **模型降级与任务状态脱节。**模型失败会静默落到本地抽取，摘要虽标记降级，但任务卡和报告没有统一显示“为什么没有模型意见、下一步怎么处理”。
5. **平台 SSE 没有被使用。**v1 提供 job-scoped event stream，但前端主要每 3 秒轮询 jobs、events 和 artifacts；任务进度、网络负载和中断恢复仍不够稳定。
6. **导出界面是产物下载器，不是报告工作流。**没有最终报告预览、版本、导出格式选择和“已确认意见/未处理意见”汇总。
7. **前端缺少行为级自动化测试。**当前主要依赖 TypeScript 构建、源码契约测试和人工 Chromium 检查，核心上传、确认、导出交互没有稳定的 Playwright 回归套件。

## 六、P2 工程与运营问题

1. `main.tsx`、`styles.css` 集中承载多个窗口和全部状态，修复一个流程容易影响其他窗口。
2. 论文、任务、成员和产物接口没有分页策略；项目数量增长后首屏请求会线性变慢。
3. 模型配置以服务端单个 JSON 文件保存，不是按组织/项目隔离；当前适合单实例演示，不适合多租户 SaaS。
4. 旧 M0 路由、旧页面和兼容适配器仍保留在仓库，导致接口语义和文档容易漂移。兼容层必须明确只读、生命周期和删除计划。
5. Python 主仓库仍包含大量历史运行时和 `pass`/best-effort 分支；这些不一定是 bug，但需要逐步标注“可接受降级”与“不可静默失败”。
6. 统一 `verify fast` 被两个测试文件的 Ruff import 排序问题阻断，说明提交门禁与当前工作树并未保持绿色。

## 七、建议目标与实施顺序

### 目标

让老师在一个项目中完成：登录 → 上传 PDF → 看到论文摘要 → 看到带证据的主要/次要意见 → 逐条确认或修改 → 刷新/重登不丢失 → 导出最终报告。

### 阶段 A：先恢复价值闭环

- 为 ReviewJob 增加结构化 review result：concerns、evidence、agent runs、citation audit 和 summary。
- Worker 在生成 Markdown 的同时发布结构化 JSON Artifact，或把结构化结果写入平台表。
- 前端从平台结果恢复 `queue.items`，让“证据与意见”和“待我确认”真正可用。
- 增加 server-side draft/revision API；finalize 只能基于最新确认版本生成不可变最终报告。

验收：同一任务刷新、换标签页、重新登录后，意见、证据和草稿保持一致；至少一条意见能从卡片跳到 PDF 原文并完成 confirm/rewrite/delete；导出内容包含最新人工修改。

### 阶段 B：让第一次使用顺畅

- 增加当前组织/项目选择器和明确的“创建项目/加入项目”引导。
- 用邮箱/用户名搜索或邀请替代手填 UUID。
- 上传前校验文件类型和大小，错误显示中文原因与下一步。
- 对模型不可用、Worker 排队、失败、取消、重试统一使用用户能理解的状态文案。

验收：新账号无需阅读技术文档即可开始第一篇审稿；普通成员不会被引导去无法访问的管理员页面。

### 阶段 C：再处理平台可靠性

- Worker 改为共享队列消费所有授权项目，或按项目动态注册 worker lease；禁止固定单租户 scope 成为生产默认。
- 为 v1 job event stream 做断线续接，轮询只作为降级。
- 上线 TLS、Secure/HttpOnly/SameSite Cookie、反向代理、请求限流、上传病毒/文件安全策略和备份恢复演练。
- 修复 Ruff 门禁，建立 Playwright 主流程回归，最后再拆分前端模块。

验收：多项目并发上传均能处理；公网稿件不经过明文 HTTP；统一 verify all 和浏览器回归同时通过。

## 八、明确暂缓

在 P0 闭环完成前暂缓：无限证据画布、多用户实时协作、完整 Next.js 迁移、复杂权限矩阵、批量上传、模板市场和大规模 Agent 编排。它们都可能有价值，但不会解决当前老师“意见为空、修改丢失、报告不含编辑结果”的直接问题。

## 九、事实来源与验证

- 代码：`services/api/routes`、`services/worker/main.py`、`src/peerassist/platform`、`web/peerassist-workspace/src/main.tsx`。
- 运行：`http://101.47.158.17:8766/api/v1/ready` 返回 database、identity、object_store ready。
- 浏览器：公网 Chromium 验证登录、管理员入口、模型设置、PDF 上传和 PDF 阅读；未登录访问 `/agent` 会回到 `/login`，无旧接口请求。
- 工程统计：`.venv/bin/pygount`，已排除依赖、缓存和构建目录。
- 门禁：`.venv/bin/python scripts/verify_repository.py fast` 当前在 Ruff 阶段失败，错误为 `tests/platform/test_model_settings_api.py` 和 `tests/repository/test_frontend_draft_editor.py` 的 I001 import 排序。

## 十、2026-07-20 HTTP 与失败任务删除修复

本轮针对公网 HTTP 下的通用英文错误，以及失败/取消审稿记录无法清理的问题完成修复：

- `public_base_url` 使用 `http://` 时，登录 Cookie 不设置 `Secure`，HTTP 演示地址可以完成登录；这只适合测试，不适合承载真实未发表稿件。
- 前端统一把 `The operation could not be completed.` 和 `The request could not be validated.` 转成中文、带下一步动作的提示，不再把平台兜底英文直接展示给老师。
- 新增 `DELETE /api/v1/projects/{project_id}/review-jobs/{job_id}`。仅 `failed` 或 `cancelled` 任务可删除；运行中、等待授权或已完成任务会保留并返回明确状态错误。
- 删除会清理任务事件、工作项、产物、阶段清单、报告版本、审稿尝试、Outbox 记录及对象存储任务产物，避免失败记录和孤儿文件长期堆积。
- 任务卡为失败/取消的 v1 任务显示“删除记录”；操作使用 CSRF 与 `Idempotency-Key`，成功返回 `204` 并刷新列表。

公网 `http://101.47.158.17:8766/` 的 Chromium 验证结果：登录成功，`peerassist_session` 与 CSRF Cookie 的 `secure=false`；选取一个 `cancelled` 任务删除返回 `204`，随后任务不再出现在列表；页面无 4xx/5xx 和 page error。第一次手工 API 复现的 `422` 是验证脚本漏传 `Idempotency-Key`，前端实现已正确传递该请求头。

## 十一、2026-07-22 首次使用可靠性

本轮完成老师首次进入系统后的项目上下文和上传入口修复：

- 顶部新增当前审稿项目选择器，选择结果写入 `peerassist.activeProjectId`；任务列表、论文列表和上传均使用显式选中的项目，不再默认取第一个组织的第一个项目。
- 没有项目时显示独立引导。管理员进入“创建第一个项目”，普通老师看到“联系管理员将你加入项目”，不再把普通账号错误引导到无权限页面。
- 上传前校验 `.pdf` 扩展名和 100 MB 上限，避免无效文件上传到后端后才得到模糊错误。
- 前端按 `invalid_upload`、`payload_too_large`、`authentication_required`、`forbidden`、`dependency_unavailable`、`stale_version` 等稳定错误码给出中文原因与下一步。
- 论文上下文探测只在服务端明确返回 404/410 时清除；临时网络失败不再删除本地论文、任务和 PDF 地址，解决刷新后偶发回到空白工作台的问题。
- 新增 `scripts/verify_first_use_browser.py`，公网 Chromium 已验证登录、项目选择持久化、非 PDF 拦截、打开论文和刷新恢复，且无 4xx/5xx、英文兜底错误或 page error。

部署期间确认云盘已经扩至 50 GB，但根分区仍为 39.8 GB。已在线执行分区与 ext4 扩容，根文件系统现约 49 GB、可用空间约 13 GB，无需重启；Keycloak、MinIO、PostgreSQL、API 和 Worker 均恢复运行。
