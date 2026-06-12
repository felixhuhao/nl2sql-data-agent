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

关键指标包括 pass rate、fallback count、repair count、focused context chars、focused context reduction、reference result matches、chart distribution 和 per-datasource pass rate。

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

## 技术说明

> 我把 eval 做成了工程仪表盘，而不是几条手工样例。Mock eval 保证系统确定性回归，real eval 验证真实模型表现。失败会按 retrieval、generation、Guard、execution、chart 等阶段归因；真实 SQL 用执行结果做等价判断，避免把同义 SQL 误判失败。
