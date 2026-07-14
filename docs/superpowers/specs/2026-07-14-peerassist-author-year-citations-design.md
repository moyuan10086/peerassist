# PeerAssist 作者-年份制引用核查设计

日期：2026-07-14

## 1. 目标

在不破坏现有数字编号引用闭环的前提下，为 PeerAssist 增加可追溯、确定性的作者-年份制引用解析与关联。正文引用、参考文献、外部元数据核验、PDF 定位、候选 concern 和人工确认继续使用同一份 `citation_audit.json` 权威产物。

本迭代必须支持：

- 括号式单条引用：`(Smith, 2023)`、`(Smith et al., 2023a)`。
- 括号式多条引用：`(Smith, 2023; Wang & Li, 2022)`。
- 叙述式引用：`Smith et al. (2023)`、`Wang and Li (2022b)`。
- 同年后缀：`2023a`、`2023b`。
- 编号与作者-年份制在同一论文中共存。
- 编号和非编号 APA/Harvard 风格参考文献条目。
- 正文 mention 与参考文献原文均保留页码、章节、locator、原句和可用 bbox。
- 唯一匹配才输出 `linked`；候选不唯一、作者键不足或年份后缀冲突时保守输出 `ambiguous` 或 unsupported warning。

本迭代不使用 LLM 猜测引用关联，不因未匹配而声称文献不存在，不自动判断引用是否支持正文主张。

## 2. 兼容策略

采用同一 schema 的向后兼容扩展，不创建第二套审计产物。

### 2.1 CitationStyle

新增严格枚举：

- `numeric`
- `author_year`

### 2.2 ReferenceRecord

保留现有字段，并扩展：

- `reference_number: int | None`：数字型条目为正整数，作者-年份非编号条目为空。
- `citation_style: CitationStyle`。
- `authors: list[str]`：只保存从参考文献原文明确解析出的作者显示文本。
- `author_keys: list[str]`：规范化后的姓氏或机构作者键。
- `year_suffix: str`：限定为空字符串或单个小写字母。

数字型旧产物缺少新增字段时，通过显式默认值读取为 `numeric`，不改变旧 ID、旧确认或现有 API 行为。

作者-年份记录 ID 使用：

`R-AY-{first_author_key}-{year_with_suffix}-{normalized_raw_sha256前8位}`

无明确年份时 `year_with_suffix` 固定为 `nd`；空作者键固定为 `unknown`。这两个哨兵只用于稳定 ID，不参与自动关联，也不能显示为解析出的作者或年份。

### 2.3 CitationLink

扩展字段：

- `citation_style: CitationStyle`。
- `reference_number: int | None`。
- `mention_key: str`：规范化后的稳定关联键。
- `mentioned_author_keys: list[str]`。
- `mentioned_year: int | None`。
- `mentioned_year_suffix: str`。
- `ambiguity_reason: str`：非歧义 link 为空；歧义 link 使用受控原因，例如 `multiple_exact_candidates`、`year_suffix_conflict`、`missing_year_suffix` 或 `insufficient_author_keys`。

数字型 link 仍要求正整数 `reference_number`；作者-年份 link 必须禁止 `reference_number`，并要求至少一个作者键和明确年份。现有 link ID 保持不变；作者-年份 link ID 使用 mention evidence ID，保证同一原句中重复引用仍由字符偏移区分。

旧产物读取采用显式默认和迁移规则：`ReferenceRecord.citation_style=numeric`、`authors=[]`、`author_keys=[]`、`year_suffix=""`；`CitationLink.citation_style=numeric`、`mentioned_author_keys=[]`、`mentioned_year=None`、`mentioned_year_suffix=""`、`ambiguity_reason=""`。旧数字 link 的 `mention_key` 规范化为 `numeric:{reference_number}`，但旧 record/link/finding ID 原样保留。迁移器只补充缺省字段，不重写旧证据或人工动作。

## 3. 正文 mention 提取

### 3.1 括号式

只扫描非参考文献章节的 `text_span`、figure caption、table 和 table cell。先识别包含明确四位年份的圆括号，再按分号拆分候选项。每个候选必须满足：

