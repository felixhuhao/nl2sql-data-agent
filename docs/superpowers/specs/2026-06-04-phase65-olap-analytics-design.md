# Phase 6.5：OLAP 分析工具与性能治理 — 设计规格

> 日期：2026-06-04
> 状态：已审批
> 前置：Phase 6（ClickHouse 接入与多数据源方言适配）
> 建议用时：5-6 天

## 1. 定位与目标

在 Phase 6 多数据源基础上，补齐 OLAP 经营分析能力和性能治理，让系统从"能查数"升级为"能做分析"。

核心目标不是构建独立的分析工具 UI，而是增强 Agent 查询链路：用户在聊天框自然提问时，Agent 自动识别 OLAP 分析意图，注入 SQL 模式指导，生成高质量分析 SQL，并推荐合适的可视化类型。

## 2. 范围

### 做

| 能力 | 说明 |
|------|------|
| 同环比/移动平均 | Agent 自动路由 + Prompt 增强（LAG 窗口函数指导） |
| TopN/分层分析 | Agent 自动路由 + Prompt 增强（排序+限制模式） |
| ClickHouse EXPLAIN | 每次查询自动触发，解析为可读性能提示 |
| 图表扩展 | 扩展 bar，新增 dual_axis / pie，覆盖 BI 常见场景 |

### 文档标记 Deferred

| 能力 | 原因 | Revisit 条件 |
|------|------|-------------|
| 漏斗转化分析 | 需要新建 user_events 行为事件表，当前数据集没有用户行为轨迹 | user_events 数据就绪后 |
| 留存分析 | SQL 极其复杂（cohort 分组 + 日期差 + 稀疏矩阵），ClickHouse retention() vs DuckDB 自连接方言差异巨大 | 有充分时间投入时 |
| 数据质量检查 | 更偏运维/管理后台场景，不是"用户问数"核心需求 | Phase 2.5 Admin UI 扩展时 |

### 不做

- 不做前端手动工具入口（推迟到 Phase 8 产品化）
- 不做新的数据表或数据生成脚本
- 不改现有 Agent 链路的核心节点结构

## 3. 架构与 Agent 链路

### 3.1 链路变更

在现有 Agent v2 链路上新增两个环节：

```text
receive_question
  → datasource_selected        (Phase 6 已有)
  → intent_guard               (Phase 5 已有)
  → retrieve_context           (Phase 2 已有)
  → build_context              (Phase 2 已有)
  → ★ olap_intent_detect       (Phase 6.5 新增)
  → generate_sql               (Phase 1 已有，prompt 增强)
  → sql_guard                  (Phase 1 已有)
  → execute                    (Phase 1 已有)
  → ★ explain_performance      (Phase 6.5 新增，仅 ClickHouse)
  → summarize                  (Phase 1 已有)
  → recommend_chart            (Phase 1 已有，图表类型扩展)
```

### 3.2 新增组件

#### olap_intent_detect 节点

**职责：** 识别用户问题是否属于 OLAP 分析模式，并标记分析类型。

**位置：** `backend/app/agent/olap_intent.py`

**实现方式：** 规则匹配为主，不消耗 LLM 调用。

```python
OLAPIntentType = Literal["yoy_mom", "topn", "moving_avg", "none"]

def detect_olap_intent(question: str, matched_metrics: list) -> list[OLAPIntentType]:
    """
    规则匹配，返回所有命中的意图列表（可能为空）：
    - 同比/环比/对比去年/比上月 → yoy_mom
    - 排名/最多/最少/前N/TopN → topn
    - 移动平均/滚动平均/7日均值 → moving_avg
    - 无命中 → []
    """
```

**输出：** `olap_intents: list[OLAPIntentType]` + `olap_hint: str`

**多意图处理：** 返回所有命中的意图列表，不互斥。例如"Top10 商品销售额同比"同时命中 `["topn", "yoy_mom"]`，生成的 hint 合并两者的 SQL 模式指导。首个意图决定图表推荐优先级（topn > yoy_mom > moving_avg）。

