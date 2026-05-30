# NL2SQL Phase 2 设计文档

> 日期: 2026-05-30
> 状态: 已确认
> 前置: Phase 1 工业化最小闭环已完成
> 范围: 元数据语义层（核心 5 项能力）

## 1. 关键决策记录

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 检索方式 | 规则匹配 | Phase 2 不引入向量 DB，先验证检索架构 |
| 语义层数据源 | SQLite | 与 Phase 1 元数据同库，零额外依赖 |
| 上下文策略 | 聚焦 + fallback | 先扩展必要 join partner，仍不足时回退全量 schema |
| 语义迁移方式 | seed 函数 + CRUD | seed 提供初始值，运行时完全读 DB，默认不覆盖用户编辑 |
| 不做 | Feedback Loop、Data Quality | 推到后续 Phase，避免 Phase 2 范围膨胀 |

## 2. Phase 2 核心能力

1. **Semantic overlay 迁移到 DB** — 消除运行时硬编码语义数据
2. **Metric Layer v1** — 结构化指标定义（name、label、expression、dimensions）
3. **Column aliases** — 字段别名管理（"销售额" → payment_amount）
4. **规则检索 v1** — 根据问题检索相关表/列/指标/示例
5. **Agent v2** — 检索节点接入工作流，聚焦上下文替代全量 dump

## 3. 数据模型

### 3.1 新增表

```text
meta_metrics
  id          INTEGER PK
  name        TEXT UNIQUE NOT NULL     -- "sales_amount"
  label       TEXT NOT NULL            -- "销售额"
  expression  TEXT NOT NULL            -- "SUM(fact_orders.payment_amount)"
  description TEXT
  default_time_column TEXT             -- "dim_date.date_value"
  allowed_dimensions TEXT              -- JSON ["date","channel","region"]
  enabled     BOOLEAN DEFAULT 1

meta_column_aliases
  id          INTEGER PK
  table_name  TEXT NOT NULL            -- "fact_orders"
  column_name TEXT NOT NULL            -- "payment_amount"
  alias       TEXT NOT NULL            -- "销售额"
  UNIQUE(table_name, column_name, alias)

meta_verified_queries
  id           INTEGER PK
  query_id     TEXT UNIQUE NOT NULL    -- "recent_30d_daily_sales"
  question     TEXT NOT NULL
  sql          TEXT NOT NULL
  tags         TEXT                    -- JSON
  verified_by  TEXT DEFAULT "system"
  enabled      BOOLEAN DEFAULT 1

meta_analysis_spaces
  id          INTEGER PK
  name        TEXT UNIQUE NOT NULL     -- "ecommerce_demo"
  datasource  TEXT NOT NULL            -- "duckdb_ecommerce"
  tables      TEXT NOT NULL            -- JSON
  enabled_metrics TEXT NOT NULL        -- JSON
  allowed_operations TEXT NOT NULL     -- JSON
  enabled     BOOLEAN DEFAULT 1
```

### 3.2 Phase 1 现有表（不修改 schema）

- `meta_tables` — 已有 display_name, description, domain
- `meta_columns` — 已有 description, is_dimension, is_metric, sample_values
- `meta_relationships` — 已有 source, confidence, fanout_risk

## 4. 语义迁移策略

### 4.0 迁移范围

Phase 2 运行时不再从代码常量读取这些语义资产：

- table semantics：表展示名、描述、domain
- column semantics：字段描述、维度/指标标记、sample value fallback
- relationship overlay：人工确认 join、source、confidence、fanout_risk、description
- metric definitions：`sales_amount`、`order_count`、`aov`
- column aliases：中文业务别名
- verified queries：demo / smoke eval / few-shot 示例
- analysis space：可问表、启用指标、允许操作

`semantic_overlay.py`、`verified_queries.py` 和 `analysis_space.py` 可以保留为 seed 数据源或向后兼容 shim，但 `metadata.service`、`metadata.sync`、`metadata.api`、`sql_guard.scope` 等运行时代码必须从 SQLite 读取。

### 4.1 迁移路径

```
semantic_overlay.py (硬编码常量)
verified_queries.py / analysis_space.py (Phase 1 代码资产)
  → seed.py 导入初始数据，写入 SQLite
  → 运行时代码完全从 DB 读取
  → 代码常量文件保留作为 seed 数据源，不再被运行时 import
```

