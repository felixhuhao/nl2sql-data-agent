# Evaluation Design

## 目标

Eval 的目标不是给项目贴一个 pass rate，而是回答三个问题：

- 失败发生在哪一层：retrieval、SQL generation、Guard、execution、chart 还是 explainability？
- 改动是否造成回归？
- 真实模型生成的 SQL 是否语义等价，而不只是字符串相似？

核心文件：

- `scripts/run_smoke_eval.py`
- `scripts/run_semantic_guard_eval.py`
- `evals/smoke_cases.yaml`
- `evals/semantic_guard_cases.yaml`
- `evals/reports/*.md`
- `backend/tests/test_smoke_eval_runner.py`

## Case 结构

每个 smoke case 包含：

```yaml
- id: recent_30d_channel_sales
  type: normal
  question: 按渠道统计最近30天销售额
  tags: [sales, channel]
  mock_sql: ...
  expected:
    should_execute: true
    result_columns: [channel_name, sales_amount]
    chart_type: bar
    required_tables: [...]
    required_columns: [...]
    join_paths: [...]
```

Datasource targeting supports both the legacy scalar shape and the new parity shape:

```yaml
datasource: clickhouse_ecommerce        # legacy singleton, still valid
datasources: [duckdb_ecommerce, clickhouse_ecommerce]  # parity anchor
```

Rules:

- `datasource` normalizes to a singleton list.
- `datasources` runs the same case once per listed datasource.
- Setting both keys is invalid and fails fast.
- Setting neither defaults to `duckdb_ecommerce`.
- Values are datasource instance names, not dialect nicknames.

Retrieval closeout cases may also use deterministic retrieval fixtures and coverage expectations:

```yaml
requires_retrieval_recovery: true
retrieval_fixture:
  tables: [...]
  columns: [...]
expected:
  coverage:
    pre_band: low
    post_band: high
    expanded: true
    fallback_used: false
```

`requires_retrieval_recovery` cases are skipped unless retrieval recovery is enabled, except in calibration mode. This keeps default flags-off smoke stable while allowing closeout runs to assert the expansion/fallback paths.

Retrieval fixtures intentionally stub only the recalled assets. Coverage scoring and graph expansion still read the live seeded metadata relationships for the selected datasource. That means incomplete-recall fixtures are deterministic about **what was recalled**, but their structural validity depends on the seeded relationship graph remaining consistent with the archetype. If seed relationships change, the `expected.coverage.pre_band` / `post_band` assertions should fail loudly and the fixture must be updated with the new graph shape in mind.

## Mock Provider

Mock 模式直接注入 `mock_sql`，用于稳定验证：

- SQL Guard
- executor
- retrieval
- focused context
- chart recommendation
- repair loop
- safety cases

Mock eval 保持严格断言，作为回归基线。

## Real Provider

DeepSeek real eval 走真实 LLM provider 和真实 prompt，用较宽但可解释的规则判断：

- SQL 必须 Guard 通过并执行成功。
- 如果 case 有参考 SQL，执行参考 SQL 并比较结果集。
- 保留 expected SQL pattern、OLAP intent、chart 等必要检查。

## Result Equivalence

真实模型可能生成不同但等价的 SQL：

- 列别名不同。
- join 写法不同。
- 多返回非核心辅助列。
- 少返回 `*_id` / `*_key` 但保留可读维度和指标。

因此 real eval 不做 SQL 字符串比较，而是：

```text
actual SQL -> Guard -> Execute -> QueryResult
reference SQL -> Guard -> Execute -> QueryResult
compare normalized result sets
```

比较规则：

- 行顺序不敏感。
- 数值允许小容差。
- 实际结果多列时，如果列名包含参考列，则投影到参考列比较。
- 实际结果少列时，只允许少掉可选 identifier 列（`*_id` / `*_key`）。
- 结果不等价时仍标记 `result_mismatch`。

这样可以把“评测器误杀”和“SQL 真错”分开。

## 错误分类

Runner 记录 `error_category`，例如：

- `retrieval_miss`
- `sql_generation_timeout`
- `sql_generation_mismatch`
- `dialect_mismatch`
- `sql_invalid`
- `guard_blocked`
- `fanout_risk`
- `guard_mismatch`
- `execution_error`
- `result_mismatch`
- `chart_mismatch`
- `explainability_mismatch`

