# PeerAssist 可追溯证据定位与引用核查设计

日期：2026-07-11

## 1. 目标与边界

本设计实现 PeerAssist 方法论中的第一版“可追溯证据定位 + 引用核查”闭环。系统从论文解析产物中识别正文引用与参考文献条目，建立稳定关联，接收可替换核验器返回的书目元数据，并把核验结论、原文证据、工具调用和人工处置串成一条可审计链路。

第一版必须满足：

- 每条引用核查结果必须满足按类型定义的追溯不变量：正常关联和元数据差异同时关联正文引用证据、关联决策和参考文献证据；缺失参考文献至少关联正文引用证据和 `missing_reference` 关联决策；仅参考文献侧的格式异常至少关联参考文献证据。
- 正文证据保留页码、章节、原句；解析器提供坐标时必须保留 `bbox`。
- 外部核验结果必须记录来源、查询条件、时间、字段差异和工具调用 ID。
- 外部服务失败、引用关联歧义或证据不足时，只能输出“待人工核查”。
- 引用异常只作为审稿线索，不得自动判定虚假引用、学术不端或录用结论。
- 每条线索都可由审稿人确认、改写、降级、删除或保持待核查，并保留动作记录。

第一版覆盖数字型正文引用（如 `[1]`、`[1, 3-5]`）、编号参考文献、DOI/题名/年份等书目一致性、报告输出和前端可消费的数据契约。作者年份制、撤稿/PubPeer、主张支持关系与 BibTeX 自动修复留给后续独立迭代。

## 2. 方案选择

采用 PeerAssist 内部统一引用审计模块，并把现有 FactReview/RefCopilot 检查器作为可替换适配器。

不直接把 `reference_check.json` 当最终契约，因为该文件缺少稳定的正文引用证据绑定、统一状态和人工处置关联。也不把第一版完全构建在 MCP 上，因为离线解析、确定性关联和自动化测试不能依赖外部服务可用性。

## 3. 架构与组件

### 3.1 引用证据提取器

输入：`EvidenceLedger` 中的 `text_span`、`reference` 项。

输出：新增的 `citation` 证据项，以及标准化参考文献记录。

职责：

- 从非参考文献章节的文本中识别数字型引用。
- 将范围引用展开为独立编号，例如 `[1, 3-5]` 展开为 `1, 3, 4, 5`。
- 为每个引用片段生成稳定 ID，并继承源文本的页码、章节、定位信息、原句、`bbox` 和源路径。
- 解析编号参考文献中的编号、DOI、年份和保守的题名候选；无法可靠解析的字段留空，不推断。
- 检测重复编号、缺失编号和正文引用未找到参考文献条目。

稳定 ID 使用源证据 ID、引用标记在源文本中的字符起止偏移和展开后的引用编号生成：`C-{source_evidence_id}-{start}-{end}-{number}`。同一句中的重复编号因字符偏移不同而保持唯一；范围引用展开后的编号因末段编号不同而保持唯一。基础证据 ID 或文本发生变化时视为新的解析版本，不尝试静默继承旧确认；系统通过论文哈希、解析版本和旧/新证据文本映射提示人工迁移。

解析规则保持保守：

- 只在非参考文献章节中识别方括号数字引用，默认不把单个无上下文的统计区间、数组或公式下标认作引用。
- 支持升序范围；降序、缺端点、非数字端点或展开超过 100 个编号的范围标记为 `unsupported_syntax`，不展开。
- 多行参考文献按编号起始行分组，直到下一个编号或章节结束。
- caption、脚注和表格中的引用可提取，但在记录中保留来源证据类型和上下文；无法确认上下文时降低解析置信度。
- 同一 DOI 出现在多个编号中时不自动合并，产生 `duplicate_reference_metadata` 发现供人工核查。

### 3.2 引用关联器

输入：正文引用证据、参考文献证据。

输出：`CitationLink`。

关联仅依据明确编号完成。一个编号对应一个参考文献时状态为 `linked`；无对应条目为 `missing_reference`；同一编号对应多个条目为 `ambiguous`。关联器不使用 LLM 猜测缺失编号。