### 4.2 种子数据内容

| 数据源 | 目标表 | 记录数 |
|--------|--------|--------|
| TABLE_SEMANTICS | meta_tables (display_name, description, domain) | 7 |
| COLUMN_SEMANTICS + TABLE_COLUMN_SEMANTICS | meta_columns (description) | 50+ |
| DIMENSION_COLUMNS / METRIC_COLUMNS | meta_columns (is_dimension, is_metric) | 34 |
| CONFIRMED_RELATIONSHIPS | meta_relationships (overlay) | 7 |
| METRIC_DEFINITIONS | meta_metrics | 3 |
| 列别名 (新写) | meta_column_aliases | 20-30 |
| VERIFIED_QUERIES | meta_verified_queries | 4 |
| ECOMMERCE_ANALYSIS_SPACE | meta_analysis_spaces | 1 |
| SAMPLE_VALUE_FALLBACKS | meta_columns (sample_values) | 7 |

### 4.3 sync 流程变更

```
Phase 1 sync:
  物理同步 (DuckDB) → overlay (从 semantic_overlay.py 常量)

Phase 2 sync:
  物理同步 (DuckDB) → 种子语义 (seed.py, 一次性) → overlay (从 DB 读取)
```

sync.py 不再在运行时导入 semantic_overlay 的常量，改为 seed.py 导入并写入 DB。sync 后半段的 overlay 逻辑改为从 DB 读取已写入的语义数据。

### 4.4 seed 与 sync 覆盖规则

为避免用户编辑被物理同步覆盖，Phase 2 采用以下边界：

- `sync.py` 只负责物理元数据：表是否存在、字段名、字段类型、row_count、自动 profiling sample_values。
- `seed.py` 负责初始语义资产：表/字段描述、维度/指标标记、relationship overlay、metrics、aliases、verified queries、analysis space。
- 默认 seed 是幂等 upsert，但不覆盖已经存在且被用户编辑过的语义字段。
- 需要提供显式 reset 入口，例如 `seed_semantics(reset=True)` 或 CLI `--reset-seed`，才允许用 seed 覆盖 DB 里的语义字段。
- 物理 schema 新增字段时，sync 创建字段记录；如果 seed 没有语义定义，则 description/is_dimension/is_metric 保持空或默认值，等待 CRUD 或后续 seed 补充。
- 物理 schema 删除字段时，sync 可以删除对应 `meta_columns`；但 aliases/metrics/verified queries 不自动删除，只在 runner 或 API 中暴露失效校验结果。

已知限制：

- `meta_columns.is_dimension` / `is_metric` 当前是 boolean，无法区分“默认 False”和“用户手动改成 False”。因此 seed 默认会把 seed 集合内字段标记为 True。Phase 2 先接受这个限制；如果后续需要严格保护人工关闭状态，再增加 nullable 字段、semantic_source 或 updated_by/seeded_at。

## 5. 规则检索 v1

### 5.1 检索流程

```
question → tokenize → match tables / columns / metrics / examples → scored results
```

### 5.2 检索策略

**表检索：**
- 匹配 display_name 关键词（"订单" → fact_orders）
- 匹配 description 关键词
- 匹配 domain（"sales" → 所有 sales domain 表）
- 通过别名间接匹配（"销售额" 的别名指向 payment_amount，属于 fact_orders）

**列检索：**
- 匹配 meta_column_aliases.alias
- 匹配 meta_columns.description 关键词

**指标检索：**
- 匹配 meta_metrics.label（"客单价" → aov）
- 匹配 meta_metrics.name

**示例检索：**
- 匹配 meta_verified_queries.question 关键词
- 匹配 tags

### 5.3 返回结构

```python
@dataclass
class RetrievalResult:
    tables: list[RetrievedTable]                   # 默认最多 10
    columns: list[RetrievedColumn]                 # 默认最多 10
    metrics: list[RetrievedMetric]                 # 默认最多 10
    verified_queries: list[RetrievedVerifiedQuery] # 默认最多 10
```

### 5.4 Fallback

fallback 不使用单纯的 table count 判断，避免单表问题被误判。流程是：

