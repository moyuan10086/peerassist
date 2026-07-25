# PeerAssist 版本管理

本目录是 PeerAssist 的唯一版本归档入口。版本归档只保存基线、Git 提交、架构变化和迁移规则，不复制旧源码、`dist` 构建产物或运行数据。

## 当前基线

- 版本：`0.1.0-p0`
- Git 分支：`peerassist-m0`
- Git 标签：`v0.1.0-p0`（源代码演进自 `b1b0f66`，包含登录主题与身份网关修复）
- 当前代码目录：`/root/.worktrees/peerassist-m0`
- 公网入口：`http://101.47.158.17:8766/`
- 版本清单：[`v0.1.0-p0.md`](v0.1.0-p0.md)

## 历史版本

旧工作目录 `/root/peerassist-review-system-20260710/peerassist` 对应旧的 `peerassist-mvp` 分支，基线提交为 `ff59216`。它只作为迁移参考，不再作为 8766 的启动目录。

需要比较历史时使用 Git：

```bash
git diff ff59216..b1b0f66 --stat
git log --oneline --decorate ff59216..b1b0f66
```

## 复用规则

1. 新功能优先扩展 `src/peerassist/platform`、`src/peerassist/confirmation_server.py`、`services/api` 和现有前端工作台，不新建平行实现。
2. 前端只维护 `web/peerassist-workspace/src`，`dist` 仅由构建生成，不手工复制或编辑旧 bundle。
3. 旧的文件型审稿工作区只通过 legacy reader/registration 适配层复用；新写入走平台 API、数据库、对象存储和异步 worker。
4. 运行数据（`runs`、Review Job `data`、PostgreSQL/MinIO/Keycloak volumes）不进入 Git，不随版本切换删除。
5. 每个版本只增加一个版本说明文件和一条 Git 基线引用；不要为同一功能生成第二套目录。

## 发布检查

```bash
git status --short
git rev-parse --short HEAD
npm --prefix web/peerassist-workspace run build
curl --fail http://127.0.0.1:8766/api/health
curl --fail http://127.0.0.1:8000/api/v1/ready
```

systemd 的 8766/8767 服务必须指向当前基线目录；平台身份栈由已有 Compose volumes 提供，不要启动旧工作目录中的服务。
