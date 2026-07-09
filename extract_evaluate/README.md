# Scientific Extraction Evaluation

科学文献图谱抽取结果评估工具。项目包含两类互补评估：

1. **事实性评估 factuality evaluation**：判断每个 `node` / `edge` 是否被自己的 `evidence` 支持。
2. **科学质量评估 scientific quality evaluation**：在事实性之后，判断这些已抽取出的 `nodes + edges` 是否构成高质量、可复用、不过度主张的科学知识图谱。

当前科学质量评估**不依赖原论文全文**。它只使用：

- 输入 graph 中的 `nodes`
- 输入 graph 中的 `edges`
- 每个 node/edge 自带的 `evidence`
- 整体图结构
- 可选的事实性评估报告

因此，科学质量评估中的 coverage 不是“全文覆盖率”，而是“对 graph/evidence 可见核心科学内容的覆盖质量”。报告中会明确输出 `quality_scope_note` 和 `coverage_scope_note`。

## 输入格式

输入是一个 graph JSON 文件，例如 `result1.json`：

```json
{
  "graph_metadata": {
    "task_id": "demo_scientific_graph_001",
    "source_docs": ["demo_paper.txt"]
  },
  "nodes": [
    {
      "id": "node_gene_ercc4",
      "type": "Gene",
      "name": "ERCC4/XPF",
      "description": "...",
      "properties": {},
      "evidence": ["..."]
    }
  ],
  "edges": [
    {
      "source": "node_gene_ercc4",
      "target": "node_disease_xpf",
      "type": "ASSOCIATED_WITH",
      "description": "...",
      "properties": {},
      "evidence": ["..."]
    }
  ]
}
```

要求：

- 每个 node 最好有稳定的 `id`。
- 每个 node/edge 都应带 `evidence`。
- edge 的 `source` 和 `target` 应引用 node id。
- edge 可选 `id`；如果没有，系统会生成类似 `edge_1:source->target:TYPE` 的内部 id。

## 安装

```bash
cd /mnt/petrelfs/caojie1/projects/extract_evaluate
pip install -e .
```

需要环境变量：

```bash
export OPENAI_API_KEY=your_key
export OPENAI_MODEL=gpt-4.1
```

如果使用兼容 OpenAI 的内部网关：

```bash
export OPENAI_BASE_URL=https://your-openai-compatible-endpoint
```

## 事实性评估

事实性评估逐个检查 node/edge 是否被自己的 evidence 支持。它不评价科学价值，只判断支持程度。

运行：

```bash
python3 -m scientific_eval.cli \
  --input result1.json \
  --output evaluation_outputs/result1_factuality_report.json \
  --model "$OPENAI_MODEL"
```

或安装后使用命令：

```bash
scientific-eval \
  --input result1.json \
  --output evaluation_outputs/result1_factuality_report.json
```

只检查输入解析和报告结构，不调用模型：

```bash
python3 -m scientific_eval.cli \
  --input result1.json \
  --output evaluation_outputs/result1_factuality_dry_run_report.json \
  --dry-run
```

事实性单元分数：

- `5`: evidence 完整支持抽取内容，没有实质性未支持信息
- `4`: 基本支持，仅有轻微措辞问题或无害遗漏
- `3`: 部分支持，核心事实存在，但有重要细节缺失、含糊或未支持
- `2`: 支持较弱，存在主要未支持内容或明显过度推断
- `1`: 与 evidence 矛盾、主要内容幻觉，或 evidence 为空/无关

## 科学质量评估

科学质量评估回答的问题是：

```text
这些 node / edge 即使有 evidence 支持，是否仍然是高质量科学知识？
整张图是否具有科学价值、证据校准、scope 完整性和 KG 可复用性？
```

它使用三段式流程，但第一步已经从“全文 paper profile”改为“不依赖全文的 evidence-scoped graph profile”。

```text
Step 1: Evidence Graph Profile
  从 graph + unit evidence 归纳 evidence 可见的科学主题、核心 claim、关键 scope 和预期 KG 内容。

Step 2: Unit Quality Scoring
  对每个 node/edge 评分，评价科学价值、证据强度、主张-证据匹配、scope 完整性和 KG 可复用性。

Step 3: Graph Quality Scoring
  评价整图是否覆盖 evidence 可见核心知识，是否低噪声、低冗余、结构一致、不过度主张。
```

运行完整科学质量评估：

```bash
python3 -m scientific_eval.quality_cli \
  --input result1.json \
  --output evaluation_outputs/result1_scientific_quality_report.json \
  --model "$OPENAI_MODEL"
```

或安装后使用命令：

```bash
scientific-quality-eval \
  --input result1.json \
  --output evaluation_outputs/result1_scientific_quality_report.json
```