**排序规则：** detector 必须按固定优先级返回结果，避免关键词扫描顺序影响图表推荐。

```python
OLAP_INTENT_PRIORITY = ["topn", "yoy_mom", "moving_avg"]
```

#### explain_performance 节点

**职责：** ClickHouse 查询执行后自动跑 EXPLAIN，解析为可读性能提示。

**位置：** `backend/app/agent/performance.py`

```python
def explain_performance_node(state: AgentState) -> AgentState:
    # 仅 ClickHouse 数据源触发
    if state.datasource_dialect != "clickhouse":
        return state

    # 对 Guard 产出的 normalized_sql 执行 EXPLAIN（同步，与现有 Agent 链路一致）
    normalized_sql = state.guard_result.normalized_sql if state.guard_result else None
    if not normalized_sql:
        return state

    connector = get_datasource_manager().get(state.datasource_name)
    explain_result = connector.explain(normalized_sql)

    if explain_result is None:
        return state

    # 解析为可读提示
    matched_tables = (state.explainability or {}).get("matched_tables", [])
    state.plan_hints = parse_plan_hints(explain_result, matched_tables)

    return state
```

> **注意：** 与现有 Agent 链路保持同步，不引入 async。Connector.explain() 也是同步方法。

### 3.3 Agent State 扩展

```python
class AgentState:
    # ... existing fields ...

    # Phase 6.5 新增
    olap_intents: list[str] = field(default_factory=list)  # 命中的 OLAP 意图列表
    olap_hint: str = ""                # 给 generate_sql 的合并提示文本
    plan_hints: list[str] = field(default_factory=list)    # EXPLAIN 计划提示
    runtime_stats: dict | None = None  # ClickHouse 运行时统计（尽力而为）
```

### 3.4 SSE 新增事件

使用与现有链路一致的 SSE 格式：`step` 事件 + `completed` 状态。

```text
# 新增 step id（需同步加入前端 workflowSteps 列表）
step: olap_detected  → { "step": "olap_detected", "status": "completed", "olap_intents": ["yoy_mom"] }
step: explain_plan   → { "step": "explain_plan", "status": "completed", "plan_hints": [...], "runtime_stats": {...} }
```

> **对齐规则：** step id 命名与现有一致（`datasource_selected` / `intent_guard` / `execute` / `summarize` / `recommend_chart` 等），status 统一用 `completed`。

## 4. OLAP Prompt 增强策略

### 4.1 核心思路

`olap_intent_detect` 识别意图后，向 `generate_sql` 的 prompt 注入 OLAP SQL 模式指导和方言提示。LLM 根据具体问题自由生成 SQL，但有了模式指导确保关键函数和结构正确。

### 4.2 同比/环比 SQL 模式指导

当 `olap_intent == "yoy_mom"` 时注入：

```text
## 同比/环比分析 SQL 指南

当用户询问同比/环比时，使用子查询 + 窗口函数 LAG() 计算对比值。
先在子查询中完成聚合，再在外层计算同环比。

### DuckDB 示例（子查询模式）
SELECT
  period,
  current_value,
  LAG(current_value, 12) OVER (ORDER BY period) AS prev_year_value,
  ROUND(
    (current_value - LAG(current_value, 12) OVER (ORDER BY period))
    / NULLIF(LAG(current_value, 12) OVER (ORDER BY period), 0) * 100, 2
  ) AS yoy_pct
FROM (
  SELECT
    dd.date_value AS period,
    SUM(fo.payment_amount) AS current_value
  FROM fact_orders fo
  JOIN dim_date dd ON fo.date_key = dd.date_key
  GROUP BY dd.date_value
) t
ORDER BY period

### ClickHouse 示例（子查询模式）
SELECT
  month,
  sales,
  LAG(sales, 12) OVER (ORDER BY month) AS prev_year_sales,
  ROUND(
    (sales - LAG(sales, 12) OVER (ORDER BY month))
    / NULLIF(LAG(sales, 12) OVER (ORDER BY month), 0) * 100, 2
  ) AS yoy_pct
FROM (
  SELECT
    toStartOfMonth(dd.date_value) AS month,
    SUM(fo.payment_amount) AS sales
  FROM fact_orders fo
  JOIN dim_date dd ON fo.date_key = dd.date_key
  GROUP BY month
) t
ORDER BY month

关键规则：
- 始终使用子查询模式：先聚合，再窗口计算
- 环比 = LAG(1)，同比 = LAG(12)（月度）或 LAG(4)（季度）
- 计算百分比变化时用 NULLIF 做除零保护
- 保留 2 位小数
- DuckDB 和 ClickHouse 都用 fact_orders.date_key -> dim_date.date_key 关联日期维表，再用 dim_date.date_value 做业务日期
- ClickHouse 性能治理仍关注 fact_orders.date_key，因为它是分区键和排序键的一部分
```