- 年份为 `19xx` 或 `20xx`，可带单个 `a-z` 后缀。
- 年份前存在作者文本。
- 作者文本不以纯数字、数学运算符或统计量开头。
- 可识别一个作者、两个作者连接形式，或首作者加 `et al.`。

逗号前可出现 `see`、`e.g.`、`cf.` 等引导词，解析时从作者键中剔除，但原始 marker 保持不变。

### 3.2 叙述式

识别紧邻年份括号之前的作者短语，例如 `Smith et al. (2023)`。只允许有限长度的作者短语，避免把整句误识别为作者。叙述式和括号式发生重叠时只保留覆盖范围更完整的一条 mention。

### 3.3 稳定证据

EvidenceItem 仍使用 `EvidenceType.CITATION`，metadata 增加：

- `citation_style`
- `author_keys`
- `year`
- `year_suffix`
- `mention_key`
- `source_evidence_id`
- `start` / `end`
- `raw_marker`
- `original_sentence`
- `confidence`

ID 使用 `C-AY-{source_evidence_id}-{start}-{end}-{mention_key_sha256前10位}`。

## 4. 参考文献解析

### 4.1 条目分组

数字型条目继续按 `[n]` 开始行分组。非编号条目仅在 References/Bibliography/参考文献章节中识别，并满足以下任一可靠起始条件：

- 当前 EvidenceItem 类型为 `reference` 且包含作者文本和括号年份。
- 当前行符合保守 APA/Harvard 起始模式，且上一条已结束。

后续没有新条目起始信号的同章节行作为 continuation。不能可靠判断边界时不合并，并记录 `unsupported_author_year_reference_boundary` warning。

### 4.2 作者键规范化

- Unicode NFKC，大小写折叠，移除作者名外围标点。
- 拉丁作者优先取逗号前姓氏；无逗号时只接受模式明确的首 token 或机构作者短语。
- `&`、`and`、`和`、`与` 作为作者连接符。
- `et al.` 只表示 mention 省略，不进入作者键。
- 带连字符和撇号的姓氏保留其语义字符；空白和标点用于规范化比较。
- 中文姓名或机构作者保留完整明确名称作为键，不猜测姓与名的拆分。

### 4.3 年份与后缀

参考文献记录保存明确年份和可选后缀。没有年份的条目仍可进入记录和外部核验，但不能与作者-年份 mention 自动唯一关联。

## 5. 确定性关联

数字型关联保持原逻辑。作者-年份关联按以下顺序执行：

1. 筛选年份完全一致的参考文献。
2. mention 有后缀时要求后缀完全一致。mention 无后缀且同作者同年候选均无后缀时继续精确匹配；只要存在带后缀候选，就不能直接唯一通过。
3. 单作者 mention 要求首作者键一致。
4. 双作者 mention 要求前两个作者键有序一致。
5. `et al.` mention 要求首作者键一致；其他作者不用于放宽匹配。
6. 候选恰好一个时为 `linked`。
7. 候选为零时为 `missing_reference`，措辞仅表示“未在已解析参考文献中建立对应”。
8. 精确候选大于一个时为 `ambiguous`，绑定全部候选参考证据，`ambiguity_reason=multiple_exact_candidates`。
9. 没有精确候选但存在唯一的同作者同年份近候选，且仅年份后缀不一致时，为 `ambiguous`，绑定该候选参考证据，`ambiguity_reason=year_suffix_conflict`。为兼容该可追溯冲突，作者-年份 `ambiguous` 允许一个或多个候选；数字型 `ambiguous` 仍要求至少两个候选。
10. mention 未提供年份后缀，而同作者同年存在一个或多个带后缀候选时，为 `ambiguous`，绑定全部同作者同年候选及其参考证据，`ambiguity_reason=missing_year_suffix`。完整性校验必须按同一规则重算候选全集。

不允许使用题名相似度或外部搜索结果反向改变正文关联。外部核验只在关联完成后运行。

## 6. 审计、API 与前端

现有 audit finding、verification、concern 和人工确认契约保持不变。审计完整性校验增加按 citation style 的字段约束：

