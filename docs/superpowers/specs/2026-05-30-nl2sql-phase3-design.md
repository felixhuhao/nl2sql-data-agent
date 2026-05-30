# NL2SQL Phase 3 设计文档

> 日期: 2026-05-30
> 状态: 已完成
> 前置: Phase 2.5 语义资产管理已完成
> 范围: 评测体系与真实模型验证

## 1. 关键决策记录

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 评测执行方式 | 继续复用现有 smoke runner 编排 | 当前 runner 支持 `mock_sql` 直注入、retrieval 检查和 focused context 对比；直接改成 `run_query_workflow` 会丢失 mock_sql 注入点 |
| Case 格式 | 扩展现有 smoke_cases.yaml | 不新建格式，Phase 1/2 case 直接复用 |
| Mock vs Real 模式 | 同一份 case 文件，按 `provider` 字段区分 | Mock case 用确定性 SQL 验证系统闭环；Real case 验证真实模型 SQL 生成能力 |
| 报告格式 | Markdown | 与现有 smoke report 一致，可直接在 GitHub 渲染 |
| 错误归因 | runner 层按阶段捕获异常并分类 | 当前 AgentState 只在 SQL Guard 拦截时设置 stopped_at，provider / executor 异常需要 runner 自己归因 |
| Real LLM 调用 | 同步串行 | 避免并发限流，30 case × ~5s = ~2.5min 可接受 |
| 不做 | HTML 报告、prompt 版本对比、CI 集成 | 推到后续，Phase 3 聚焦量化能力 |

## 2. Phase 3 核心能力

1. **Case 扩展到 30+** — 覆盖时间、地区、渠道、品类、客单价、别名、安全拦截、fallback、retrieval 验证
2. **错误归因分类** — 自动归类为 retrieval miss / sql_invalid / guard_blocked / execution_error / result_mismatch
3. **Real LLM Benchmark** — 支持 DeepSeek provider 批量跑 eval；显式 real 模式无 API key 时直接报错
4. **Markdown 报告增强** — 输出错误类型分布、per-case 详情（生成 SQL / normalized SQL / 错误原因 / 耗时）
5. **README 更新** — 展示最新 eval 结果和 Phase 3 能力

## 3. 现有基础

### 3.1 已就绪

| 组件 | 说明 |
|------|------|
| `evals/smoke_cases.yaml` | 30 条 case，YAML 格式，含 retrieval / guard / execution 验证 |
| `scripts/run_smoke_eval.py` | 完整的 smoke runner，含报告生成 |
| `MockLLMProvider` | 确定性 SQL 生成，支持 verified query 匹配 |
| `DeepSeekProvider` | 真实 LLM 调用，已实现 |
| `run_query_workflow()` | 完整 Agent 工作流，返回 AgentState；Phase 3 不直接替换当前 smoke runner，除非先补 `StaticSQLProvider` 或等价注入点 |
| `SmokeResult` / `_write_report()` | 评测结果数据结构和 Markdown 报告 |

### 3.2 已补齐

- Case 数量已扩展到 30 条。
- Smoke runner 已支持错误归因分类。
- Runner 已支持 `--provider mock|deepseek`。
- 报告已包含 per-case SQL、错误归因、耗时、provider 和 skipped cases。

## 4. Case 扩展设计

### 4.1 新增 Case 规划

在现有 15 条基础上新增 15 条，覆盖以下场景：

**正常查询（10 条）：**

| ID | 问题 | 验证重点 |
|----|------|---------|
| `recent_30d_user_orders` | 最近30天下单最多的10个用户 | dim_users join, TopN |
| `recent_30d_channel_user_count` | 按渠道统计最近30天活跃用户数 | 多表 join, COUNT DISTINCT |
| `recent_30d_avg_order_amount` | 最近30天平均订单金额 | 聚合, AVG |
| `product_sales_rank` | 商品销量排行 | ORDER BY, LIMIT |
| `region_channel_cross` | 按地区和渠道交叉统计销售额 | GROUP BY 多维度 |
| `daily_order_trend` | 最近30天每日订单数趋势 | 时间序列, line chart |
| `top_category_by_region` | 各地区最畅销品类 | 子查询或窗口函数 |
| `user_repeat_purchase_rate` | 最近30天复购率 | 复杂聚合；Phase 3 先标 `provider: mock` |
| `recent_7d_vs_30d_sales` | 最近7天与30天销售额对比 | 时间段对比；当前 schema context 只显式定义最近30天，Phase 3 先标 `provider: mock` |
| `payment_distribution` | 订单金额分布 | CASE WHEN, 分组统计；Phase 3 先标 `provider: mock` |

**安全拦截（3 条）：**

| ID | 问题 | 验证重点 |
|----|------|---------|
| `unsafe_update_orders` | 把所有订单金额改为0 | UPDATE 拦截 |
| `unsafe_truncate_table` | 清空 fact_orders | TRUNCATE 拦截 |
| `unsafe_read_parquet` | 从 parquet 文件导入数据 | read_parquet 拦截 |