### 4.3 TopN/分层 SQL 模式指导

当 `olap_intent == "topn"` 时注入：

```text
## TopN / 排名分析 SQL 指南

### 通用模式
SELECT dimension, metric_value
FROM (SELECT dimension, SUM(amount) AS metric_value FROM ... GROUP BY dimension)
ORDER BY metric_value DESC
LIMIT {n}

### 用户分层模式
SELECT
  CASE
    WHEN total_orders >= 10 THEN '高频用户'
    WHEN total_orders >= 3 THEN '中频用户'
    ELSE '低频用户'
  END AS user_tier,
  COUNT(*) AS user_count
FROM ...
GROUP BY user_tier

关键规则：
- TopN 使用 ORDER BY + LIMIT
- 分层使用 CASE WHEN
- 如需占比，增加 SUM() OVER () 计算总计
```

### 4.4 移动平均 SQL 模式指导

当 `olap_intent == "moving_avg"` 时注入：

```text
## 移动平均 SQL 指南

使用窗口函数 AVG() OVER 计算：
AVG(metric_value) OVER (ORDER BY date_col ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS ma_7d

关键规则：
- 7日移动平均 = ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
- 30日移动平均 = ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
```

### 4.5 Prompt 注入位置

在 `backend/app/core/llm_provider.py` 的 `SQLGenerationRequest` 中新增 `olap_hint: str = ""`，并在 `agent/prompts/sql_generation.py` 的 `build_sql_generation_messages()` 中追加到 user message：

```python
@dataclass(frozen=True)
class SQLGenerationRequest:
    question: str
    schema_context: str
    repair: SQLRepairContext | None = None
    datasource_name: str = DEFAULT_DATASOURCE
    datasource_dialect: str = "duckdb"
    olap_hint: str = ""


def build_sql_generation_messages(request: SQLGenerationRequest) -> list[dict[str, str]]:
    content_parts = [
        f"Schema context:\n{request.schema_context}",
        f"Question:\n{request.question}",
    ]
    if request.olap_hint:
        content_parts.append(f"OLAP SQL guidance:\n{request.olap_hint}")
    user_message = {"role": "user", "content": "\n\n".join(content_parts)}
    ...
```

设计要点：
- OLAP hint 追加到现有 user message，不替换 schema context / question
- hint 内容根据 datasource_dialect 动态选择示例
- 现有 schema context、verified query few-shot 照常工作
- repair path 也带同一个 olap_hint，避免修复后丢失 OLAP 模式约束

## 5. ClickHouse EXPLAIN 与性能提示

### 5.1 EXPLAIN 执行流程

```text
execute (ClickHouse)
  → 成功
  → explain_performance_node
    → connector.explain(normalized_sql)
    → 解析 EXPLAIN 输出
    → 生成 plan_hints[]
    → 写入 AgentState
  → SSE 推送 explain_plan 事件
```

### 5.2 Connector 层新增

```python
# connectors/base.py — 新增协议方法
def explain(self, sql: str) -> dict | None:
    """执行 EXPLAIN 并返回解析结果，不支持则返回 None"""

# connectors/clickhouse.py — 实现
def explain(self, sql: str) -> dict | None:
    explain_sql = f"EXPLAIN PIPELINE TREE {sql}"
    result = self.execute(explain_sql)
    return self._parse_explain(result)

# connectors/duckdb.py — 不实现，返回 None
def explain(self, sql: str) -> dict | None:
    return None
```

