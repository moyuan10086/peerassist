# PeerAssist 论文审核辅助系统

PeerAssist 是以 [DEFENSE-SEU/FactReview](https://github.com/DEFENSE-SEU/FactReview) 为代码底座构建的论文审核辅助系统，目标是把论文原文浏览、证据审稿、确定性核查、多代理评审、可追溯 MCP/Skills 调用和人工逐条确认放到同一个审稿工作台里，帮助审稿人更快定位证据、发现机械性错误，并保留真正有价值的审稿意见。

当前系统已完成一个可运行的 MVP：前端采用 React、TypeScript、Vite、PDF.js 和 lucide-react，后端由 Python 提供审稿状态、PDF、模型调用、人工确认和产物导出 API。界面语言以中文为主，证据原文保留论文原语言，避免误译影响审稿判断。

## 在线访问

- 公网服务：<http://101.47.158.17:8766/>
- 默认入口：PDF 原文阅读与智能审稿工作台
- 兼容入口：<http://101.47.158.17:8766/legacy>（自动跳转到 `/paper`，不再维护独立旧界面）
- 当前演示论文 PDF：<http://101.47.158.17:8766/paper.pdf>

当前 11 页演示论文的浏览器冷启动抽样结果：桌面端约 `3.27 秒`、移动端约 `4.09 秒`完成首页 PDF 出图与文字层准备。该结果用于本次界面回归，不替代 `PeerAssist-Eval-v1` 的正式性能评测。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| PDF 原文审稿 | 基于 PDF.js 渲染原始论文，支持 Range 分段读取、单页/双页、适应宽度、翻页、缩放、页级文字选择、证据高亮、相邻页预取和失败重试 |
| 证据审稿队列 | 将模型/规则发现的 concerns 汇入人工确认队列；侧栏证据可跳原文，PDF 页边标记可反向打开并聚焦 concern |
| 确定性核查 | 检查统计显著性、数值一致性、百分比/符号/表述冲突等机械错误 |
| 可追溯引用核查 | 在 PDF 侧栏关联正文数字引用与参考文献，可跳页高亮原文，展示外部核验来源和字段差异，并直接执行逐条人工确认；证据不足时显示明确降级提示 |
| 多代理评审 | 汇总证据、确定性核查和代理结果，生成可追溯审稿草稿 |
| MCP/Skills 追踪 | 记录工具调用、状态、产物 ID 和失败事件，便于复盘 |
| 人工逐条确认 | 支持确认、改写、降级、删除和标记待定，避免无证据事实进入最终意见 |
| 中文工作台 | 导航、按钮、状态、操作提示和操作手册均为中文 |

## 仓库结构

| 路径 | 说明 |
| --- | --- |
| `src/peerassist/confirmation_server.py` | PeerAssist 后端服务，提供页面、PDF、API、SSE、模型调用 |
| `src/peerassist/confirmation_cli.py` | 人工确认命令行工具 |
| `web/peerassist-workspace` | React/Vite 前端源码 |
| `web/peerassist-workspace/dist` | 前端生产构建产物，由后端挂载到 `/workspace/` |
| `docs/peerassist_operation_manual.md` | 中文操作手册 |
| `docs/peerassist_citation_audit.md` | 引用核查数据契约、适配器、状态和降级策略 |
| `docs/peerassist_lark_sync.md` | 飞书同步文档固定入口和本地同步记录 |
| `deploy/nginx/peerassist-subdomains.conf` | 子域名/前后端分离部署参考 |
| `eval/PeerAssist-Eval-v1` | 冻结测试集与评测骨架 |
| `RefCopilot` | 原 FactReview 生态中的参考文献核查工具 |

前端源码已上传到 `web/peerassist-workspace`。如果 GitHub 右侧语言统计暂时显示 Python 占比很高，是语言统计和文件体积规则导致的，不代表没有前端代码。

## 快速启动后端

从仓库根目录运行：

```bash
PYTHONPATH=src \
PEERASSIST_OPENAI_API_KEY="<your-runtime-key>" \
PEERASSIST_OPENAI_BASE_URL="https://deepkey.top/v1" \
PEERASSIST_OPENAI_MODEL="gpt-5.4" \
PEERASSIST_OPENAI_TIMEOUT_SECONDS="240" \
.venv/bin/python -m peerassist.confirmation_server \
  --run-dir runs/arxiv_real_data/runs/arxiv_2607_08522_v1 \
  --paper-id arxiv_2607_08522_v1 \
  --host 0.0.0.0 \
  --port 8766
```

检查服务：

```bash
curl -I http://127.0.0.1:8766/
curl -I http://127.0.0.1:8766/paper.pdf
curl -I -H 'Range: bytes=0-1023' http://127.0.0.1:8766/paper.pdf
curl http://127.0.0.1:8766/api/state
```

不要把真实 API Key 写入 README、`.env`、提交信息、飞书文档或 Git remote URL。密钥只允许通过运行时环境变量注入。

## 前端开发

```bash
cd web/peerassist-workspace
npm install
npm run dev
```

Vite 开发服务会代理以下路径到 `http://127.0.0.1:8766`：

- `/api`
- `/paper.pdf`
- `/legacy`

生产构建：

```bash
cd web/peerassist-workspace
npm run build
```

构建产物写入 `web/peerassist-workspace/dist`。后端服务会将这些文件以 `/workspace/` 静态路径提供，访问 `/`、`/paper`、`/agent` 等路径时加载同一个前端应用。

## 审稿流程

1. 打开公网服务或本地服务，进入“论文阅读”窗口。
2. 在大尺寸 PDF 阅读区使用单页/双页、适应宽度、缩放、翻页和页码输入定位原文；右侧审稿栏可拖动调整宽度或收起。双页按封面单页、随后 `2–3、4–5…` 排列，移动端自动回退单页。
3. 选择 PDF 文字后，原文和页码会自动进入右侧审稿助手，可直接触发选区审稿或写入人工证据队列。
4. PDF 页边的消息图标表示该页 concern。点击后会打开“本页关注”、聚焦对应卡片并高亮 evidence；卡片中的证据链接可再次跳回原文。
5. 打开右侧“引用核查”标签，查看正文引用、参考文献、外部来源和字段差异；点击“定位正文”或“查看参考文献原文”可跳页并高亮对应 bbox。
6. 已绑定 concern 的引用 finding 可在同一卡片中确认、改写、降级或删除；所有动作按 ReviewJob 的确认版本持久化。
7. 点击“全篇审稿”，系统读取证据台账、确定性核查、多代理结果和选中文本，调用模型生成审稿草稿。
8. 在“审稿草稿预览”查看模型生成内容；实时数据流只显示阶段状态。
9. 在“证据队列”和“人工确认”逐条检查 concern、证据来源、严重度和建议动作。
10. 在“工具追踪”查看 MCP/Skills/确定性核查/代理调用记录，在“产物导出”查看并下载最终报告。

## API 速查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/bootstrap` | 前端启动所需完整状态 |
| `GET` | `/api/state` | 当前确认队列、追踪事件、产物路径和统计信息 |
| `GET` | `/api/events` | SSE 事件流，包含 state、heartbeat、done |
| `GET` | `/api/jobs/<job-id>/workspace` | 当前任务的证据、引用核查、人工队列、工具追踪和报告视图 |
| `POST` | `/api/jobs/<job-id>/decisions` | 对任务 concern 写入带版本约束的人工决定 |
| `POST` | `/api/agent-review` | 触发智能审稿 |
| `POST` | `/api/manual-concern` | 将 PDF 选中文本保存为人工 concern |
| `POST` | `/api/decision` | 写入人工确认动作 |
| `GET` | `/paper.pdf` | 返回当前 run 的原始 PDF，支持 `Range`、ETag 和一小时私有缓存 |

## 人工确认 CLI

查看确认状态：

```bash
PYTHONPATH=src .venv/bin/python -m peerassist.confirmation_cli \
  --run-dir runs/arxiv_real_data/runs/arxiv_2607_08522_v1 \
  --state
```

提交确认动作：

```bash
PYTHONPATH=src .venv/bin/python -m peerassist.confirmation_cli \
  --run-dir runs/arxiv_real_data/runs/arxiv_2607_08522_v1 \
  --paper-id arxiv_2607_08522_v1 \
  --concern-id "<concern-id>" \
  --action confirm \
  --timestamp "2026-07-11T12:00:00+08:00" \
  --reviewer-id "local-reviewer"
```

可用动作：

| action | 含义 |
| --- | --- |
| `confirm` | 接受该 concern |
| `rewrite` | 人工改写 concern |
| `downgrade` | 降低严重度或降为次要建议 |
| `delete` | 删除不成立或无证据支撑的 concern |
| `mark_pending` | 暂不决定，后续复核 |

## 产物位置

当前演示 run 的 PeerAssist 产物位于：

```text
runs/arxiv_real_data/runs/arxiv_2607_08522_v1/stages/peerassist/
```

常用文件：

| 文件 | 说明 |
| --- | --- |
| `evidence_ledger.json` | 证据台账 |
| `deterministic_checks.json` | 确定性核查结果 |
| `agent_results.json` | 多代理审稿结果 |
| `peerassist_concerns.json` | 系统生成的审稿关注点 |
| `confirmation_review_queue.json` | 待人工确认队列 |
| `human_confirmations.json` | 人工确认动作 |
| `tool_trace.jsonl` | 工具调用追踪 |
| `agent_review_draft.md` | 模型生成的审稿草稿 |
| `peerassist_report.zh.md` | 中文审稿报告 |

## 飞书同步

飞书同步文档固定在：

- <https://my.feishu.cn/docx/XuVIdkaGgoykehxox9Kc3Qnhnw2>

本地固定记录见 `docs/peerassist_lark_sync.md`。同步时只追加新章节，不覆盖整篇文档；不要写入 API Key、GitHub token、模型 key 或审稿私密材料。

## GitHub 作者说明

如果 GitHub 页面显示早期提交作者为 `Codex`，原因是当时本机 git author 配置为 `Codex <codex@local>`。仓库已将后续提交作者配置为：

```bash
git config user.name "moyuan10086"
git config user.email "moyuan10086@users.noreply.github.com"
```

历史提交作者不会自动改变，除非明确进行历史重写。后续新提交会使用上面的 GitHub 用户身份。

## 安全与质量规则

- 不在代码、文档、飞书、提交信息或 Git remote 中保存真实密钥。
- 模型输出必须经过人工逐条确认后才能进入最终审稿意见。
- 无证据新增事实必须删除、改写或标记待定。
- 审稿意见需要绑定 PDF 原文、证据台账、确定性核查或工具追踪。
- 只把系统作为审稿辅助，不替代审稿人的最终判断。

## 引用核查产物

运行 PeerAssist 阶段后，引用核查主要产物位于 `stages/peerassist/`：

- `citation_audit.json`：正文引用、参考文献、核验记录和审计 finding 的规范索引。
- `citation_verifications/attempt-<attempt_id>.json`：实际收到的外部核验响应快照，按 SHA-256 校验且不覆盖历史尝试。
- `evidence_ledger.json`：新增 `citation` 证据项，保留原句、页码、章节、定位信息和解析器提供的坐标。
- `confirmation_review_queue.json`：将需要人工处理的引用问题与其他审稿 concern 放入同一确认队列。

第一版支持 `[1]`、`[1, 3-5]` 等数字型正文引用和编号参考文献。作者年份制、撤稿/PubPeer 和“引用是否语义支持主张”仍属于后续能力。`not_found`、`unavailable`、`ambiguous` 或核验失败都不表示引用虚假，只表示需要人工复核。

网页入口位于论文阅读窗口右侧的“引用核查”标签。前端只接收经过服务端整理的审稿视图，不返回核验适配器的原始查询、私有响应文件路径或内部缓存路径。当前解析器遇到不支持的作者年份制或非数字引用时，会显示“解析提示”和人工检查建议，不会把空结果伪装为核验通过。

## 代码底座

PeerAssist 基于 FactReview 扩展。FactReview 原始目标是为机器学习论文生成 evidence-grounded review，并提供参考文献核查、claim audit、实验复现与报告生成能力。PeerAssist 在此基础上增加了中文审稿工作台、PDF-first 交互、多代理审稿入口、人工确认队列和可追溯工具调用记录。

原始项目与论文：

- FactReview GitHub：<https://github.com/DEFENSE-SEU/FactReview>
- FactReview arXiv：<https://arxiv.org/abs/2604.04074>
