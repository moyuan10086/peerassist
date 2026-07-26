# PeerAssist Git 目录与总文档重整设计

日期：2026-07-26

## 1. 目标

将 PeerAssist 从“统一入口目录 + 当前 linked worktree + 归档主工作树”的反向依赖结构，重整为一个普通、可理解、可恢复的 Git 仓库：

```text
/root/PeerAssist/              # 唯一当前 Git 仓库
├── .git/
├── src/
├── services/
├── web/
├── docs/
│   └── PROJECT_OVERVIEW.md    # 项目唯一总文档
├── reference-materials/
└── runtime/                   # 运行数据，Git 忽略
```

完成后，开发者只需执行：

```bash
cd /root/PeerAssist
git status
```

即可确认当前版本和工作树状态，不再需要理解 `/root/.worktrees/peerassist-m0`、`current` 符号链接或归档目录中的 Git 公共元数据。

## 2. 当前问题

当前 Git 拓扑为：

- `/root/PeerAssist/archive/peerassist-mvp` 是 Git 主工作树并持有 `.git` 公共元数据；
- `/root/.worktrees/peerassist-m0` 是当前版 linked worktree；
- `/root/PeerAssist/current` 再通过符号链接指向该 linked worktree；
- systemd 通过 `/root/PeerAssist/current` 启动 8766 和 8767；
- 归档主工作树存在尚未提交的历史改动；
- `/root/PeerAssist/README.md` 不在 Git 中，与仓库内版本文档可能漂移。

这导致“当前版依赖归档版”，目录名称、Git 所有权和部署入口互相矛盾。

## 3. Git 版本模型

### 3.1 当前开发

- 当前分支继续使用 `peerassist-m0`，避免无意义改名。
- `/root/PeerAssist` 直接检出该分支。
- `moyuan` 保持项目远端；上游 FactReview 的 `origin` 保持只读参考用途。
- GitHub `moyuan10086/peerassist` 的默认分支从历史 `peerassist-mvp` 切换为当前 `peerassist-m0`。先以普通 fast-forward/new-branch push 发布，不改写远端历史。

### 3.2 发布版本

- `v0.1.0-p0` 和 `v0.1.1-p0` 继续作为不可变发布标签。
- 后续发布只增加标签和 `docs/versions/<version>.md`，不创建 `v2`、`v3` 源码目录。
- 当前两个标签经本地 OID 校验后推送到 `moyuan`；GitHub 主页面必须显示当前 README、版本和总文档入口。

### 3.3 旧版与未提交历史改动

- 旧 `peerassist-mvp` 基线保留为历史分支。
- 归档工作树中的已跟踪修改和通过安全检查的未跟踪文件写入独立分支 `archive/peerassist-mvp-local`。
- 为该保护提交创建带日期的 annotated tag，作为迁移回滚点。
- 不把归档改动合并进当前分支；需要复用时按文件或提交正常 cherry-pick。
- 完成校验前不删除旧工作树；完成后只保留 Git 分支和标签，不长期保留第二套源码目录。
- 扫描发现的密钥、私密论文、运行数据或不适合入库的二进制不得提交。此类文件写入仓库外、权限为 `0700` 的迁移保护目录，并记录相对路径、文件类型、大小和 SHA-256。
- 当前工作树、归档工作树、忽略文件和 `/root/PeerAssist/README.md` 都必须生成迁移清单。每个未跟踪或忽略文件必须被明确标记为“进入保护提交”“进入受限备份”“可重新生成”或“经用户确认排除”，不得只记录目录总数。

## 4. 非 Git 数据

- `runtime/` 保存 Review Job、论文运行目录和其他服务数据，必须加入 `.gitignore`。
- PostgreSQL、MinIO、Keycloak volume 和模型密钥不进入 Git。
- `reference-materials/` 中适合公开、无敏感内容的 Markdown 资料纳入 Git；二进制论文和私密材料继续排除。
- 迁移不得修改、重置或清空任何运行数据。
- 运行数据保护清单至少记录 Review Job ID、paper ID、文件数、总字节数、最新修改时间；不可变 PDF 和产物记录 SHA-256。PostgreSQL、MinIO 和 Keycloak 记录容器、volume 名称及 readiness，不复制或重建现有 volume。

## 5. 唯一总文档

仓库新增 `docs/PROJECT_OVERVIEW.md`，作为项目事实总入口。它只陈述经过 Git、运行探针或版本记录确认的事实，并固定包含以下章节：

1. 当前结论与产品定位；
2. 当前代码、版本、分支和目录；
3. 当前可用能力；
4. 当前运行架构和服务入口；
5. 当前限制与风险；
6. 历史版本和迁移关系；
7. 历史计划中仍有效与已暂缓的内容；
8. 文档权威顺序和更新规则。

状态词只能使用：