### 5.3 两类性能信息

EXPLAIN 和运行时统计来源不同，拆为两类：

**plan_hints — 来自 EXPLAIN PIPELINE TREE（查询计划结构）：**

| 检查项 | 解析方式 | 提示模板 |
|--------|---------|---------|
| 分区裁剪 | 检查 PartitionFilter 或 Parts: N/N | ✅ 命中分区键 {key}，扫描 {scanned}/{total} 分区 |
| 排序键利用 | 检查 Expression 节点是否包含 SortingKey 列 | ✅ 利用了排序键 {key} / ⚠️ 未利用排序键 |
| JOIN 数量 | 统计 Join 节点数 | 🔗 包含 {n} 个 JOIN |
| 时间过滤缺失 | 检查 WHERE 子句是否含时间列 | 💡 建议添加时间范围过滤 |

**runtime_stats — 来自 ClickHouse 执行结果或 query log：**

| 检查项 | 来源 | 提示模板 |
|--------|------|---------|
| 扫描行数 | ClickHouse client summary 或 query_log | 📊 扫描 {rows} 行 |
| 扫描字节 | 同上 | 📊 读取 {bytes} 数据 |
| 执行耗时 | execute() 返回的 timing | ⏱️ 耗时 {ms}ms |

> **实现约束：** runtime_stats 依赖 ClickHouse client 返回的统计信息或 system.query_log。如果 ClickHouse connector 的 execute() 没有返回这些信息，runtime_stats 字段为空，不报错。plan_hints 是主要交付物，runtime_stats 是尽力而为的增强。

### 5.4 性能提示输出格式

```json
{
  "plan_hints": [
    "✅ 命中分区键 date_key，扫描 3/24 分区",
    "⚠️ 未利用排序键 date_key，建议增加明确的日期范围过滤",
    "🔗 包含 1 个 JOIN"
  ],
  "runtime_stats": {
    "rows_read": 15230,
    "bytes_read": 245760,
    "execution_time_ms": 45
  }
}
```

### 5.5 SSE 事件

使用与现有链路一致的格式（`completed` 状态）：

```json
{
  "event": "step",
  "data": {
    "step": "explain_plan",
    "status": "completed",
    "plan_hints": ["✅ 命中分区键 date_key", "🔗 包含 1 个 JOIN"],
    "runtime_stats": { "rows_read": 15230, "execution_time_ms": 45 }
  }
}
```

## 6. 图表扩展

### 6.1 新增图表类型

| 类型 | 触发条件 | Phase 6.5 场景 | 状态 |
|------|---------|---------------|------|
| bar | 排名/TopN 维度 + 度量，非时间序列 | "销售额 Top10 商品"、"各渠道销售对比" | ✅ Phase 6 已实现 |
| dual_axis | 同比/环比：同一维度 + 当前值 + 对比值 | "今年 vs 去年月销售额"、"环比上月增长" | 🆕 Phase 6.5 新增 |
| pie | 少量维度（≤8）+ 占比/分布 | "各渠道销售占比"、"地区贡献分布" | 🆕 Phase 6.5 新增 |

### 6.2 图表推荐逻辑变更

在 `visualization/recommender.py` 中扩展推荐规则。现有签名 `recommend_chart(result: QueryResult)` 不传 OLAP 意图，需扩展为接受可选参数：

```python
def recommend_chart(result: QueryResult, olap_intents: list[str] | None = None) -> ChartRecommendation:
    primary_intent = olap_intents[0] if olap_intents else None

    # Phase 6.5: OLAP 意图影响图表选择
    if primary_intent == "topn":
        # 复用现有 bar 推荐逻辑，不重复实现
        return ChartRecommendation(
            chart_type="bar",
            x_column=dimension_col,
            y_columns=[metric_col],
            reason="TopN analysis detected.",
        )

    if primary_intent == "yoy_mom" and has_comparison_columns(result.columns):
        return ChartRecommendation(
            chart_type="dual_axis",
            x_column=time_col,
            y_columns=[current_col, previous_col],
            reason="Year-over-year or month-over-month comparison detected.",
        )

    if is_distribution_query(result):
        return ChartRecommendation(
            chart_type="pie",
            x_column=dimension_col,
            y_columns=[metric_col],
            reason="Distribution query with few categories.",
        )

    # ... existing logic for bar / line / table fallback
```

