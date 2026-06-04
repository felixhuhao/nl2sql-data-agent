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
| 图表扩展 | 新增 bar / dual_axis / pie，覆盖 BI 常见场景 |

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
  → execute_sql                (Phase 1 已有)
  → ★ explain_performance      (Phase 6.5 新增，仅 ClickHouse)
  → summarize_result           (Phase 1 已有)
  → recommend_chart            (Phase 1 已有，图表类型扩展)
```

### 3.2 新增组件

#### olap_intent_detect 节点

**职责：** 识别用户问题是否属于 OLAP 分析模式，并标记分析类型。

**位置：** `backend/app/agent/olap_intent.py`

**实现方式：** 规则匹配为主，不消耗 LLM 调用。

```python
OLAPIntentType = Literal["yoy_mom", "topn", "moving_avg", "none"]

def detect_olap_intent(question: str, matched_metrics: list) -> OLAPIntentType:
    """
    规则匹配：
    - 同比/环比/对比去年/比上月 → yoy_mom
    - 排名/最多/最少/前N/TopN → topn
    - 移动平均/滚动平均/7日均值 → moving_avg
    - 其他 → none
    """
```

**输出：** `olap_intent: OLAPIntentType` + `olap_hint: str`

**多意图优先级：** 当用户问题同时命中多个 OLAP 意图时（如"Top10 商品销售额同比"），按 topn > yoy_mom > moving_avg 优先级选择。原因是 topn 限定了结果集形态，同环比只是附加计算列。

#### explain_performance 节点

**职责：** ClickHouse 查询执行后自动跑 EXPLAIN，解析为可读性能提示。

**位置：** `backend/app/agent/performance.py`

```python
async def explain_performance_node(state: AgentState) -> AgentState:
    # 仅 ClickHouse 数据源触发
    if state.datasource_dialect != "clickhouse":
        return state

    # 对 normalized_sql 执行 EXPLAIN
    explain_result = await connector.explain(state.normalized_sql)

    # 解析为可读提示
    hints = parse_explain_hints(explain_result, state.matched_tables)

    state.performance_hints = hints
    return state
```

### 3.3 Agent State 扩展

```python
class AgentState:
    # ... existing fields ...

    # Phase 6.5 新增
    olap_intent: str = "none"          # yoy_mom / topn / moving_avg / none
    olap_hint: str = ""                # 给 generate_sql 的提示文本
    performance_hints: list[str] = []  # ClickHouse EXPLAIN 解析结果
```

### 3.4 SSE 新增事件

```text
step: olap_detected    → { olap_intent: "yoy_mom", description: "检测到同比/环比分析意图" }
step: explain_plan     → { hints: [...], description: "查询性能分析完成" }
```

## 4. OLAP Prompt 增强策略

### 4.1 核心思路

`olap_intent_detect` 识别意图后，向 `generate_sql` 的 prompt 注入 OLAP SQL 模式指导和方言提示。LLM 根据具体问题自由生成 SQL，但有了模式指导确保关键函数和结构正确。

### 4.2 同比/环比 SQL 模式指导

当 `olap_intent == "yoy_mom"` 时注入：

```text
## 同比/环比分析 SQL 指南

当用户询问同比/环比时，请使用窗口函数 LAG() 计算对比值：

### DuckDB 示例
SELECT
  period,
  current_value,
  LAG(current_value, 12) OVER (ORDER BY period) AS prev_year_value,
  ROUND((current_value - LAG(current_value, 12) OVER (ORDER BY period))
    / NULLIF(LAG(current_value, 12) OVER (ORDER BY period), 0) * 100, 2) AS yoy_pct
FROM (...)

### ClickHouse 示例
SELECT
  toStartOfMonth(order_date) AS month,
  SUM(payment_amount) AS sales,
  lagInFrame(sales) OVER (ORDER BY month) AS prev_month,
  ROUND((sales - prev_month) / NULLIF(prev_month, 0) * 100, 2) AS mom_pct
FROM ...

关键规则：
- 环比 = LAG(1)，同比 = LAG(12)（月度）或 LAG(4)（季度）
- 计算百分比变化时用 NULLIF 做除零保护
- 保留 2 位小数
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

在 `agent/prompts/sql_generation.py` 的 `build_sql_prompt()` 中追加：

