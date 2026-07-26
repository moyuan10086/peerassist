# PeerAssist 飞书同步说明

状态：`current-sync-contract`

PeerAssist 的工程事实以 Git 为准。唯一当前项目总览是
[`docs/PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md)；飞书文档是便于阅读和协作的同步视图，
不是第二套事实源。

## 固定文档

- 文档：PeerAssist 论文审核辅助系统：当前状态、历史版本与后续边界
- URL：https://my.feishu.cn/docx/XuVIdkaGgoykehxox9Kc3Qnhnw2
- Token：`XuVIdkaGgoykehxox9Kc3Qnhnw2`
- 所有者身份：用户陈昊
- 最后验证日期：2026-07-26
- 最后验证 revision：`311`
- 结构：恰好十个固定二级章节

## 当前同步状态

- 唯一当前仓库：`/root/PeerAssist`
- 当前开发分支：`peerassist-m0`
- Python 环境：`uv`，锁文件为仓库根目录 `uv.lock`
- Git 目录统一：已完成
- Git 内统一总文档：已完成
- 飞书十章重整：已完成，revision `298` 覆盖为 revision `301`，Git 统一后同步到 revision `304`，平台恢复状态同步到 revision `311`
- GitHub 分支、标签和默认分支同步：已完成；默认分支为 `peerassist-m0`
- 冗余迁移目录与旧容器清理：已完成
- 平台 API `:8000` 与 Worker 恢复：已完成，并通过 readiness 与重启核验
- 管理员后端模型配置：已完成；飞书只记录 provider 和模型，不记录密钥

## 十章职责

1. 项目结论
2. 当前能力与状态
3. 当前工程基线
4. 目标架构
5. 无限证据画布
6. 自治 Agent 与人工门禁
7. 安全、部署与恢复
8. 迁移路线与验收
9. 历史里程碑与事实来源
10. 当前行动项

后续同步只更新上述固定章节，不再追加按日期命名的开发日志。当前事实、历史完成、
历史方案、归档内容和待完成事项必须使用明确状态，不得互相替代。

## 更新规则

1. 写入前读取最新 revision 和十章目录。
2. 使用最新 revision 作为乐观并发保护，写入后重新获取目录和修改内容。
3. 工程状态必须有 Git 提交、标签、测试或运行探针作为依据。
4. 发布只增加 Git 标签和 `docs/versions/` 记录，不创建版本源码目录。
5. 密钥、令牌、私密稿件、审稿人私有信息、provider 私有响应和运行数据不得进入
   Git 或飞书。

## 历史说明

飞书 revision `298` 及以前包含逐次追加的开发日志，这些内容仍可通过飞书历史和 Git
历史追溯。经用户授权，revision `301` 将当前正文重整为固定十章；旧日志不再作为当前
项目状态读取。迁移前目录和旧 worktree 已在 Git 引用、运行数据和服务路径核验后清理；
其名称只保留在历史记录中，不再是当前入口或回滚副本。