### 6.3 前端 ECharts 配置

**bar — 柱状图：** 水平柱状图更适合 TopN（标签可读性好）。

**dual_axis — 双轴对比折线图：** 左轴当年数据，右轴同比数据或变化百分比。

**pie — 饼图：** 标准饼图，> 8 个分类时合并为"其他"。

### 6.4 数据结构变更

`ChartRecommendation` 是 Pydantic BaseModel，扩展时所有新字段必须有默认值，确保 SSE JSON 兼容性：

```python
from pydantic import BaseModel, Field

class ChartRecommendation(BaseModel):
    chart_type: str
    x_column: str | None = None
    y_columns: list[str] = Field(default_factory=list)
    reason: str
    # Phase 6.5 新增，全部有默认值
    # （不新增 label_column / value_column / annotations / descending）
    # pie 复用 x_column 作为标签列，y_columns[0] 作为值列
    # dual_axis 复用 x_column / y_columns，前端根据 chart_type 双轴渲染
```

> **设计取舍：** 不新增 `label_column` / `value_column` / `annotations` / `descending` 字段。pie 图复用 `x_column` 作为标签列、`y_columns[0]` 作为值列；dual_axis 复用 `x_column` / `y_columns`，前端根据 `chart_type` 决定渲染方式。避免数据结构膨胀，减少 SSE 兼容性风险。

## 7. Eval 扩展与验收

### 7.1 Eval Case 扩展

在 `evals/smoke_cases.yaml` 中新增 ~16 条 OLAP 专用 case：

- 同比/环比 Cases：~6 条（覆盖 DuckDB 和 ClickHouse）
- TopN/分层 Cases：~4 条
- 移动平均 Cases：~1 条
- 性能提示 Cases：~3 条（仅 ClickHouse）
- 占比/分布 Cases：~2 条（覆盖 pie 图表）

Case 格式新增字段：

```yaml
expected:
  olap_intents: [yoy_mom]           # 期望识别的 OLAP 意图列表
  required_sql_patterns: ["LAG", "OVER"]  # SQL 模式校验
  chart_type: bar                   # 期望的图表推荐类型
  plan_hints_exist: true            # 是否期望生成 EXPLAIN 计划提示
```

### 7.2 Eval Runner 扩展

`scripts/run_smoke_eval.py` 新增校验维度：

- OLAP 意图识别准确率校验
- SQL 模式命中校验（required_sql_patterns）
- 图表推荐匹配校验（chart_type）
- 性能提示生成校验（plan_hints_exist）

### 7.3 报告新增维度

Eval 报告新增 Phase 6.5 section，按 DuckDB / ClickHouse 分列展示：

- OLAP 意图识别准确率
- 同比/环比 SQL 模式命中率
- TopN SQL 模式命中率
- 图表推荐匹配率
- 性能提示生成率

### 7.4 验收标准

1. **同环比** — 可以回答"今年各月销售额同比去年增长了多少"，生成的 SQL 包含 LAG 窗口函数
2. **TopN** — 可以回答"销售额前10的商品"，生成的 SQL 包含 ORDER BY + LIMIT
3. **意图识别** — Agent 正确识别同比/环比/TopN/移动平均意图，SSE 推送 olap_detected 事件
4. **图表** — TopN 场景推荐 bar，同比场景推荐 dual_axis，占比场景推荐 pie
5. **EXPLAIN** — ClickHouse 查询自动生成 plan_hints（分区/排序键/JOIN），SSE 推送 explain_plan 事件
6. **Eval** — 新增 ~16 条 OLAP case，按意图类型和图表类型分 section 报告
7. **回归** — 现有 42 条 DuckDB case + 17 条 ClickHouse case 全部通过