### 3.3 元数据核验适配器

定义统一协议，输入标准化参考文献记录，输出来源无关的 `CitationVerification`。每次实际收到的外部响应必须保存为不可变 JSON 产物并记录路径和 SHA-256；适配器、查询、尝试、匹配和外部记录字段严格遵守 4.3 节按状态定义的条件必填矩阵，不为无候选或未执行请求的状态伪造字段。第一版提供：

- `ExistingRefcheckAdapter`：读取现有 FactReview `reference_check.json` 并映射结果。
- `OfflineMetadataVerifier`：测试和离线运行使用固定元数据映射，不访问网络。
- 无适配器或适配器失败时，生成显式 `unavailable` / `failed` 记录。

后续 Crossref、Semantic Scholar、OpenAlex 或 MCP 实现只能新增适配器，不能改变审计产物契约。

### 3.4 引用审计器

输入：引用关联和元数据核验结果。

输出：`CitationAuditFinding`，状态限定为：

- `verified`：正文引用唯一关联，核验状态为 `completed`，外部记录唯一匹配，且 DOI 精确一致，或在 DOI 缺失时题名相似度达到 0.95 且年份满足规则；必须至少比较两个可用字段，不能因外部元数据为空或不足而判定通过。
- `metadata_mismatch`：DOI、题名或年份存在可复现字段差异。
- `missing_reference`：正文明确引用的编号在参考文献中不存在。
- `ambiguous`：编号对应多个条目或核验结果无法唯一对应。
- `not_found`：外部来源未检索到，但不足以证明引用不存在。
- `insufficient_evidence`：解析信息或外部结果不足。
- `verification_failed`：核验器执行失败。
- `uncited_reference`：参考文献条目未被正文引用；默认只作为次要线索，不自动生成主要 concern。
- `malformed_reference`：参考文献条目缺少可识别书目信息或编号结构异常。
- `duplicate_reference_metadata`：多个编号共享同一规范化 DOI，或题名和年份高度一致。

除 `verified` 外，第一版所有状态均要求人工复核。`not_found` 和 `verification_failed` 生成的 concern 必须使用“暂未完成外部核验”等中性措辞。

规范化与比较规则是确定性的：

- DOI：去除 `https://doi.org/`、`http://dx.doi.org/` 和 `doi:` 前缀，去首尾空白，转小写，再做完全相等比较。
- 题名：Unicode NFKC、转小写、连续空白归一、去除 Unicode 标点后计算 token 集合相似度；相似度大于等于 0.95 为唯一匹配，大于等于 0.85 且小于 0.95 只能是 `ambiguous`，低于 0.85 不视为匹配。
- 年份：完全相等为一致；当外部来源明确同时给出 online 和 print 年份时，论文年份匹配任一者均视为一致，并记录采用的年份类型。
- 作者只用于候选排序和歧义提示，第一版不单独以作者格式差异生成 mismatch。

最终状态按以下优先级决定，前项命中后不再被后项覆盖：

1. 关联缺失或重复时分别为 `missing_reference` 或 `ambiguous`。
2. 核验执行失败或不可用时分别为 `verification_failed` 或 `insufficient_evidence`。
3. 外部来源无候选时为 `not_found`；候选不唯一或题名相似度大于等于 0.85 且小于 0.95 时为 `ambiguous`。
4. 唯一候选中任一可比字段出现实质差异时为 `metadata_mismatch`。DOI 精确匹配只决定候选身份，不覆盖题名或年份差异。
5. 唯一候选但少于两个可比字段时为 `insufficient_evidence`。
6. 唯一候选、至少两个可用字段完成比较且全部一致时才为 `verified`。

### 3.5 流水线集成

PeerAssist 阶段顺序调整为：

```text
建立基础证据台账
  -> 提取正文引用与参考文献记录
  -> 建立引用关联
  -> 读取或执行元数据核验
  -> 生成 citation_audit.json
  -> 引用审稿代理生成候选 concern
  -> 人工确认
  -> 中英文 Markdown / JSON 报告
```