```python
def build_sql_prompt(context: SchemaContext, question: str, olap_hint: str = "", ...) -> str:
    prompt = base_prompt  # 现有 prompt

    if olap_hint:
        prompt += f"\n\n{olap_hint}"  # 追加 OLAP SQL 模式指导

    return prompt
```

设计要点：
- OLAP hint 追加到现有 prompt，不替换
- hint 内容根据 datasource_dialect 动态选择示例
- 现有 schema context、verified query few-shot 照常工作

## 5. ClickHouse EXPLAIN 与性能提示

### 5.1 EXPLAIN 执行流程

```text
execute_sql (ClickHouse)
  → 成功
  → explain_performance_node
    → connector.explain(normalized_sql)
    → 解析 EXPLAIN 输出
    → 生成 performance_hints[]
    → 写入 AgentState
  → SSE 推送 explain_plan 事件
```

### 5.2 Connector 层新增

```python
# connectors/base.py — 新增协议方法
async def explain(self, sql: str) -> dict | None:
    """执行 EXPLAIN 并返回解析结果，不支持则返回 None"""

# connectors/clickhouse.py — 实现
async def explain(self, sql: str) -> dict | None:
    explain_sql = f"EXPLAIN PIPELINE TREE {sql}"
    result = await self.execute_readonly(explain_sql)
    return self._parse_explain(result)

# connectors/duckdb.py — 不实现，返回 None
async def explain(self, sql: str) -> dict | None:
    return None
```

### 5.3 EXPLAIN 解析规则

从 ClickHouse `EXPLAIN PIPELINE TREE` 输出中提取：

| 检查项 | 解析方式 | 提示模板 |
|--------|---------|---------|
| 分区裁剪 | 检查 PartitionFilter 或 Parts: N/N | ✅ 命中分区键 {key}，扫描 {scanned}/{total} 分区 |
| 排序键利用 | 检查 Expression 节点是否包含 SortingKey 列 | ✅ 利用了排序键 {key} / ⚠️ 未利用排序键 |
| 扫描量 | 从 ReadFromMergeTree 节点提取行数 | 📊 扫描 {rows} 行（共 {total} 行，{pct}%） |
| JOIN 数量 | 统计 Join 节点数 | 🔗 包含 {n} 个 JOIN |
| 时间过滤缺失 | 检查 WHERE 子句是否含时间列 | 💡 建议添加时间范围过滤 |

### 5.4 性能提示输出格式

```json
{
  "performance_hints": [
    "✅ 命中分区键 order_date，扫描 3/24 分区",
    "📊 扫描 15,230 行（共 1,200,000 行，1.3%）",
    "⚠️ 未利用排序键，添加 ORDER BY order_date 可提升性能"
  ],
  "explain_raw": "...",
  "query_stats": {
    "rows_read": 15230,
    "bytes_read": 245760,
    "execution_time_ms": 45
  }
}
```

### 5.5 SSE 事件

```json
{
  "event": "step",
  "data": {
    "step": "explain_plan",
    "status": "done",
    "hints": ["✅ 命中分区键 order_date", "📊 扫描 15,230 行"],
    "query_stats": { "rows_read": 15230, "execution_time_ms": 45 }
  }
}
```

## 6. 图表扩展

### 6.1 新增图表类型

| 类型 | 触发条件 | Phase 6.5 场景 |
|------|---------|---------------|
| bar | 排名/TopN 维度 + 度量，非时间序列 | "销售额 Top10 商品"、"各渠道销售对比" |
| dual_axis | 同比/环比：同一维度 + 当前值 + 对比值 | "今年 vs 去年月销售额"、"环比上月增长" |
| pie | 少量维度（≤8）+ 占比/分布 | "各渠道销售占比"、"地区贡献分布" |

### 6.2 图表推荐逻辑变更

在 `visualization/recommender.py` 中扩展推荐规则：

```python
def recommend_chart(columns, rows, olap_intent=None):
    # Phase 6.5: OLAP 意图影响图表选择
    if olap_intent == "topn":
        return ChartRecommendation(
            chart_type="bar",
            x_column=dimension_col,
            y_columns=[metric_col],
            descending=True
        )

    if olap_intent == "yoy_mom":
        if has_comparison_columns(columns):
            return ChartRecommendation(
                chart_type="dual_axis",
                x_column=time_col,
                y_columns=[current_col, previous_col],
                annotations=[change_pct_col]
            )

    if is_distribution_query(columns, rows):
        # 维度 ≤ 8 个值，且只有 1 个度量列
        return ChartRecommendation(
            chart_type="pie",
            label_column=dimension_col,
            value_column=metric_col
        )

    # ... existing logic for line / table fallback
```

