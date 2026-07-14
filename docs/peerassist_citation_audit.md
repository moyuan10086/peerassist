# PeerAssist 可追溯引用核查技术说明

本文档说明 PeerAssist 第一版引用核查闭环。系统只提供可复核的审稿线索，不判定虚假引用、学术不端或录用结论。

## 1. 数据流

```text
EvidenceLedger
  -> 数字型正文引用提取
  -> 参考文献记录与编号关联
  -> ExistingRefcheckAdapter / OfflineMetadataVerifier
  -> CitationVerification 不可变尝试
  -> CitationAuditFinding
  -> Concern
  -> 人工确认与中英文报告
```

核心实现：

- `src/schemas/citation.py`：严格 Pydantic 契约和状态枚举。
- `src/peerassist/citation_evidence.py`：正文引用、参考文献和稳定 ID。
- `src/peerassist/citation_metadata.py`：DOI、题名和年份的确定性规范化与比较。
- `src/peerassist/citation_verification.py`：适配器、候选选择、重试和权威尝试。
- `src/peerassist/citation_artifacts.py`：路径受限、不可覆盖的响应快照。
- `src/peerassist/citation_audit.py`：finding 决策、语义完整性和原子索引发布。
- `src/peerassist/citation_pipeline.py`：PeerAssist 阶段协调器。
- `src/peerassist/review_job_api.py`：按 ReviewJob 生成脱敏引用审计视图，绑定当前 concern 与人工状态。
- `web/peerassist-workspace/src/main.tsx`：中文引用核查侧栏、PDF 双向跳转、bbox/文字高亮和人工操作。

## 2. 稳定标识

- 正文引用：`C-{source_evidence_id}-{start}-{end}-{number}`。
- 参考文献：`R-{reference_number}-{normalized_raw_text_sha256前8位}`。
- 核验记录：由参考记录、适配器名称/版本和规范化查询共同生成，重试不会改变聚合 ID。
- Finding：由状态和规范化追溯链生成；文案变化不会改变 ID，证据或核验变化会生成新 ID。

这些 ID 用于关联人工确认历史。证据版本变化时必须重新协调，不能把旧决定直接套到新解析结果。

## 3. 核验规则

DOI 去除标准 URL/`doi:` 前缀并转小写后精确比较。题名做 Unicode NFKC、大小写、空白和标点规范化后计算 token 集合相似度：大于等于 `0.95` 才可唯一匹配，`0.85` 到 `0.95` 之间为歧义。年份精确比较；外部来源明确区分 online/print 年份时可匹配其中之一。

`verified` 必须满足唯一关联、核验完成、至少两个独立可比字段一致且没有 DOI/题名/年份差异。DOI 精确匹配只确定候选身份，不会掩盖题名或年份冲突。

## 4. RefCopilot 适配

`ExistingRefcheckAdapter` 读取 FactReview 已有 `reference_check.json`，不主动联网。该文件只保存问题条目和未验证条目，成功条目会被省略，因此“没有匹配 issue”只能降级为 `unavailable`，不能判为 `not_found`。

只有明确的 `unverified::no_match` 或相应 no-match 错误才能形成 `not_found`。warning 中存在可解析的 corrected BibTeX 时，适配器才会构造外部候选；缺失字段保持为空，不由模型补全。

## 5. 不可变响应与失败降级

实际收到的响应保存到 `citation_verifications/attempt-<attempt_id>.json`。文件发布使用目录描述符约束、禁止 symlink 跟随和 no-replace 语义；缓存复用前校验路径、attempt ID 与 SHA-256。

默认最多三次尝试，只重试 timeout、rate limit 和暂时性 5xx。`not_found`、schema 错误和适配器不可用不重试。失败不会阻断 PDF 阅读和其他审稿流程：系统生成中性待确认 concern，并在 `tool_trace.jsonl` 中保留错误码。

## 6. 前端审稿视图

`GET /api/jobs/<job-id>/workspace` 的 `state.citation_audit` 只返回审稿所需字段：覆盖统计、参考文献记录、正文 mention 证据、页码/bbox、最新外部核验来源、字段差异、finding 和已绑定 concern 状态。服务端不返回核验 query、原始响应快照路径或内部缓存路径。

论文阅读窗口的“引用核查”标签按正文链接展示卡片。正文 mention 和参考文献证据都可以驱动 PDF 跳页与高亮；需要人工复核的 finding 复用任务级 concern 决策接口，因此确认版本、finding lineage 和刷新恢复行为与主人工确认队列一致。没有链接但存在解析 warning 时，前端显示中文降级提示，禁止把零结果解释为核验通过。

## 7. 当前边界

第一版只覆盖数字型正文引用和编号参考文献。尚未实现：

- 作者年份制引用。
- 撤稿、PubPeer 和复现数据库深度交叉核查。
- 正文主张与被引论文之间的语义支持/冲突判断。
- `CitationBench-200` 冻结集上的正式指标报告。

这些能力需要独立数据集、外部服务授权和人工标注，不能用当前单元测试结果代替。