新增产物：

- `citation_audit.json`：唯一规范化审计索引，引用核验记录只存索引和不可变响应产物引用。
- `citation_verifications/attempt-<attempt_id>.json`：每次外部核验的不可变原始响应，不覆盖历史尝试。

现有产物继续保留：

- `evidence_ledger.json`
- `peerassist_concerns.json`
- `human_confirmations.json`
- `tool_trace.jsonl`
- `peerassist_report.json`
- 中英文 Markdown 报告

引用核查各阶段都写入 `tool_trace.jsonl`。事件必须携带 `task_id`、`call_id`、状态、产物 ID、证据 ID、耗时和错误码。报告只消费 schema 校验成功的产物。

## 4. 数据契约

### 4.1 ReferenceRecord

```json
{
  "id": "R-1-a1b2c3d4",
  "reference_number": 1,
  "source_evidence_ids": ["P08-L014"],
  "raw_text": "[1] ...",
  "title": "",
  "year": 2024,
  "doi": "10.xxxx/example",
  "parse_confidence": 0.8
}
```

参考文献记录 ID 使用 `R-{reference_number}-{normalized_raw_text_sha256前8位}`。相同编号的不同条目因规范化原始文本哈希不同而保持唯一；完全重复的条目共享同一规范 ID，并在 `source_evidence_ids` 中记录全部来源证据。

### 4.2 CitationLink

```json
{
  "id": "citation-link-C-P02-L004-12-17-1",
  "mention_evidence_id": "C-P02-L004-12-17-1",
  "reference_number": 1,
  "reference_evidence_ids": ["P08-L014"],
  "status": "linked"
}
```

### 4.3 CitationVerification

```json
{
  "id": "V-R-1-a1b2c3d4-existing_refcheck-1-e5f6a7b8",
  "reference_record_id": "R-1-a1b2c3d4",
  "source": "existing_refcheck",
  "status": "completed",
  "query": {"doi": "10.xxxx/example"},
  "adapter": {"name": "existing_refcheck", "version": "1"},
  "source_record": {"id": "external-id", "url": "https://example.invalid/record"},
  "match": {
    "method": "doi_exact",
    "candidate_count": 1,
    "selected_candidate_id": "external-id",
    "selection_reason": "normalized DOI exact match"
  },
  "observed_metadata": {},
  "field_differences": [
    {
      "field": "year",
      "manuscript_value": "2023",
      "external_value": "2024",
      "normalized_manuscript_value": "2023",
      "normalized_external_value": "2024",
      "comparison": "mismatch",
      "rule": "year_exact_or_online_print_alias"
    }
  ],
  "raw_response_artifact": {
    "path": "citation_verifications/attempt-abc.json",
    "sha256": "hex"
  },
  "attempt_id": "abc",
  "attempt_number": 1,
  "checked_at": "2026-07-11T00:00:00Z",
  "tool_call_id": "verify-reference-ref-1",
  "error_code": ""
}
```

字段按核验状态条件必填：

| 核验状态 | 必填字段 | 可为空字段 |
|---|---|---|
| `completed` | adapter、query、attempt、raw response、match、source record、observed metadata、checked_at、tool call | error code |
| `not_found` | adapter、query、attempt、raw response、match（候选数为 0）、checked_at、tool call | source record、selected candidate、observed metadata |
| `ambiguous` | adapter、query、attempt、raw response、match（候选数大于 1，或题名相似度大于等于 0.85 且小于 0.95）、checked_at、tool call | source record、selected candidate；若保留选中候选，只能标为非权威建议 |
| `unavailable` | adapter、query、attempt、checked_at、tool call、error code | raw response、match、source record、observed metadata |
| `failed` | adapter、query、attempt、checked_at、tool call、error code；收到响应时同时要求 raw response | match、source record、observed metadata |

`completed` 必须有唯一选中候选；`ambiguous` 保存候选集合摘要但没有权威选中候选；`not_found` 禁止伪造 source record；`unavailable` 表示没有执行外部请求；`failed` 表示请求或响应映射已尝试但未成功。