这让每次失败都有明确归因，而不是只看 pass/fail。

## Semantic Guard Eval

Phase 1 semantic grounding uses a separate warn-only eval runner:

```bash
backend/.venv/bin/python scripts/run_semantic_guard_eval.py --semantic-mode warn --retries 1
```

The case file pairs supported no-warning questions with unsupported adjacent-substitution / omission questions. It also includes `type: verifier_only` cases with synthetic full-schema metadata, used to test Stage A support decisions when a schema truly contains returned/cancelled/deleted-style fields or values. The runner records generated SQL, warning count, warning concepts, required concepts, failure kinds, refutation confirmation, verifier availability, and writes `evals/reports/semantic_guard_latest.md` (ignored by git like other generated reports). These results are evidence for Phase 2 promotion; they do not enable `enforce` mode by themselves.

Use targeted reruns during iteration:

```bash
backend/.venv/bin/python scripts/run_semantic_guard_eval.py --case-id verifier_refund_does_not_support_return_rate
backend/.venv/bin/python scripts/run_semantic_guard_eval.py --promotion-pattern concept_absent_full_metadata --limit 1
```

Provider outages, rate limits, or billing errors are not semantic regressions. Treat those cases as inconclusive, fix the provider issue, and rerun only the impacted `--case-id` values instead of chasing a perfect monolithic run.

Full-corpus runs are reserved for checkpoint validation because workflow cases call the generator, verifier, SQL Guard, and executor.

## 报告内容

`evals/reports/*.md` 包含：

- Summary
- Datasource Summary
- Phase 6.5 OLAP Analytics
- Error Distribution
- Skipped Cases
- Retrieval Expected Hits
- Case Results
- Failure Details
- Retrieval Details

关键指标包括 pass rate、fallback count、repair count、flags-off vs flags-on focused context chars/delta、focused context reduction、retrieval coverage transition、reference result matches、chart distribution 和 per-datasource pass rate。

## Retrieval Expansion Closeout

Retrieval recovery validation adds three runner behaviors:

- **Coverage path assertions**: `expected.coverage` checks pre-expansion band, post-context band, `expanded`, and `fallback_used`.
- **Parity anchors**: any case expanded across multiple datasources must have the same final coverage band across those datasource runs; divergence fails the case group.
- **ClickHouse closeout gate**: `--require-clickhouse` fails if any ClickHouse-listed case is skipped because ClickHouse is unavailable.

Useful commands:

```bash
PYTHONPATH=. RETRIEVAL_EXPANSION_ENABLED=true RETRIEVAL_FALLBACK_MODE=on \
  python scripts/run_smoke_eval.py --provider mock

PYTHONPATH=. python scripts/run_smoke_eval.py \
  --provider mock \
  --retrieval-calibration \
  --retrieval-thresholds 0.5,0.6,0.7 \
  --report-path evals/reports/retrieval_calibration.md

PYTHONPATH=. python scripts/run_smoke_eval.py \
  --provider mock \
  --require-clickhouse
```

Calibration mode temporarily enables retrieval expansion/fallback in-process and sweeps the supplied thresholds. The report records recovery cases, fallback-path cases, high-confidence regressions, fallback count, and average context delta. Candidate thresholds are observations, not pass/fail expectations.

The high-confidence holdout is fixed at the reference threshold loaded from settings before the sweep starts. Swept thresholds may reclassify those cases to low and trigger expansion/fallback; if that changes the final band or focused-context size, calibration reports it as a high-confidence regression.

## 当前结果口径

常用验收：

- Mock smoke：50/50 passed。
- Backend tests：328 passed。
- DeepSeek real eval：18/18 passed（DuckDB real cases，依赖 `DEEPSEEK_API_KEY`）。

ClickHouse 未启用时，ClickHouse case 会标记为 skipped，而不是失败。

## 为什么 Safety Case 默认用 Mock

真实 LLM 遇到危险请求时可能拒答自然语言，或者输出解释而不是 SQL。这验证的是模型安全策略，不是系统 Guard。项目要验证的是：**只要危险 SQL 到达执行链路，Guard 必须拦截**。所以 safety case 使用 Mock 确定性注入危险 SQL。