### 6.3 前端 ECharts 配置

**bar — 柱状图：** 水平柱状图更适合 TopN（标签可读性好）。

**dual_axis — 双轴对比折线图：** 左轴当年数据，右轴同比数据或变化百分比。

**pie — 饼图：** 标准饼图，> 8 个分类时合并为"其他"。

### 6.4 数据结构变更

`ChartRecommendation` 扩展：

```python
@dataclass
class ChartRecommendation:
    chart_type: str           # line / table / bar / dual_axis / pie
    x_column: str | None      # bar/line 的 x 轴
    y_columns: list[str]      # 度量列
    # Phase 6.5 新增
    label_column: str | None  # pie 的标签列
    value_column: str | None  # pie 的值列
    annotations: list[str]    # 标注列（如变化百分比）
    descending: bool = False  # bar 排序方向
```

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
  olap_intent: yoy_mom              # 期望识别的 OLAP 意图
  required_sql_patterns: ["LAG", "OVER"]  # SQL 模式校验
  chart_type: bar                   # 期望的图表推荐类型
  performance_hints_exist: true     # 是否期望生成性能提示
```

### 7.2 Eval Runner 扩展

`scripts/run_smoke_eval.py` 新增校验维度：

- OLAP 意图识别准确率校验
- SQL 模式命中校验（required_sql_patterns）
- 图表推荐匹配校验（chart_type）
- 性能提示生成校验（performance_hints_exist）

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
5. **EXPLAIN** — ClickHouse 查询自动生成可读性能提示，SSE 推送 explain_plan 事件
6. **Eval** — 新增 ~16 条 OLAP case，按意图类型和图表类型分 section 报告
7. **回归** — 现有 42 条 DuckDB case + 17 条 ClickHouse case 全部通过

## 8. Iteration 拆分建议

```text
I65.1 OLAP 意图识别
  → olap_intent.py 规则匹配
  → Agent State 扩展
  → olap_intent_detect 节点接入链路
  → SSE olap_detected 事件
  → 意图识别单元测试

I65.2 OLAP Prompt 增强
  → sql_generation.py 追加 OLAP hint 逻辑
  → 3 类 OLAP SQL 模式指导文本
  → 方言感知示例选择
  → Mock provider OLAP case 测试

I65.3 图表扩展
  → recommender.py 新增 bar / dual_axis / pie 推荐规则
  → ChartRecommendation 数据结构扩展
  → 前端 ECharts 3 种新图表配置
  → 图表推荐单元测试

I65.4 ClickHouse EXPLAIN
  → Connector 协议新增 explain 方法
  → ClickHouse connector 实现 EXPLAIN PIPELINE TREE
  → EXPLAIN 解析规则（分区/排序键/扫描量）
  → performance.py 节点接入链路
  → SSE explain_plan 事件

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
| `backend/app/agent/olap_intent.py` | 新增 | OLAP 意图识别 |
| `backend/app/agent/performance.py` | 新增 | EXPLAIN 解析与性能提示 |
| `backend/app/agent/state.py` | 修改 | 新增 olap_intent / olap_hint / performance_hints 字段 |
| `backend/app/agent/nodes.py` | 修改 | 接入 olap_intent_detect 和 explain_performance 节点 |
| `backend/app/agent/prompts/sql_generation.py` | 修改 | 追加 OLAP SQL 模式指导 |
| `backend/app/visualization/recommender.py` | 修改 | 新增 bar / dual_axis / pie 推荐规则 |
| `backend/app/connectors/base.py` | 修改 | 新增 explain 协议方法 |
| `backend/app/connectors/clickhouse.py` | 修改 | 实现 EXPLAIN PIPELINE TREE |
| `backend/app/connectors/duckdb.py` | 修改 | explain 返回 None |
| `backend/app/api/chat.py` | 修改 | SSE 新增 olap_detected / explain_plan 事件 |
| `frontend/src/components/` | 修改 | ECharts 新增 bar / dual_axis / pie 配置 |
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
