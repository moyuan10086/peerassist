# PeerAssist 论文审核辅助系统操作手册

本文档面向系统维护者和审稿使用者，说明 PeerAssist 的启动、访问、审稿流程、人工确认、产物导出和常见故障处理。文档只记录运行方法和占位配置，不保存任何真实 API Key、GitHub token 或审稿私密材料。

## 1. 系统入口

当前公网服务地址：

- `http://101.47.158.17:8766/`

主要页面：

| 路径 | 用途 |
| --- | --- |
| `/`、`/paper` | PDF 原文阅读与批注式审稿入口 |
| `/agent` | 智能审稿、模型配置状态、审稿草稿预览 |
| `/queue` | 证据审稿队列与关注点处理 |
| `/trace` | MCP/Skills/工具调用追踪 |
| `/confirm` | 人工逐条确认入口 |
| `/artifacts` | 产物导出与文件路径查看 |
| `/legacy` | 旧版服务端渲染界面兜底 |
| `/paper.pdf` | 当前 run 绑定的原始 PDF |

## 2. 本地目录与关键文件

| 路径 | 说明 |
| --- | --- |
| `src/peerassist/confirmation_server.py` | PeerAssist 审稿工作台后端服务 |
| `src/peerassist/confirmation_cli.py` | 人工确认命令行工具 |
| `web/peerassist-workspace` | React + TypeScript 前端源码 |
| `web/peerassist-workspace/dist` | 前端生产构建产物，后端以 `/workspace/` 静态路径提供 |
| `runs/arxiv_real_data/runs/arxiv_2607_08522_v1` | 当前演示 run 目录 |
| `docs/peerassist_lark_sync.md` | 飞书同步文档固定入口和本地追加记录 |
| `deploy/nginx/peerassist-subdomains.conf` | 子域名/前后端分离部署示例 |

当前演示 run 的 PeerAssist 产物位于：

```text
runs/arxiv_real_data/runs/arxiv_2607_08522_v1/stages/peerassist/
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

后台运行示例：

```bash
setsid env \
  PYTHONPATH=/root/peerassist-review-system-20260710/peerassist/src \
  PEERASSIST_OPENAI_API_KEY="<your-runtime-key>" \
  PEERASSIST_OPENAI_BASE_URL=https://deepkey.top/v1 \
  PEERASSIST_OPENAI_MODEL=gpt-5.4 \
  PEERASSIST_OPENAI_TIMEOUT_SECONDS=240 \
  /root/peerassist-review-system-20260710/peerassist/.venv/bin/python \
  -m peerassist.confirmation_server \
  --run-dir /root/peerassist-review-system-20260710/peerassist/runs/arxiv_real_data/runs/arxiv_2607_08522_v1 \
  --paper-id arxiv_2607_08522_v1 \
  --host 0.0.0.0 \
  --port 8766 \
  >/tmp/peerassist-confirm-server-8766.log 2>&1 &
echo $! >/tmp/peerassist-confirm-server-8766.pid
```

健康检查：

```bash
curl -I http://127.0.0.1:8766/
curl -I http://127.0.0.1:8766/paper.pdf
curl http://127.0.0.1:8766/api/state
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

1. 打开 `http://101.47.158.17:8766/`，默认进入论文阅读窗口。
2. 在 PDF 阅读区浏览原文。当前阅读器基于 PDF.js 渲染，支持放大、翻页、文字选择和打开原始 PDF。
3. 选择 PDF 中的一段文字后，可在右侧作为人工关注点提交，也可作为智能审稿的额外关注上下文。
4. 点击“全篇审稿”后，系统会读取证据台账、确定性核查、多代理结果和选中文本，调用配置的大模型生成审稿草稿。
5. 在“审稿草稿预览”查看完整模型输出。实时数据流只显示阶段状态，不承载完整报告。
6. 在“证据队列”逐条查看系统提出的 concern、证据来源、严重度和建议处理方式。
7. 在“人工确认”中对每条 concern 执行确认、改写、降级、删除或标记待定。
8. 在“工具追踪”查看每次 MCP/Skills/确定性检查/代理调用的状态、时间和产物 ID。
9. 在“产物导出”复制关键产物路径，用于写正式审稿意见或复盘。

### 5.1 引用核查

PeerAssist 会从论文正文中提取 `[1]`、`[1, 3-5]` 等数字引用，并与参考文献编号建立确定性关联。引用 concern 至少回指正文引用位置；元数据差异还会同时回指参考文献条目和核验记录。

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
| `POST` | `/api/agent-review` | 触发智能审稿，输入选中文本和模式，输出审稿草稿与新增 concern |
| `POST` | `/api/manual-concern` | 将 PDF 选中文本保存为人工 concern |
| `POST` | `/api/decision` | 写入人工确认动作 |
| `GET` | `/paper.pdf` | 返回当前 run 的原始 PDF |

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

**页面能打开但 PDF 不显示。** 先检查 `/paper.pdf` 是否返回 200；如果是 404，说明 run 目录里没有发现源 PDF，需确认 `--run-dir` 和 `--paper-id` 是否对应。

**点击智能审稿提示 API Key 未配置。** 服务启动环境缺少 `PEERASSIST_OPENAI_API_KEY`。重新启动服务并只通过运行时环境注入，不要写进仓库。

**事件流中断。** 前端会自动切换到 `/api/state` 轮询；如果状态长期不更新，查看 `/tmp/peerassist-confirm-server-8766.log`。

**GitHub 首页显示作者是 Codex。** 这是本地 git author 配置导致的历史提交显示。将仓库 `user.name` 和 `user.email` 改为 GitHub 用户后，新提交会显示为对应用户；已推送的历史提交不会自动改名，除非明确执行历史重写。

**GitHub 语言统计显示 Python 100%。** 语言统计可能延迟，也可能受文件体积和 Linguist 规则影响。前端源码在 `web/peerassist-workspace`，可通过 `git ls-files web/peerassist-workspace` 确认已上传。

## 11. 安全边界

- 不在文档、代码、提交信息、飞书或日志里保存真实密钥。
- 大模型输出必须经过人工逐条确认后才能进入正式审稿意见。
- 无证据新增事实应删除、改写或标记待定，不能直接保留。
- 审稿意见应绑定 PDF 原文、证据台账、确定性核查或工具追踪，不把模型自由发挥当作证据。