核验聚合 ID 使用 `V-{reference_record_id}-{adapter_name}-{adapter_version}-{normalized_query_sha256前8位}`，对相同参考记录、适配器版本和规范化查询保持稳定。每次请求另有唯一 `attempt_id`，重试共享同一个 verification ID，但各自写入独立不可变响应；适配器版本或规范化查询变化时生成新的 verification ID，并使依赖它的 finding 进入新版本。

### 4.4 CitationAuditFinding

```json
{
  "id": "F-metadata_mismatch-c8d9e0f1",
  "status": "metadata_mismatch",
  "severity": "clarification_needed",
  "citation_link_ids": ["citation-link-C-P02-L004-12-17-1"],
  "reference_record_ids": ["R-1-a1b2c3d4"],
  "mention_evidence_ids": ["C-P02-L004-12-17-1"],
  "reference_evidence_ids": ["P08-L014"],
  "verification_ids": ["V-R-1-a1b2c3d4-existing_refcheck-1-e5f6a7b8"],
  "message": "参考文献年份与核验来源记录不一致。",
  "requires_human_review": true,
  "metadata": {}
}
```

finding ID 使用 `F-{status}-{canonical_trace_sha256前8位}`，其中 canonical trace 是排序后的 citation link ID、reference record ID、verification ID、关键差异字段和规则的规范 JSON。消息措辞、报告语言和 concern 状态不参与哈希，因此重新生成相同证据结论时可稳定重放人工动作；证据、核验或差异规则变化时生成新 ID 并触发 `needs_reconciliation`。

顶层 `CitationAudit` 记录 schema 版本、论文 ID、解析版本、引用记录、关联、核验索引、发现、覆盖统计和警告。它是关联和核验状态的唯一规范来源；原始响应目录只保存不可变尝试产物。写入顺序为原始响应临时文件、原子重命名、构建审计索引临时文件、完成全部引用完整性校验、原子重命名为 `citation_audit.json`。

所有新模型使用 Pydantic 严格枚举，并对本版本拥有的模型采用 `extra="forbid"`；不同 `schema_version` 必须通过显式迁移器读取，避免拼写错误被静默忽略。产物发布前执行引用完整性校验：finding 引用的 link、reference、verification 和 evidence ID 必须存在，核验响应 SHA-256 必须与文件一致，条件必填字段必须满足，否则整份新审计索引不发布并保留上一有效版本。

## 5. 追溯与人工处置约束

- `metadata_mismatch` finding/concern 必须直接引用 citation link、reference record、正文引用证据、参考文献证据、verification 记录和不可变原始响应。
- `missing_reference` 至少绑定正文引用证据，并明确说明未找到对应编号，不声称文献不存在。
- `verified`、`not_found`、核验派生的 `ambiguous` 和 `insufficient_evidence` 必须引用 citation link、reference record、正文及参考文献证据、verification 记录；实际收到响应时还必须引用不可变响应。
- `verification_failed` 必须引用 citation link、reference record、正文及参考文献证据、失败 verification 和 error code；失败前收到响应时还必须引用不可变响应。
- 只有参考文献条目、没有正文引用位置的异常可进入审计产物，但默认不生成主要审稿意见。
- 引用审稿代理不得新增 DOI、题名、年份或核验结论；只能消费审计产物。
- 整合器发现 evidence ID 不存在时必须丢弃或降级该 concern，并记录警告。

finding 通过 `metadata.citation_finding_ids` 与 concern 建立显式关联。人工处置继续使用现有 `HumanConfirmationAction`，动作限定为 `confirm`、`rewrite`、`downgrade`、`delete`、`mark_pending`，并保留 concern ID、finding ID、旧文本、新文本、审稿人、UTC 时间、理由和审计版本。重新生成审计时不覆盖历史动作；若 finding ID 仍存在则重放动作，若 ID 消失或证据版本变化则标记 `needs_reconciliation`，由审稿人决定迁移或关闭。

状态流为：