1. 先执行规则检索，得到直接命中的表、字段、指标、示例。
2. 再执行 join partner expansion，补齐时间维、指标默认时间列、命中维度需要的 join 对端表。
3. 如果仍然没有命中任何表、字段、指标或示例，才回退到全部启用表，等同 Phase 1 全量 schema 行为。
4. 如果只命中一个事实表但已包含必要指标和时间列，不强制 fallback。
5. retrieval result 中需要标注 `fallback_used: bool`，供 SSE 和 explainability 展示。

### 5.5 Join Partner 自动包含

join expansion 不应把所有邻接表无条件塞进上下文。规则：

- 如果命中 metric 且 metric 有 `default_time_column`，自动包含该时间列所在表。
- 如果命中字段/别名属于维度表，自动包含连接该维度表所需的事实表和 relationship。
- 对 `fact_orders` 这类高频事实表，默认只补 `dim_date`；其他维表必须由问题关键词、alias、metric allowed_dimensions 或 verified query 命中后再补。
- 每个事实表最多自动补 3 个 join partner，防止上下文退化成全量 schema。
- `RetrievedTable` 需要包含 `source` 字段，例如 `direct_match`、`metric_expansion`、`join_expansion`、`fallback`。

检索消歧注意事项：

- `fact_orders.region_key` 和 `dim_regions.region_group` 都可能有“地区/区域/大区”别名。检索时应优先把可读维度字段（如 `dim_regions.region_group`）作为展示/分组字段，把 fact 表 key 字段作为 join expansion 线索，而不是优先返回给 LLM 作为业务维度。

## 6. 聚焦上下文构建

### 6.1 build_focused_context(retrieval_result)

```python
def build_focused_context(result: RetrievalResult) -> str:
    if result.fallback_used:
        return build_schema_context()  # fallback
    # 仅加载检索到的表、列、关系、指标、示例
    # 格式与 build_schema_context() 完全一致
```

可以保留便捷函数 `build_focused_context_for_question(question)`，内部先调用 `retrieve(question)`，但 Agent workflow 必须消费 `state.retrieval_result`，避免重复检索。

### 6.2 输出格式

与 Phase 1 `build_schema_context()` 保持完全相同的章节标题和格式：
- `## Analysis Space`
- `## Tables` （仅检索到的表）
- `## Join Relationships` （仅检索到的表之间的关系）
- `## Metric Definitions` （仅检索到的指标）
- `## Verified Queries` （仅匹配的示例）

格式一致意味着 SQL generation prompt 不需要任何修改。

## 7. Agent v2 工作流

### 7.1 节点变更

```
Phase 1:
  build_context → generate_sql → sql_guard → execute → summarize → recommend_chart

Phase 2:
  retrieve_context → build_context → generate_sql → sql_guard → execute → summarize → recommend_chart
```

新增 `retrieve_context` 节点，在 `build_context` 之前执行。

### 7.2 AgentState 变更

```python
@dataclass
class AgentState:
    question: str
    schema_context: str | None = None
    retrieval_result: dict | None = None     # 新增
    sql: str | None = None
    provider: str | None = None
    matched_query_id: str | None = None
    guard_result: GuardResult | None = None
    query_result: QueryResult | None = None
    summary: str | None = None
    explainability: dict | None = None
    error: str | None = None
    stopped_at: str | None = None
    completed_steps: list[str] = field(default_factory=list)
```

### 7.3 retrieve_context_node

```python
def retrieve_context_node(state, retriever=retrieve):
    state.retrieval_result = retriever(state.question)
    state.completed_steps.append("retrieve_context")
    return state
```

### 7.4 build_context_node 变更

```python
def build_context_node(state, schema_context_builder=None):
    if schema_context_builder is not None:
        state.schema_context = schema_context_builder()  # 向后兼容
    elif state.retrieval_result is not None:
        state.schema_context = build_focused_context(state.retrieval_result)
    else:
        state.schema_context = build_focused_context_for_question(state.question)
    state.completed_steps.append("build_context")
    return state
```

传 `schema_context_builder` 的测试路径不受影响。

### 7.5 SSE 事件变更

新增 `retrieve_context` 步骤事件：

```json
{
  "step": "retrieve_context",
  "status": "completed",
  "tables": ["fact_orders", "dim_date"],
  "columns": ["fact_orders.payment_amount"],
  "metrics": ["sales_amount", "order_count"],
  "examples": ["recent_30d_daily_sales"],
  "fallback_used": false
}
```