推荐把事实性报告作为输入，让科学质量评估使用事实性 gate：

```bash
python3 -m scientific_eval.quality_cli \
  --input result1.json \
  --factuality-report evaluation_outputs/result1_factuality_report.json \
  --output evaluation_outputs/result1_scientific_quality_report.json \
  --model "$OPENAI_MODEL"
```

只检查报告结构，不调用模型：

```bash
python3 -m scientific_eval.quality_cli \
  --input result1.json \
  --output evaluation_outputs/result1_scientific_quality_dry_run_report.json \
  --dry-run
```

可选参数：

- `--factuality-report`: 可选事实性报告。支持本项目事实性评估输出，也支持 `{unit_id: {"factuality_score": 5}}` 这类映射。
- `--target-kg-goal`: 可选目标 KG 说明，例如 `biomedical mechanism graph for variant-disease-therapy relations`。
- `--batch-size`: 每次 unit quality judge 请求包含多少个 node/edge，默认 `10`。
- `--max-concurrency`: unit quality batch 并发数，默认 `4`。
- `--base-url`: OpenAI-compatible API 地址，默认读取 `OPENAI_BASE_URL`。
- `--api-key`: API key，默认读取 `OPENAI_API_KEY`。
- `--dry-run`: 只构造报告，不调用模型。

## 科学质量维度

### Unit Quality

每个 node/edge 输出以下 5 个维度，均为 1 到 5 分：

| 字段 | 含义 |
| --- | --- |
| `scientific_value_score` | 是否是有意义的科学知识，而不是元数据、泛泛概念或低信息内容 |
| `evidence_strength_score` | 当前 evidence 对该知识的科学支持强度 |
| `claim_evidence_alignment_score` | 抽取主张是否与 evidence 强度匹配，是否存在 overclaim |
| `scope_completeness_score` | 是否保留关键 scope，例如物种、细胞系、人群、突变、剂量、数据集、指标、实验条件 |
| `kg_reusability_score` | node 类型、edge 类型、方向、属性是否清晰、具体、可融合、可推理 |

`final_unit_quality_score` 含义：

- `5`: 高质量科学知识，核心、清晰、scope 完整、证据校准、KG-ready
- `4`: 良好科学知识，只有轻微问题
- `3`: 可用但需要修订，可能泛化、scope 不足或结构不够理想
- `2`: 低质量，建议降权或重抽
- `1`: 建议丢弃

对应 `decision`：

- `keep`
- `keep_with_revision`
- `downweight`
- `discard`

### Graph Quality

整图输出以下 7 个维度：

| 字段 | 含义 |
| --- | --- |
| `core_knowledge_coverage_score` | 是否覆盖 graph/evidence 可见的核心科学 claim、实体、机制、结果和限制 |
| `scientific_value_density_score` | 高价值科学知识在整图中的密度，是否被低价值内容稀释 |
| `claim_calibration_score` | 整图是否避免 association 到 causation、体外实验到临床疗效等 overclaim |
| `scope_and_context_preservation_score` | 整图是否保留关键科学上下文和限制 |
| `structural_consistency_score` | node/edge 类型、方向、描述、属性是否一致且语义清晰 |
| `redundancy_noise_control_score` | 是否避免重复节点、重复边、孤立低价值节点和泛化关系 |
| `evidence_quality_distribution_score` | 高价值知识是否主要来自强或中等强度 evidence |

`final_graph_quality_score` 含义：

- `5`: 优秀，核心内容覆盖好、证据校准、scope 完整、结构可复用
- `4`: 良好，只有少量问题
- `3`: 可用但需要明显修订
- `2`: 质量较低，存在主要科学或结构问题
- `1`: 基本不可用

## 事实性 Gate

如果提供 `--factuality-report`，科学质量评估会使用以下 gate：

- `factuality_score <= 2`: 该 unit 的 `final_unit_quality_score` 强制为 `1`，`decision = discard`
- `factuality_score == 3`: 该 unit 的 `final_unit_quality_score` 最高为 `3`
- `claim_evidence_alignment_score <= 2`: 该 unit 的最终质量最高为 `2`
- `scientific_value_score <= 2`: 该 unit 的最终质量最高为 `2`

这保证科学质量评估不会把事实性不可靠的抽取误判为高质量知识。

## 聚合规则

科学质量不会只取 node/edge 简单平均。报告会输出：

- `weighted_average_unit_quality`: unit 加权平均，node 权重 `1.0`，edge 权重 `1.2`
- `formula_graph_quality_score`: 按推荐公式计算的整图分
- `final_adjusted_graph_quality_score`: 结合 LLM 整图评分、公式评分和确定性 cap 后的最终分