```text
link: linked -> verification completed with >=2 comparable fields -> finding verified | metadata_mismatch
link: linked -> verification completed with <2 comparable fields -> finding insufficient_evidence -> concern pending
link: missing_reference -> finding missing_reference -> concern pending
link: ambiguous -> finding ambiguous -> concern pending
verification: ambiguous -> finding ambiguous -> concern pending
verification: unavailable -> finding insufficient_evidence -> concern pending
verification: failed -> finding verification_failed -> concern pending
verification: not_found -> finding not_found -> concern pending
reference only: uncited_reference | malformed_reference | duplicate_reference_metadata -> minor concern pending or audit-only according to policy
finding -> concern pending -> confirmed | rewritten | downgraded | deleted | pending
```

## 6. 错误处理与降级

- 解析器无 Markdown：保留原有解析失败语义，不生成虚构引用数据。
- 引用标记语法不支持：记录覆盖警告，不做猜测。
- 参考文献重复编号：关联状态为 `ambiguous`。
- 外部检查文件缺失：核验状态为 `unavailable`，流程继续。
- 外部检查 schema 无法映射：状态为 `failed`，记录 `adapter_schema_error`。
- 网络超时或限流：状态为 `failed`，保留错误码，允许后续从检查点重试。
- 任何外部失败都不能阻断 PDF 阅读、确定性检查和人工批注。

每次核验使用 `attempt_id` 和递增的 `attempt_number`。同一 reference、适配器版本和规范化查询生成幂等键；已完成且响应哈希有效的尝试可复用。默认最多尝试三次，只对超时、限流和暂时性 5xx 重试；schema 错误和无匹配不重试。所有尝试永久保留。权威结果按最新的有效终态选择：`completed`、`not_found`、`ambiguous` 和 `unavailable` 都是有效终态；只有全部尝试均为 `failed` 时，最后一次失败才决定 `verification_failed`。较早失败不会覆盖较新的有效终态。

## 7. 自动化验证

测试必须离线、确定性运行，覆盖：

- 单个、组合和范围数字引用的识别与展开。
- 引用证据继承页码、章节、原句与 `bbox`。
- 正常关联、缺失编号和重复编号。
- DOI、年份、题名差异映射。
- 外部核验缺失、失败和 schema 异常的保守降级。
- DOI、题名和年份规范化阈值及边界条件。
- 畸形、降序、超大范围和非引用方括号数字不产生错误关联。
- 多行参考文献、caption/脚注/表格上下文以及重复 DOI。
- 稳定 ID、重新解析后的 `needs_reconciliation` 和人工动作重放。
- 重试幂等性、最大次数、权威尝试选择、原始响应哈希和引用完整性校验。
- 按 finding 类型验证追溯不变量：元数据差异同时绑定正文与参考文献证据；缺失参考文献绑定正文引用和关联决策；仅参考文献侧发现绑定参考文献证据。
- 对 `verified`、`not_found`、核验派生的 `ambiguous`、`insufficient_evidence` 和 `verification_failed` 分别验证 verification、响应或错误证据的条件必填关系。
- 阶段产物、工具追踪、中英文报告和人工确认队列集成。
- 现有 PeerAssist 测试回归。

单元测试使用内存或临时目录固定样例；联网核验仅作为可选集成测试，不作为 CI 通过条件。

## 8. 文档与同步

实现完成后更新：

- 中文 README 中的已实现能力与产物列表。
- `docs/peerassist_operation_manual.md` 中的引用核查使用和人工确认说明。
- 新增中文技术说明，描述 schema、适配器和失败降级策略。
- `docs/peerassist_lark_sync.md` 追加本轮记录。
- 飞书固定文档只执行 `append`，记录目标、实现、验证结果、提交哈希和剩余差距，不覆盖历史内容，不写入任何密钥。

## 9. 非目标

本设计不宣称达到完整 `PeerAssist-Eval-v1` 指标，也不替代冻结数据集和真实审稿人交叉实验。撤稿、PubPeer、引用语义支持关系、作者年份制、完整图像证据和复现检查仍需后续设计、实现与独立验收。