## Eval 如何指导迭代

- 如果 `retrieval_miss` 上升，检查 alias/metric/verified query 和 vector index。
- 如果 `scope_guard` 增多，检查 prompt 是否发明字段，或 schema context 是否漏列。
- 如果 `result_mismatch` 增多，先看 reference result match，再判断是 SQL 真错还是评测期望过窄。
- 如果 `chart_mismatch` 增多，检查 recommender 和 OLAP intent。

## 方法学定位：与市场标准对照（2026）

### NL2SQL 评测的通用度量

业界主要用三种度量，可信度递增：

- **Exact Match (EM)**：预测 SQL 与 gold SQL 的字符串/AST 是否等价。已基本弃用作主度量——一个问题有多种正确写法（join、alias、列序不同），EM 会大量误杀。
- **Execution Accuracy (EX)**：执行预测 SQL 与 gold SQL，比较结果集。**当前主流度量**，对语法差异鲁棒。
- **Test-Suite Accuracy**：EX 的弱点是 false positive（错 SQL 在单个实例上凑巧返回对的行）。Test-suite 在多个 fuzz 过的 DB 实例上执行，只有真正正确的查询才处处通过。Spider 自 2020 起的官方度量，研究级严谨基线。
- BIRD 另加 **Valid Efficiency Score (VES)**：不仅正确，还要高效。

公开 benchmark：Spider（10,181 问 / 200 DB，EM 已近饱和 ~91%）、BIRD（12,751 对，脏数据+效率，EX ~73%）、Spider 2.0（agentic、企业级，仅 ~21%，是当前真正前沿）。

企业/生产实践：从生产 query log 构建 golden dataset，按难度/类型分层，用 observability 持续补充失败样本；对歧义题用带 rubric 的 LLM-as-judge；做 module-level 评测（NL2SQLBench 拆 schema-selection / candidate-generation / query-revision；NL2SQL360 多角度打分），定位失败发生在哪一阶段。

### 本项目的对齐关系

| 市场实践 | 本项目 |
|---|---|
| Execution Accuracy（非 EM） | ✅ real eval 执行 actual vs reference SQL 比 **normalized 结果集**（行序不敏感、数值容差、列投影），刻意避开字符串比较，即标准 EX。 |
| Golden set | ✅ 人工 curated YAML：`smoke_cases.yaml`、`semantic_guard_cases.yaml`。 |
| Module-level 归因 | ✅ **强项**——`error_category` 把失败拆到 retrieval/generation/guard/execution/chart，与 NL2SQLBench/NL2SQL360 主张一致，多数自建 eval 没有。 |
| 确定性回归 vs 真实模型 | ✅ Mock provider（回归基线）与 DeepSeek real eval 分离"系统 bug"与"模型波动"。 |
| 区分评测器误杀与真错 | ✅ `result_mismatch` + reference-result-match 追踪。 |

方法学上，项目独立落到了正确度量（EX + 结果等价）与新兴最佳实践（module-level 归因），高于一般自建 eval 的中位水平。

### 已知取舍（接受）

- **规模**：约 50 smoke + 18 real case，vs Spider/BIRD 数千条。足够做回归 harness，不构成统计意义上的 accuracy 主张——本 doc 定位 eval 为"工程仪表盘"而非 pass rate，正是出于此。
- **无 test-suite accuracy**：单 DB 实例，理论上存在 EX false positive。当前 curated 规模下风险低，但如实记录。
- **未跑公开 benchmark**：无法给出"BIRD X%"这类外部可识别数字。若需对外数字，最小动作是对 **BIRD 子集**跑一遍流水线。
- **无 LLM-as-judge**：改用确定性结果等价——在适用范围内更严格，但无法评判开放式/歧义答案。

结论：评测方法本身健全且成熟；缺口集中在**规模与外部可比性**，非方法问题。需要生产真实性时，标准做法是用真实 query log 按难度分层扩充 golden set。

## 技术说明

> 我把 eval 做成了工程仪表盘，而不是几条手工样例。Mock eval 保证系统确定性回归，real eval 验证真实模型表现。失败会按 retrieval、generation、Guard、execution、chart 等阶段归因；真实 SQL 用执行结果做等价判断，避免把同义 SQL 误判失败。