- link、mention metadata 和所有候选 record 的 `citation_style` 必须一致。
- 数字型重新校验 `reference_number` 与候选记录编号一致。
- 作者-年份制按第 5 节重新计算 mention 对候选记录的作者键、年份和后缀关系；不能因为双方 `reference_number` 都为空而通过。
- link 的 `reference_record_ids` 和 `reference_evidence_ids` 必须与重新计算的候选集合一致。
- 非编号作者-年份记录的 malformed 判断基于作者、年份、题名、DOI 等可识别书目信息，不要求数字前缀。

ReviewJob workspace API 为每条 link 输出：

- `citation_style`
- `display_label`
- 可空 `reference_number`
- 作者键、年份和后缀

每个 reference summary 携带自己的 verification。只有 `linked` 且候选唯一时才输出 link 级单一 `verification`；`ambiguous` link 的该字段必须为空，前端在每个候选参考文献下分别展示核验状态和字段差异，不能把第一个候选的 verification 冒充为整条 link 的结论。

前端规则：

- 数字型继续显示 `[n]`。
- 作者-年份制显示原始 mention，例如 `Smith et al. (2023)`。
- 引用卡继续提供正文定位、参考文献定位、字段差异、核验来源、finding 和人工操作。
- 没有链接时的中文空状态改为“未识别出可核验的引用”，不再限定“数字编号引用”。
- 不再为已成功处理的作者-年份 mention 显示旧版 unsupported warning。

## 7. 错误与降级

- 圆括号包含年份但作者结构不可靠：记录 `unsupported_author_year_citation_marker`，不生成 link。
- 非编号参考文献边界不可靠：保留原始 evidence，记录 warning，不跨条目猜测合并。
- 作者键相同且同年多条但缺少后缀：`ambiguous`。
- 正文 mention 缺少后缀而参考文献存在 `a/b` 条目：绑定全部同作者同年候选，`ambiguity_reason=missing_year_suffix`。
- mention 有后缀但参考文献没有：存在同作者同年唯一近候选时为 `ambiguous` 且 `ambiguity_reason=year_suffix_conflict`；没有近候选时为 `missing_reference`。不得忽略后缀后强行唯一匹配。
- 混合引用风格分别解析并合并结果；相同字符范围不能生成重复 mention。
- 旧 audit 仍能读取；新 audit 使用新的 parse version，避免静默重放证据已变化的人工动作。

## 8. 验证

离线测试至少覆盖：

- 括号式、叙述式、多条分号、双作者、`et al.` 和同年后缀。
- 组合中每条 mention 的独立稳定 ID 与字符偏移。
- 页码、章节、原句、bbox 和 source type 继承。
- 编号和非编号参考文献混合分组。
- 唯一、缺失、同年歧义和后缀冲突关联。
- 数字型歧义至少两个候选、作者-年份后缀冲突允许单候选且必须带受控 ambiguity reason。
- 统计年份、数据集版本、普通括号年份不误识别为引用。
- 旧数字引用 schema、audit、API、报告和前端回归。
- workspace API 输出与中文引用卡展示。
- 歧义 link 不产生伪造的 link 级 verification，每个候选单独显示自己的核验记录。
- 流水线产物完整性、人工 concern 关联和刷新恢复。

至少使用一篇真实采用作者-年份制的 arXiv PDF 跑完整 ReviewJob，确认 PDF mention 高亮、参考文献定位和人工确认可用。真实运行结果只证明流程接通，不宣称达到 CitationBench-200 指标。

## 9. 安全与隐私

解析和关联完全本地执行。只有既有外部元数据核验适配器在取得任务级同意后才能发送规范化查询；不得上传正文段落或整篇稿件。所有工具调用继续进入 tool trace。

## 10. 非目标与后续

本迭代不包括：

- 撤稿、PubPeer 或复现数据库适配器。
- “引用是否支持正文主张”的语义判定。
- BibTeX 自动修复。
- 基于 LLM 的模糊作者消歧。
- CitationBench-200 冻结评测结论。

后续在本契约上增加外部科研诚信 observation 和 citation-to-claim support edge，不改变本地 mention 到 reference 的确定性关联权威性。
