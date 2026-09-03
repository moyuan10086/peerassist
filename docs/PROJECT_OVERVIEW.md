<!-- current-authority -->

# PeerAssist 项目总览

本文是 PeerAssist 当前状态、历史版本和后续边界的唯一事实入口。其他 README、版本说明、历史审阅和实施计划只承担各自范围内的导航或记录职责；发生冲突时，以当前 Git 状态、运行探针和本文为准。

## 一、当前结论与产品定位

**当前有效。** PeerAssist 是面向高校教师和科研人员的证据驱动 AI 审稿工作台。它围绕一篇论文完成 PDF 阅读、证据定位、确定性核查、审稿意见生成、人工确认和报告导出，不替代审稿人的最终判断。

当前产品不是投稿管理系统，不负责作者投稿、编辑分稿、审稿人邀请、期刊出版或自动接收/拒稿。OpenReview、OJS、JOSS 和 Kotahi 只作为交互与流程参考。

## 二、当前代码、版本与目录

**当前有效。** 唯一代码仓库是：

```text
/root/PeerAssist
```

```text
/root/PeerAssist/
├── .git/                    独立 Git 元数据
├── src/                     审稿领域与兼容服务
├── services/                FastAPI 平台 API、bootstrap 和 worker
├── web/                     React/Vite 工作台
├── docs/                    产品、架构、操作与版本文档
├── reference-materials/     可公开的研究背景资料
├── eval/PeerAssist-Eval-v1/  评测资料，不是源码版本
└── runtime/                 本机运行数据，Git 忽略
```

- 当前开发分支：`peerassist-m0`
- `peerassist-mvp` 仅作为 Git 历史分支保留；它是当前分支的祖先，不是活动开发入口。
- `eval/PeerAssist-Eval-v1` 仅存放评测清单、样例和验收资料，不代表第二套产品源码。
- 当前发布基线：`v0.1.1-p0`，提交 `ef8b00b`
- 上一发布基线：`v0.1.0-p0`，提交 `30b048f`
- 当前分支还包含 Git 目录重整、`uv` 锁文件和文档治理提交；发布标签保持不可变。
- Python 环境使用 `uv` 和仓库根目录的 `uv.lock` 管理，不复制旧 `.venv`。

旧路径 `/root/.worktrees/peerassist-m0`、符号链接 `/root/PeerAssist/current`、迁移目录 `/root/PeerAssist.pre-git-layout` 和临时迁移状态目录均已在 Git 引用、运行数据与服务路径核验后清理。它们只属于历史拓扑，不再保留为开发、部署或回滚入口。

## 三、当前可用能力

**当前有效。** 当前代码包含：

- React、TypeScript、Vite、PDF.js 和 lucide-react 构成的中文 PDF 审稿工作台；
- PDF Range 读取、单页/双页、缩放、文字选择、证据跳转与高亮；
- 证据台账、确定性核查、引用核查、专业审稿代理和本地降级；
- concern 队列及确认、改写、降级、删除、待定等人工动作；
- ReviewJob 状态、取消、重试、事件时间线、结构化 `review_result.json`、服务端草稿和不可变报告；
- MCP/Skills/模型调用追踪与产物下载；
- FastAPI、PostgreSQL、OIDC、S3/MinIO、组织/项目 RBAC 和 worker 的平台化代码与契约；
- 模型设置的管理员写权限和普通老师安全状态投影。

能力存在于代码不等于当前所有平台服务都在运行，当前运行态以下一节为准。

## 四、当前运行架构与入口

**当前运行，核验日期 2026-07-26。**

| 入口或组件 | 当前状态 | 说明 |
| --- | --- | --- |
| `http://127.0.0.1:8766` | 运行中 | `confirmation_server`，当前 PDF 工作台与兼容 API |
| `http://127.0.0.1:8767` | 运行中 | 文件型 ReviewJob API |
| PDF Range | 可用 | 返回 `206`，演示 PDF 头为 `%PDF-` |
| PostgreSQL | 容器健康 | 平台数据服务，volume 保持不变 |
| MinIO | 容器健康 | S3 兼容对象存储，volume 保持不变 |
| Keycloak | 容器健康 | OIDC 身份服务，volume 保持不变 |
| 平台 API `http://127.0.0.1:8000` | 运行且 ready | `/api/v1/ready` 返回数据库、身份和对象存储均 ready |
| 平台 Worker | 运行中 | 从 PostgreSQL 队列领取任务；模型设置卷以只读方式挂载 |

公网 `http://101.47.158.17:8766/` 只适合公开或合成材料的演示。真实未发表稿件必须在 HTTPS、受控身份和明确外部模型授权下处理。

本地环境与验证入口：

```bash
cd /root/PeerAssist
uv sync --extra platform-dev
uv run python scripts/verify_repository.py fast
npm --prefix web/peerassist-workspace run build
curl --fail http://127.0.0.1:8000/api/v1/ready
```

平台栈由 Git 中的 `infrastructure/compose/compose.m1.yml`、`infrastructure/compose/compose.platform.yml` 和 `deploy/systemd/` 管理。运行数据继续使用已有 PostgreSQL、Keycloak、MinIO、Worker scratch 和模型设置持久卷；bootstrap 状态与旧兼容读取目录固定在 `/root/PeerAssist/runtime/platform/`，不依赖 worktree 或 `/tmp` 临时源码目录。

**当前管理员模型配置。** 后端使用 `openai-compatible` provider，地址为 `https://deepkey.top/v1`，模型为 `gpt-5.6-sol`，配置已启用并由 Worker 成功读取。包含密钥的完整配置只保存在服务器端 model-settings 持久卷中，文件权限为 `0640`；Git、飞书和前端状态接口都不得保存或返回原始密钥。