安全拦截 case 默认标 `provider: mock`。Real LLM 可能拒答危险请求或输出自然语言解释，无法稳定测试 SQL Guard；Phase 3 的安全 case 目标是验证 Guard，而不是验证 LLM 自身安全策略。

**检索验证（2 条）：**

| ID | 问题 | 验证重点 |
|----|------|---------|
| `phase2_date_alias` | 最近30天订单量 | "最近30天"别名命中 dim_date |
| `phase2_product_name_alias` | 商品名称列表 | "商品名称"别名命中 dim_products.name |

### 4.2 Case 格式扩展

现有格式完全兼容，新增字段为可选：

```yaml
- id: recent_30d_avg_order_amount
  type: normal
  question: 最近30天平均订单金额
  tags: [sales, aggregation]
  mock_sql: >-
    SELECT AVG(o.payment_amount) AS avg_order_amount
    FROM fact_orders o
    JOIN dim_date d ON o.date_key = d.date_key
    WHERE d.date_value BETWEEN DATE '2025-12-02' AND DATE '2025-12-31'
  expected:
    should_execute: true
    matched_query_id: null
    retrieval:
      fallback_used: false
      required_tables: [fact_orders, dim_date]
      required_metrics: [aov]
    required_tables: [fact_orders, dim_date]
    result_columns: [avg_order_amount]
    min_row_count: 1
    chart_type: table
    join_paths:
      - fact_orders.date_key -> dim_date.date_key
```

**新增可选字段：**

```yaml
  # Real LLM 模式专用：期望 SQL 包含的关键结构
  expected_sql_keywords: [AVG, payment_amount]

  # 标记只跑 Mock 或只跑 Real
  provider: mock  # 默认 both，可选 mock / real；safety 和复杂口径 case 优先 mock
```

Provider 规则：

- 未设置 `provider`：mock 和 real 都可运行。
- `provider: mock`：只跑 Mock，适合安全拦截、复杂口径、当前 semantic layer 未明确支持的 case。
- `provider: real`：只跑 Real，适合真实模型探索性验证，不要求 Mock provider 覆盖。

## 5. 错误归因分类

### 5.1 错误类型定义

| 错误类型 | 含义 | 判定条件 |
|---------|------|---------|
| `retrieval_miss` | 检索未命中必要资产 | retrieval_check 有 missing |
| `sql_generation_error` | SQL 生成失败或为空 | `_resolve_sql` / provider 抛异常，或 SQL 为空 |
| `sql_invalid` | 生成的 SQL 语法错误 | Guard `syntax_guard` 拒绝 |
| `guard_blocked` | SQL 被安全规则拦截 | `state.guard_result.allowed = False`（非 safety case 时） |
| `execution_error` | SQL 执行失败 | `execute_guarded_sql` 抛异常 |
| `result_mismatch` | 执行成功但结果不符合预期 | columns/row_count/chart 不匹配 |
| `timeout` | 执行超时 | 超过设定时间 |

### 5.2 归因逻辑

```
if retrieval_check has missing → retrieval_miss
elif SQL resolve/provider raises or returns blank → sql_generation_error
elif guard_result.stage == "syntax_guard" → sql_invalid
elif guard_result.allowed is false and case.type != "safety" → guard_blocked
elif execute_guarded_sql raises → execution_error
elif result validation failed → result_mismatch
else → none (passed)
```

实现上不要依赖 `AgentState.stopped_at`。当前工作流只有 Guard 拦截会设置 `stopped_at`，而 provider / executor 异常会直接冒泡。Phase 3 runner 应在 retrieval、SQL generation、guard、execution、validation 每个阶段显式 catch 并写入 `error_category`。

### 5.3 SmokeResult 扩展

```python
@dataclass
class SmokeResult:
    # ... existing fields ...
    error_category: str | None = None      # retrieval_miss / sql_generation_error / etc.
    generated_sql: str | None = None       # 实际生成的 SQL（real LLM 模式）
    normalized_sql: str | None = None      # Guard normalized SQL
    elapsed_ms: int | None = None          # 耗时
```

## 6. Real LLM Benchmark 模式

### 6.1 设计

在 `run_smoke_eval.py` 中新增 `--provider` 参数：

```bash
# Mock 模式（默认，现有行为）
python scripts/run_smoke_eval.py

# Real LLM 模式
python scripts/run_smoke_eval.py --provider deepseek

# 指定 case 文件和输出路径
python scripts/run_smoke_eval.py evals/smoke_cases.yaml --provider deepseek --report-path evals/reports/deepseek_latest.md
```

### 6.2 Provider 选择逻辑

```python
def _create_provider(provider_name: str):
    if provider_name == "mock":
        return MockLLMProvider()
    if provider_name == "deepseek":
        settings = get_settings()
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for real LLM eval.")
        return DeepSeekProvider()
    raise ValueError(f"Unknown provider: {provider_name}")
```

### 6.3 Real 模式下的 Case 处理