- **当前有效**：已在当前分支实现并有验证依据；
- **当前运行**：本机或部署环境当次探针确认；
- **历史完成**：曾完成，但不是当前运行事实；
- **历史方案**：只代表设计意图，不能描述为已实现；
- **已归档**：只供追溯，不作为启动或开发入口；
- **待完成**：当前仍缺失或未通过验收。

`README.md`、`docs/README.md`、`docs/versions/README.md` 只保存各自入口职责并链接总文档，不重复维护完整现状。

三个入口文件必须包含统一的 authority 声明和总文档链接。仓库检查器以固定锚点验证它们只保留导航、启动入口或版本索引；历史审阅、计划、操作手册和版本说明保留原职责，但页首标明 `current-reference`、`historical-review`、`historical-plan` 或 `version-record`，不与总文档争夺“当前现状”权威。

## 6. 飞书同步

飞书文档 `XuVIdkaGgoykehxox9Kc3Qnhnw2` 保持现有十章，不创建副本。仓库文档先更新和提交，飞书再按以下映射定点同步：

| 飞书章节 | Git 总文档来源 |
| --- | --- |
| 一、项目结论 | 当前结论与产品定位 |
| 二、当前能力与状态 | 当前可用能力、当前运行态 |
| 三、当前工程基线 | 当前代码、版本、分支和目录 |
| 四至六 | 仍有效的目标架构、画布和 Agent 设计，并标记为历史方案或待完成 |
| 七、安全、部署与恢复 | 当前限制与安全边界 |
| 八、迁移路线与验收 | Git 版本规则和下一阶段 |
| 九、历史里程碑与事实来源 | 标签、提交和历史审阅 |
| 十、当前行动项 | 只保留尚未完成事项 |

同步前保存带 block ID 的十章快照和最新 revision。每章使用有界局部 patch；只有接口支持 revision/CAS 前置条件，或已取得明确的独占同步窗口时才自动写入。否则只生成预览，不执行自动更新。每完成一章记录章节、输入 revision、输出 revision 和内容摘要，以便幂等续传。发现并发修改时停止后续写入，不覆盖用户内容。同步后回读十章目录和被修改章节，记录新 revision 到 `docs/peerassist_lark_sync.md`，提交第二个“同步记录”Git 提交，并确认最终 porcelain 只含执行前已知的用户文件。

## 7. 迁移步骤与回滚

### 7.1 外部路径

迁移使用三个不位于 `/root/PeerAssist` 内的同文件系统路径：

```text
/root/.peerassist-migration/candidate   新独立仓库候选
/root/.peerassist-migration/protected   受限文件、清单和配置备份（0700）
/root/PeerAssist.pre-git-layout         切换后的旧目录回滚副本
/root/.peerassist-migration/state       持久化切换阶段与路径 OID/哈希
```

执行前检查三个目标不存在、根文件系统空间足够容纳候选仓库和保护副本、所有路径位于同一文件系统以支持原子 rename。任何条件不满足即停止。

### 7.2 Git 保护与候选仓库

1. 导出两个工作树的 porcelain v2、tracked/untracked/ignored 文件清单、SHA-256、全部 `refs/heads`、`refs/tags`、远端 URL、upstream 和当前 OID。
2. 对两个工作树运行敏感信息检查。当前规格和后续总文档进入当前分支正常提交；执行前已存在的 `docs/versions/feishu-baseline-correction.xml` 不擅自提交，按清单原样保护。
3. 在归档分支创建保护提交和 `archive/peerassist-mvp-local-2026-07-26` annotated tag；不适合 Git 的内容进入 `protected` 并校验权限和 SHA-256。
4. 使用 mirror/bundle 保留所有 refs 和对象，再从该本地完整源创建 candidate，避免普通 clone 改写远端语义或遗漏本地分支。
5. candidate 恢复 `moyuan` 和 FactReview `origin`；将 `origin` 的 push URL 显式设为禁用值，避免向上游误推。恢复 `peerassist-m0` upstream 配置。
6. 对比迁移前后全部 heads/tags 的 OID，运行 `git fsck --full`，确认 candidate 的 `.git` 为独立目录，既无 alternates，也不引用旧 common dir。

### 7.3 环境与服务候选验证

1. candidate 只运行不依赖最终绝对路径的源码检查、测试和前端构建。不得复制旧 worktree 的虚拟环境，也不得把 candidate 下创建的 `.venv` 带入正式路径。
2. 将 `/usr/local/sbin/peerassist-ui-start` 的仓库版本作为唯一模板，检查并更新所有 PeerAssist systemd unit、helper、Compose 和配置中的旧路径。
3. 使用临时、明确指向 candidate 的隔离环境完成必要的 Python 导入和非正式端口冒烟；该环境不是正式 `.venv`，切换后删除。
4. 备份 systemd unit、启动器及其权限和哈希，准备明确的正向与逆向切换命令。

### 7.4 原子切换