## 8. API 变更

### 8.1 新增端点

```
GET /api/metadata/retrieve?question=... — 检索结果（调试用，只读）
GET /api/metadata/verified-queries      — 列出 verified queries（DB backed，已完成）
GET /api/metadata/analysis-space        — 当前 Analysis Space（DB backed，已完成）
```

指标、别名、verified query 的 CRUD 先不进入规则检索迭代；等检索链路和 Agent v2 跑通后，作为管理能力单独拆分。

## 9. 实现顺序

### Iteration 1: 语义层 DB 迁移

```
I2.1 Models
  -> 新增 MetaMetric / MetaColumnAlias / MetaVerifiedQuery / MetaAnalysisSpace
  -> 只创建 schema，不改变运行时读取路径

I2.2 Seed Semantics
  -> 新增 metadata/seed.py
  -> seed table/column/relationship/metric/alias/verified query/analysis space 初始语义
  -> 幂等 upsert，默认不覆盖已有语义字段

I2.3 Sync Reads Seeded Semantics
  -> sync.py 不再直接 import semantic_overlay
  -> 物理 sync 后调用 seed
  -> overlay 从 DB 读取
  -> 保持 sync_metadata() 输出和现有测试兼容

I2.4 Service DB-backed Runtime
  -> service.py 删除 METRIC_DEFINITIONS
  -> build_schema_context() 从 DB 读 metrics / verified queries / analysis space
  -> metadata API 的 verified queries / analysis space 从 DB 读

I2.5 Guard Scope DB-backed Analysis Space
  -> sql_guard/scope.py 不再从 dataspace.analysis_space 读代码常量
  -> 从 meta_analysis_spaces 读 allowed tables
  -> SQL Guard 测试和 smoke eval 保持通过
```

### Iteration 2: 规则检索引擎

```
I2.6 Retrieval Engine
  -> 新增 metadata/retrieval.py
  -> question normalization
  -> 基于表/字段/别名/指标/verified query/sample values 做规则匹配
  -> 返回 scored matched assets，先不改变 agent 运行链路

I2.7 Focused Context Builder
  -> 新增 build_focused_context(question)
  -> 根据检索结果选择 tables / columns / metrics / join paths / verified queries
  -> 无明确命中时 fallback 到 full schema context
  -> 验证 focused context 小于 full schema context，且 smoke eval 仍通过

I2.8 Retrieval API
  -> 新增只读检索调试端点，例如 /api/metadata/retrieve
  -> 返回 matched tables / columns / metrics / verified queries / join paths
  -> 指标/别名 CRUD 暂不进入本轮，后移到管理能力迭代
```

### Iteration 3: Agent v2

```
I2.9 AgentState + retrieve_context_node + build_context_node 改用聚焦上下文
I2.10 SSE 流更新（retrieve_context 事件）+ Smoke eval 扩展（15 cases）+ README 更新
```

## 10. 验收标准

- [ ] `build_schema_context()` 输出与 Phase 1 一致（I2.3 验证点）
- [ ] `semantic_overlay.py` 不再被运行时代码 import
- [ ] `METRIC_DEFINITIONS` 硬编码从 service.py 删除
- [ ] verified queries 和 analysis space 从 DB 读取，不再由运行时读取代码常量
- [ ] "查询最近30天每日销售额" 检索到 fact_orders + dim_date + sales_amount + order_count
- [ ] "客单价" 检索到 aov 指标
- [ ] 聚焦上下文比全量 schema 小，但 smoke eval 仍通过
- [ ] 检索 API 可展示 matched tables / columns / metrics / verified queries / join paths
- [ ] SSE 流包含 retrieve_context 步骤
- [ ] 15/15 smoke cases 通过
- [ ] 所有现有 pytest 不受影响

新增 5 条 Phase 2 smoke cases：

- 客单价：验证 `aov` metric 检索和表达式可用。
- 销售额别名：验证 "销售额" 能命中 `fact_orders.payment_amount` 和 `sales_amount` metric。
- 渠道别名：验证 "渠道" 能命中 `dim_channels.channel_name` 并补 join path。
- 品类别名：验证 "品类" 能命中 `dim_products.category` 并补 join path。
- fallback：验证无法检索的普通查询会回退到 Phase 1 全量 schema，且不会破坏默认查询链路。