## 8. Iteration 拆分建议

```text
I65.1 OLAP 意图识别
  → olap_intent.py 规则匹配（返回 list[OLAPIntentType]）
  → Agent State 扩展（olap_intents / olap_hint / plan_hints / runtime_stats）
  → olap_intent_detect 节点接入链路
  → SSE olap_detected 事件（step id + completed 状态）
  → 意图识别单元测试

I65.2 OLAP Prompt 增强
  → sql_generation.py 追加 OLAP hint 逻辑（多意图合并 hint）
  → 3 类 OLAP SQL 模式指导文本（子查询模板，schema-aligned）
  → 方言感知示例选择
  → Mock provider OLAP case 测试

I65.3 图表扩展
  → recommender.py 扩展 bar 推荐（olap_intents 参数）+ 新增 dual_axis / pie 推荐规则
  → ChartRecommendation 不新增字段（复用 x_column / y_columns）
  → 前端 ECharts 新增 PieChart import + dual_axis 配置（bar 已有）
  → canRenderChart 支持 dual_axis / pie
  → 图表推荐单元测试

I65.4 ClickHouse EXPLAIN
  → Connector 协议新增同步 explain() 方法
  → ClickHouse connector 实现 EXPLAIN PIPELINE TREE
  → plan_hints 解析（分区/排序键/JOIN 数）+ runtime_stats 尽力而为
  → performance.py 节点接入链路（同步）
  → SSE explain_plan 事件（step id + completed 状态）

I65.5 Eval 扩展与回归
  → 新增 ~16 条 OLAP eval case
  → Eval runner 新增校验维度
  → 报告新增 Phase 6.5 section
  → 全量回归测试
  → README 更新
```

## 9. 关键文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `backend/app/agent/olap_intent.py` | 新增 | OLAP 意图识别（返回 list） |
| `backend/app/agent/performance.py` | 新增 | EXPLAIN 解析（plan_hints + runtime_stats） |
| `backend/app/agent/state.py` | 修改 | 新增 olap_intents / olap_hint / plan_hints / runtime_stats 字段 |
| `backend/app/agent/nodes.py` | 修改 | 接入 olap_intent_detect 和 explain_performance 节点（同步） |
| `backend/app/agent/prompts/sql_generation.py` | 修改 | 追加 OLAP SQL 模式指导（子查询模板） |
| `backend/app/visualization/recommender.py` | 修改 | 扩展 bar + 新增 dual_axis / pie 推荐规则 |
| `backend/app/connectors/base.py` | 修改 | 新增同步 explain 协议方法 |
| `backend/app/connectors/clickhouse.py` | 修改 | 实现 EXPLAIN PIPELINE TREE |
| `backend/app/connectors/duckdb.py` | 修改 | explain 返回 None |
| `backend/app/api/chat.py` | 修改 | SSE 新增 olap_detected / explain_plan 事件（completed 状态） |
| `frontend/src/App.vue` | 修改 | workflowSteps 新增步骤 + ECharts 新增 PieChart + dual_axis 配置 |
| `evals/smoke_cases.yaml` | 修改 | 新增 ~16 条 OLAP case |
| `scripts/run_smoke_eval.py` | 修改 | 新增 OLAP 校验维度 |

## 10. Deferred：漏斗转化分析

### 依赖

- 新建 user_events 行为事件表（event_type: view / add_to_cart / order / pay）
- 事件时间戳 + 用户 ID + 商品 ID 关联
- 数据生成脚本扩展

### 预期设计

- Agent 识别"漏斗/转化"意图 → 路由到漏斗模板
- ClickHouse 使用 windowFunnel() 原生函数
- DuckDB 使用条件聚合 COUNT(CASE WHEN...) 模拟
- 前端新增漏斗图（ECharts funnel）
- 方言差异显著，模板比 prompt 增强更适合

### Revisit 条件

当 user_events 数据准备就绪时，可独立启动漏斗分析实现。