1. 停止 8766、8767 及所有会写入 `runtime/` 的 PeerAssist worker/API；确认无残留写进程。
2. 生成 runtime 最终清单，并将 `runtime/` 和 reference materials 同步到 candidate；校验元数据、不可变文件哈希和清单。
3. 使用持久化 `state` 文件驱动两次 rename，不使用目录 exchange：先将 `/root/PeerAssist` 移到 `/root/PeerAssist.pre-git-layout`，再将 candidate 移到 `/root/PeerAssist`。每次 rename 前后 `fsync` 状态目录，并为 `initial`、`old-moved`、`candidate-active`、`verified` 四种状态定义幂等恢复。任何重启或中断后，恢复脚本根据状态和两个目录的 Git OID 决定继续或反向恢复，禁止凭目录名猜测。`old-moved` 状态必须优先恢复旧目录或继续激活已校验 candidate，不能启动任何依赖缺失路径的服务。
4. candidate 到达最终 `/root/PeerAssist` 后，按锁定依赖重新创建并安装正式 `.venv`；校验 Python shebang、editable `.pth`、包导入和所有 systemd entrypoint 均只引用最终路径。
5. 安装已验证的 systemd unit 和启动器，执行 `systemctl daemon-reload`，再启动服务。
6. 验证可执行路径、Git refs/OID、8766、8767、PDF Range、Review Job/paper 清单、PostgreSQL/MinIO/Keycloak volume 身份和平台 readiness。

### 7.5 提交点与回滚

- 服务与数据验证全部通过前属于基础设施阶段。失败时停止新服务，反向 rename 两个目录，恢复 unit/启动器，执行 `daemon-reload` 并启动旧服务。
- 服务与数据验证全部通过后记录“基础设施切换完成”提交点。此后 Git 文档或飞书同步失败不回滚已经验证的新仓库和服务，而是从文档提交或章节 revision 检查点继续。
- 只有 Git 文档提交、飞书回读和最终工作树检查通过后，才移除旧 linked worktree 注册。`PeerAssist.pre-git-layout` 至少保留到用户确认迁移结果；不得在本轮静默删除。

### 7.6 GitHub 发布

本地新布局、服务、运行数据和总文档提交全部验证通过后，才发布 GitHub：

1. 重新确认 `gh` 身份为 `moyuan10086`、目标仓库为 `moyuan10086/peerassist`、当前默认分支仍为预期的 `peerassist-mvp`，并获取远端 ref OID 清单。
2. 只执行非强制、显式 refspec 推送：`peerassist-m0:peerassist-m0`、`v0.1.0-p0` 和 `v0.1.1-p0`。远端已存在同名 ref 且 OID 不同时立即停止，不覆盖。
3. 禁止使用 `--all`、宽泛 `--tags` 或 force push。`archive/peerassist-mvp-local` 及其保护标签默认仅保留本地；除非用户另行明确批准，不发布到 GitHub。
4. 回读并比较三个远端 ref OID；一致后才将 GitHub 默认分支改为 `peerassist-m0`，再读取仓库设置验证默认分支和首页 README。
5. 把发布前后远端 OID、默认分支和时间写入迁移清单。GitHub 发布完成后不因飞书同步失败回退远端，而是保留已验证发布并从文档同步检查点继续。

## 8. 验收标准

- `/root/PeerAssist/.git` 存在，且 `git -C /root/PeerAssist status` 正常。
- `/root/.worktrees/peerassist-m0` 不再是开发或部署依赖。
- 当前分支、两个发布标签、归档分支和保护标签的 OID 与迁移清单一致；`git fsck --full` 通过，`.git` 不依赖旧目录或 alternates。
- 两个工作树的每个原有 tracked/untracked/ignored 文件都能从保护提交、受限备份或明确的可再生规则恢复。
- systemd、启动器、helper 和 Compose 不再引用 `/root/PeerAssist/current` 或旧 `.worktrees` 路径；所有实际可执行文件存在并通过冒烟。
- 正式 `.venv` 的 shebang、`.pth` 和入口脚本只引用 `/root/PeerAssist`，不引用 candidate 或旧 worktree。
- 8766、8767、PDF Range 和相关运行数据校验通过。
- runtime 的 job/paper ID、文件数、总字节数、最新时间和不可变文件哈希与切换前清单一致；数据库、对象存储和身份 volume 未更换。
- 仓库内只有一个带 `current-authority` 标记的项目总文档；三个入口文档通过确定性导航检查，历史文档带类型标记。
- 飞书仍为十章，清楚区分当前事实、历史完成、历史方案和待完成事项。
- 飞书每章的 revision 检查点可追溯，更新后目录与章节回读通过。
- GitHub 默认分支为 `peerassist-m0`，远端 branch/tag OID 与本地发布清单一致，仓库首页显示当前总文档入口；历史 `peerassist-mvp` 分支仍可访问。
- 文档检查、敏感信息检查、Git diff 检查和相关测试通过；同步记录形成第二个提交，最终状态只保留执行前已知且明确保护的用户文件。