公式：

```text
formula_graph_quality_score =
  0.40 * weighted_average_unit_quality
+ 0.20 * core_knowledge_coverage_score
+ 0.15 * claim_calibration_score
+ 0.10 * scope_and_context_preservation_score
+ 0.10 * structural_consistency_score
+ 0.05 * redundancy_noise_control_score
```

确定性 cap：

- `core_knowledge_coverage_score <= 2`: 最终整图分最高 `3`
- `scientific_value_density_score <= 2`: 最终整图分最高 `2`
- `claim_calibration_score <= 2`: 最终整图分最高 `2`
- `structural_consistency_score <= 2`: 最终整图分最高 `3`
- 超过 30% 已评分 unit 的 `final_unit_quality_score <= 2`: 最终整图分最高 `3`
- 超过 50% 已评分 unit 的 `final_unit_quality_score <= 2`: 最终整图分最高 `2`

## Issue Tags

科学质量报告会输出可统计的 `issue_tags`：

```text
low_scientific_value
weak_evidence
overclaim
missing_scope
missing_uncertainty
generic_relation
wrong_relation_granularity
ambiguous_node_type
redundant_node
redundant_edge
relation_target_imprecise
missing_intermediate_concept
missing_core_finding
missing_limitation
poor_graph_structure
failed_factuality_gate
```

这些标签会汇总到 `unit_statistics.issue_tag_counts` 和 `unit_statistics.common_issues`，方便统计主要质量问题来源。

## 科学质量输出结构

科学质量报告主要字段：

```json
{
  "evaluation_type": "scientific_quality",
  "quality_scope_note": "...",
  "summary": {
    "node_count": 4,
    "edge_count": 3,
    "unit_count": 7,
    "weighted_average_unit_quality": 4.1,
    "formula_graph_quality_score": 4.0,
    "final_adjusted_graph_quality_score": 4,
    "final_adjusted_decision": "usable_with_minor_revision"
  },
  "evidence_graph_profile": {},
  "unit_statistics": {},
  "unit_quality_scores": [],
  "nodes": [],
  "edges": [],
  "graph_quality_report": {}
}
```

`evidence_graph_profile` 包含：

- `profile_source`: 固定为 `graph_and_unit_evidence_only`
- `coverage_confidence`: `high | medium | low`
- `paper_or_study_type_guess`: 仅根据 graph/evidence 推测
- `evidence_visible_topic`
- `evidence_quality_score`
- `evidence_visible_core_claims`
- `major_limitations_from_available_evidence`
- `expected_kg_content`

`unit_quality_scores` 中每个 unit 包含：

- `unit_id`
- `unit_kind`
- `factuality_score`
- 5 个 unit 维度分
- `final_unit_quality_score`
- `decision`
- `issue_tags`
- `quality_issues`
- `recommended_revision`
- `rationale`
- `content`
- `evidence`
- edge 额外包含 `source_node` 和 `target_node`

`graph_quality_report` 包含：

- `dimension_scores`
- `final_graph_quality_score`
- `formula_graph_quality_score`
- `final_adjusted_graph_quality_score`
- `final_adjusted_decision`
- `deterministic_adjustments`
- `unit_quality_distribution`
- `major_strengths`
- `major_quality_issues`
- `missing_core_knowledge`
- `overclaim_risks`
- `scope_missing_issues`
- `structural_issues`
- `recommended_revision_priorities`
- `coverage_scope_note`

## 推荐运行顺序

最稳妥的流程：

```bash
python3 -m scientific_eval.cli \
  --input result1.json \
  --output evaluation_outputs/result1_factuality_report.json \
  --model "$OPENAI_MODEL"

python3 -m scientific_eval.quality_cli \
  --input result1.json \
  --factuality-report evaluation_outputs/result1_factuality_report.json \
  --output evaluation_outputs/result1_scientific_quality_report.json \
  --model "$OPENAI_MODEL"
```

最小流程：

```bash
python3 -m scientific_eval.quality_cli \
  --input result1.json \
  --output evaluation_outputs/result1_scientific_quality_report.json \
  --model "$OPENAI_MODEL"
```

最小流程也可以运行，但无法使用事实性 gate。

## 方案边界

科学质量评估不读原论文全文，所以它不能判断“整篇论文所有核心发现是否都被覆盖”。它只能判断：

```text
在当前 graph 和 evidence 可见范围内，抽取结果是否具有科学价值、证据校准、scope 完整性、结构一致性和图谱复用价值。
```

如果需要严格的全文覆盖评估，应额外提供论文全文并增加全文 paper profile 步骤。当前实现按需求刻意不依赖原论文。