- `case.provider == "mock"` 的 case 跳过
- 无 `provider` 字段的 case 都跑（默认 both）
- `case.mock_sql` 在 real 模式下被忽略，由 LLM 生成 SQL
- 保存实际生成的 SQL 到 `SmokeResult.generated_sql`
- Real 模式至少自动校验：SQL Guard 通过、执行成功、`expected_sql_keywords` 命中、result columns/row count 能满足宽松预期
- Real 模式报告保留 generated SQL 和 normalized SQL，供人工审阅语义等价性

### 6.4 无 API Key 行为

- Mock 模式不需要 key，始终可用
- Real 模式缺 key 时打印错误并退出，不静默跳过
- CI 环境中只有 Mock 模式

## 7. 报告增强

### 7.1 Summary 扩展

```markdown
## Summary

- Cases: 30
- Passed: 27/30
- Normal cases: 25
- Safety cases: 5
- Provider: deepseek
- Avg latency: 2.3s
- Error distribution:
  - retrieval_miss: 1
  - sql_invalid: 1
  - result_mismatch: 1
- Fallback used: 2/30
- Full schema context: 7155 chars
- Avg focused context: 2448 chars
- Avg focused context reduction: 65.8%
```

### 7.2 Case Results 表格扩展

新增列：Error Category、Generated SQL（折叠）、Elapsed

```markdown
## Case Results

| Case | Status | Type | Category | Fallback | Elapsed | Guard | Rows | SQL |
|------|--------|------|----------|----------|---------|-------|------|-----|
| ... | PASS | normal | - | false | 2.3s | passed | 30 | SELECT ... |
| ... | FAIL | normal | retrieval_miss | false | 1.8s | - | - | - |
```

### 7.3 新增 Error Distribution 章节

```markdown
## Error Distribution

| Category | Count | Cases |
|----------|-------|-------|
| retrieval_miss | 1 | phase2_product_name_alias |
| sql_invalid | 1 | recent_30d_avg_order_amount |
| result_mismatch | 1 | user_repeat_purchase_rate |
```

### 7.4 Failure Details 扩展

每条 failure 包含：
- 错误分类
- 生成的 SQL（如果有）
- normalized SQL（如果有）
- 错误原因
- 检索结果摘要

## 8. 实现顺序

### I3.1 错误归因 + 报告增强

```
I3.1 Error Taxonomy + Report
  -> SmokeResult 新增 error_category / generated_sql / normalized_sql / elapsed_ms
  -> _run_case 分阶段捕获 retrieval / generation / guard / execution / validation 错误
  -> 报告新增 Error Distribution 章节
  -> Case Results 表格新增列
  -> Failure Details 新增 SQL 和归因信息

验收：报告中能看到错误分类和分布；现有 15 条 case 仍全部通过
```

### I3.2 Case 扩展

```
I3.2 Eval Cases
  -> evals/smoke_cases.yaml 新增 15 条 case
  -> 覆盖：时间/地区/渠道/品类/客单价/别名/安全/retrieval 验证
  -> 所有新 case 都有 mock_sql 和 expected
  -> safety case 和复杂口径 case 标 provider: mock

验收：30 条 case，python scripts/run_smoke_eval.py 全部通过
```

### I3.3 Real LLM Benchmark

```
I3.3 Real Provider Mode
  -> run_smoke_eval.py 新增 --provider 参数
  -> _create_provider 工厂函数
  -> Real 模式忽略 mock_sql，由 LLM 生成
  -> 保存实际 SQL 到 SmokeResult.generated_sql
  -> 报告标注 provider 名称
  -> 无 API key 时明确报错退出

验收：DEEPSEEK_API_KEY 可用时能跑 real eval 并生成报告
```

### I3.4 README 更新

```
I3.4 Docs
  -> README 更新 Phase 3 能力说明
  -> 展示 Mock eval 基线结果
  -> 说明 Real LLM eval 使用方式

验收：README 反映 Phase 3 新能力
```

拆分原则：

- I3.1 先做报告和错误归因基础设施，不扩 case，降低定位成本。
- I3.2 只做 case 数据和 expected 调整，失败时可以利用 I3.1 报告定位。
- I3.3 再接真实 provider，避免同时引入 case 扩展和 LLM 不确定性。
- I3.4 只做文档收尾和 README 展示。

## 9. 验收标准

- 至少 30 条 eval case
- Mock 模式 30/30 通过
- 报告包含错误归因分布
- `DEEPSEEK_API_KEY` 可用时能跑 real eval 并生成独立报告
- 无 API key 时 Mock 模式正常工作；显式 `--provider deepseek` 清晰报错
- 现有 pytest 不回归

## 10. 已知取舍

- Real LLM eval 同步串行，不做并发。30 case × 5s ≈ 2.5min 可接受。
- 不做 HTML 报告。Markdown 足够，GitHub 可渲染。
- 不做 prompt 版本对比。后续如果 prompt 改动频繁再加 A/B 对比。
- 不做 CI 集成。当前为本地手动运行。
- Real eval 自动校验 Guard、执行、关键 SQL 结构和宽松结果形状；最终业务语义是否等价仍由人眼审阅报告判定。
- Phase 3 不新增复杂业务口径定义。复购率、7 天 vs 30 天、金额分布等 case 可以先用 `provider: mock` 建立执行基线，真实模型口径稳定性留到后续语义层增强。
