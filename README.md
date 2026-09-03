<!-- authority: docs/PROJECT_OVERVIEW.md -->

# PeerAssist

PeerAssist 是面向高校教师和科研人员的证据驱动 AI 审稿工作台。它把 PDF 阅读、证据定位、确定性核查、审稿意见、人工确认和报告导出放在同一条工作流中。

项目当前状态、历史版本、运行服务、已实现能力和待完成事项统一记录在 [项目总览](docs/PROJECT_OVERVIEW.md)。该文档是仓库内唯一的当前事实入口。

## 当前仓库

```bash
cd /root/PeerAssist
git status
```

- 当前分支：`peerassist-m0`
- 当前发布：`v0.1.1-p0`
- GitHub：<https://github.com/moyuan10086/peerassist>
- 飞书同步文档：<https://my.feishu.cn/docx/XuVIdkaGgoykehxox9Kc3Qnhnw2>

旧的 `/root/.worktrees/peerassist-m0` 和 `/root/PeerAssist/current` 不再是当前入口。
GitHub 的 `peerassist-mvp` 仅保留为只读历史分支；`eval/PeerAssist-Eval-v1/` 是评测资料目录，不是第二套源码。

## 开发与验证

```bash
uv sync --extra platform-dev
uv run python scripts/verify_repository.py fast
npm --prefix web/peerassist-workspace run build
```

提交前完整门禁为 `uv run python scripts/verify_repository.py all`。飞书同步文档保持 10 个固定章节，只对受影响章节做精确更新，不追加平行的日期日志。

常用文档：

- [项目总览](docs/PROJECT_OVERVIEW.md)
- [开发指南](docs/development.md)
- [操作手册](docs/peerassist_operation_manual.md)
- [安全策略](SECURITY.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [版本索引](docs/versions/README.md)

## 当前本机入口

- 工作台：<http://127.0.0.1:8766/>
- ReviewJob API：<http://127.0.0.1:8767/api/health>
- 平台 API readiness：<http://127.0.0.1:8000/api/v1/ready>
- 演示 PDF：<http://127.0.0.1:8766/paper.pdf>

公网 HTTP 地址只用于公开或合成材料演示，不能处理真实未发表稿件。