## 五、当前限制与风险

**待完成。** 当前已确认的主要限制：

- 公网仍是 HTTP，不能承载真实未发表论文、正式会话或敏感模型调用；
- 前端主要集中在 `main.tsx` 和 `styles.css`，维护和行为回归成本较高；
- 浏览器自动化覆盖不足，核心教师主流程仍需要稳定的 Playwright 回归；
- 当前仓库全量检查仍有既有问题：3 个 Ruff finding、3 个缺失 Git LFS PDF fixture 测试和 1 个 OpenAPI 漂移测试；
- npm audit 当前报告 1 个 high 风险依赖，需要独立评估升级影响；
- 作者年份制引用、撤稿/PubPeer 核验和引用语义支持仍未完整实现；
- 无限证据画布和 Next.js 应用壳尚未实现。

## 六、历史版本与迁移关系

| 状态 | 名称 | Git 依据 | 说明 |
| --- | --- | --- | --- |
| 历史来源 | FactReview | `origin/main` | PeerAssist 的上游代码底座 |
| 已归档 | `peerassist-mvp` | 本地分支 `ff59216`；GitHub 历史分支 `1e73c93`；均为当前分支祖先 | 旧文件型工作区和历史实现参考，不参与当前 CI/部署 |
| 已归档 | 本地旧版保护 | 分支 `archive/peerassist-mvp-local` | 保存旧工作树尚未提交的安全内容；默认不发布到 GitHub |
| 历史完成 | `v0.1.0-p0` | 标签指向 `30b048f` | 第一版可部署教师审稿闭环基线 |
| 当前发布 | `v0.1.1-p0` | 标签指向 `ef8b00b` | 教师首次使用和模型状态补丁 |
| 当前开发 | `peerassist-m0` | `/root/PeerAssist` 的 HEAD | 在发布基线上继续维护，不创建平行源码目录 |

2026-07-26 之前的结构以 `/root/PeerAssist/current` 指向 `/root/.worktrees/peerassist-m0`，Git 公共元数据却位于归档工作树。该反向依赖已通过独立仓库迁移消除。

### 功能差异摘要

| 维度 | 历史 `peerassist-mvp` | 当前 `peerassist-m0` |
| --- | --- | --- |
| 阅读与证据 | PDF 阅读、Range 加载和基础证据定位 | 保留上述能力，并加入证据台账、确定性核查、引用核查和 finding/evidence 绑定 |
| 审稿任务 | 文件型 ReviewJob 和本地运行态 | PostgreSQL 持久化任务、取消/重试、事件时间线、worker lease 和可恢复阶段清单 |
| 身份与组织 | 本地入口，无租户边界 | Keycloak OIDC、服务端会话、组织/项目成员、RBAC、CSRF 与限流 |
| 模型与外部服务 | 全局本地模型调用 | 管理员模型设置、任务级授权、provider/policy/data-scope 门禁和只读状态投影 |
| 草稿与导出 | 本地草稿或请求内生成结果 | 服务端版本化草稿、CAS 冲突保护、不可变报告版本和异步导出 |
| 部署与治理 | 单体确认页和源码工作树 | FastAPI 平台、PostgreSQL/MinIO/Keycloak、systemd/Compose、备份恢复、uv 锁定依赖和仓库契约 |

这张表是基于 `peerassist-mvp` 为 `peerassist-m0` 祖先的 Git 历史归纳；它不是第二套源码清单。当前实现只维护 `peerassist-m0`，历史能力通过 Git 提交、分支和标签追溯。

## 七、历史方案中仍有效与已暂缓内容

**历史方案，仍有效的原则：**

- 领域层不依赖 FastAPI、身份厂商、数据库厂商或对象存储厂商；
- PostgreSQL、OIDC 和 S3 通过端口与适配器替换；
- PDF 是主阅读入口，证据和意见必须反向定位原文；
- Agent 只能通过受审计工具操作；外发、覆盖人工内容、发布报告和分享需要人工门禁；
- ReviewJob、Artifact、Concern、Decision 和 ReviewEvent 是可恢复工作流的核心对象；
- 新功能继续在当前仓库和分支增量实现。

**历史方案，当前暂缓：**

- 无限证据画布；
- Next.js 全栈应用壳；
- 多人实时协作和 CRDT；
- 完整投稿、分稿和出版流程；
- 大规模 SaaS 配额、计费和复杂权限矩阵。

恢复这些方案前，先完成 HTTPS、主流程自动化和现有前端边界拆分。

## 八、文档权威顺序与更新规则

文档权威顺序：

1. Git 提交、标签、运行探针和可执行契约；
2. 本文 `docs/PROJECT_OVERVIEW.md`；
3. `README.md`、`docs/README.md` 和 `docs/versions/README.md` 的导航信息；
4. 当前操作手册、PRD、ADR 和 API 约定；
5. 历史审阅、设计规格、实施计划和飞书历史里程碑。

更新规则：

- 当前事实只在本文维护，入口文件只链接，不复制整段现状；
- 发布版本只用 Git 标签和 `docs/versions/` 记录，不创建版本源码目录；
- 历史审阅和计划保留当时事实，并以状态标记说明它们不是当前现状；
- 飞书固定十章是本文的同步视图，不是独立事实源；先提交 Git，再定点同步飞书；
- 运行数据、密钥、私密稿件和 provider 私有响应永不进入 Git 或飞书。
