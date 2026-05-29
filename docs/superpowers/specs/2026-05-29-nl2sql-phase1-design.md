# NL2SQL Phase 1 设计文档

> 日期: 2026-05-29
> 状态: 已确认
> 范围: 整体架构确认 + Phase 1 工业化最小闭环详细设计

## 1. 关键决策记录

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 分析数据源 | DuckDB | 零依赖 OLAP，与 ClickHouse 方言连续 |
| 元数据存储 | SQLite | 轻量无外部服务，与 DuckDB 物理分离 |
| LLM 提供者 | DeepSeek API + Mock provider | DeepSeek 用于真实生成，Mock provider 用于本地测试、CI 和无 API Key 演示 |
| Agent 工作流 | LangGraph 显式节点图 | 可观测、可修复、与 SSE 天然对齐 |
| 数据生成 | Python 脚本 | 可控、可调规模、schema 精确匹配 |
| 前端框架 | Vue 3 + ECharts | 文档已推荐，TypeScript + Vite |

## 2. 整体架构

```
nl2sql_pro/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口
│   │   ├── config.py                # pydantic-settings 配置管理
│   │   ├── api/
│   │   │   ├── chat.py              # POST /api/chat/query (SSE)
│   │   │   ├── metadata.py          # 元数据 CRUD + sync
│   │   │   └── history.py           # 查询历史
│   │   ├── agent/
│   │   │   ├── graph.py             # LangGraph StateGraph 定义
│   │   │   ├── state.py             # AgentState TypedDict
│   │   │   ├── nodes/
│   │   │   │   ├── context_builder.py
│   │   │   │   ├── sql_generator.py
│   │   │   │   ├── executor.py
│   │   │   │   ├── summarizer.py
│   │   │   │   └── chart_recommender.py
│   │   │   └── prompts/
│   │   │       ├── sql_generation.py
│   │   │       └── summarize.py
│   │   ├── core/
│   │   │   ├── llm_provider.py      # LLM 抽象层
│   │   │   └── db.py                # DuckDB + SQLite 连接管理
│   │   ├── dataspace/
│   │   │   ├── analysis_space.py    # 可问数据空间
│   │   │   └── verified_queries.py  # 已验证 question-SQL
│   │   ├── metadata/
│   │   │   ├── models.py            # SQLAlchemy 元数据模型
│   │   │   ├── explorer.py          # Schema Explorer
│   │   │   ├── sync.py              # DuckDB schema -> SQLite 同步
│   │   │   ├── semantic_overlay.py  # demo 业务语义补丁
│   │   │   └── service.py           # 元数据查询服务
│   │   ├── sql_guard/
│   │   │   ├── guard.py             # 主入口
│   │   │   ├── syntax_guard.py      # 语法检查
│   │   │   ├── operation_guard.py   # 操作类型检查
│   │   │   ├── scope_guard.py       # 表/字段白名单
│   │   │   └── cost_guard.py        # LIMIT / 超时
│   │   ├── execution/
│   │   │   └── runner.py            # 只读执行器
│   │   ├── visualization/
│   │   │   └── recommender.py       # 图表推荐
│   │   └── schemas/
│   │       └── api.py               # Pydantic request/response models
│   ├── tests/
│   │   ├── test_sql_guard.py
│   │   ├── test_execution.py
│   │   └── test_agent_nodes.py
│   ├── pyproject.toml
│   └── .env.example
│
├── frontend/
│   ├── src/
│   │   ├── App.vue
│   │   ├── main.ts
│   │   ├── api/
│   │   │   └── chat.ts              # SSE 客户端
│   │   ├── components/
│   │   │   ├── ChatInput.vue
│   │   │   ├── MessageList.vue
│   │   │   ├── StepFlow.vue
│   │   │   ├── SqlDisplay.vue
│   │   │   ├── ResultTable.vue
│   │   │   └── ChartView.vue
│   │   ├── stores/
│   │   │   └── chat.ts              # Pinia store
│   │   └── types/
│   │       └── index.ts
│   ├── package.json
│   └── vite.config.ts
│
├── scripts/
│   └── generate_ecommerce_data.py   # 电商数仓数据生成
│
├── data/
│   └── ecommerce.duckdb             # 生成后的 DuckDB 文件
│
└── docs/
    └── (existing)
```

## 3. Agent 工作流

### 3.1 Graph 流程

```
receive_question -> build_schema_context -> generate_sql -> sql_guard
  ├─ guard rejected -> 返回拒绝原因（终止）
  └─ guard passed -> execute_sql -> summarize_result -> recommend_chart -> 返回完整结果
```

### 3.2 Agent State

```python
class AgentState(TypedDict):
    question: str
    schema_context: str | None
    generated_sql: str | None
    guard_result: GuardResult | None
    execution_result: ExecutionResult | None
    summary: str | None
    chart_recommendation: ChartRec | None
    error: str | None
    steps: list[Step]
```

### 3.3 Schema Context 约束

Phase 1 不做复杂语义层，但 `build_schema_context` 必须包含这些确定性上下文，避免第一条 demo 查询依赖模型猜测：

- 可用表、字段、类型、字段说明
- fact 与 dim 的 join 关系
- 指标基础口径：销售额=`SUM(payment_amount)`，订单数=`COUNT(DISTINCT order_id)`，客单价=`SUM(payment_amount) / COUNT(DISTINCT order_id)`
- 数据集日期锚点：`dataset_current_date = 2025-12-31`
- 相对日期规则：用户说“最近30天”时，按数据集日期锚点解释为 `2025-12-02` 到 `2025-12-31`

Phase 1 不做 SQL 修复节点。Guard 或执行失败时直接返回结构化错误，修复闭环留到 Phase 5。

### 3.4 SSE 事件格式

每个节点完成后推送 step 事件，全部完成后推送 done 事件：

`POST /api/chat/query` 是 POST SSE，浏览器端不能用原生 `EventSource`。前端必须用 `fetch` + `ReadableStream` 读取 `text/event-stream`。Phase 1 暂不做“POST 创建 query_id + GET 订阅 SSE”。

```json
{"event": "step", "data": {"step": "build_context", "status": "completed"}}
{"event": "step", "data": {"step": "generate_sql", "status": "completed", "sql": "SELECT ..."}}
{"event": "step", "data": {"step": "sql_guard", "status": "completed", "allowed": true}}
{"event": "step", "data": {"step": "execute", "status": "completed", "rows": 30}}
{"event": "step", "data": {"step": "summarize", "status": "completed"}}
{"event": "step", "data": {"step": "recommend_chart", "status": "completed"}}
{"event": "done", "data": {"summary": "...", "table": {...}, "chart": {...}}}
```

## 4. SQL Guard

### 4.1 分层校验

1. **Syntax Guard**: SQLGlot parse，要求单语句，指定方言
2. **Operation Guard**: 只允许 SELECT，拒绝 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE
3. **Function Guard**: 拒绝 DuckDB 外部读取函数和系统/扩展相关语句，例如 `read_csv`、`read_parquet`、`read_json`、`COPY`、`INSTALL`、`LOAD`
4. **Scope Guard**: 提取所有表名和字段名，检查是否在表白名单、字段白名单内
5. **Cost Guard**: 无 LIMIT 则自动追加 LIMIT 500，已有 LIMIT 超过 500 则截断
6. **Connection Guard**: DuckDB 执行连接必须使用 read-only 模式，执行器只接收 Guard 后的 `normalized_sql`

### 4.2 返回结构

```python
class GuardResult(BaseModel):
    allowed: bool
    normalized_sql: str | None = None
    stage: str | None = None        # syntax / operation / scope / cost
    reason: str | None = None
    suggestion: str | None = None
    warnings: list[str] = []
```

### 4.3 单元测试（至少 15 个）

- 正常 SELECT 通过
- INSERT / UPDATE / DELETE 被拒绝
- DROP / ALTER / TRUNCATE 被拒绝
- 多语句被拒绝
- 非白名单表被拒绝
- 非白名单字段被拒绝
- DuckDB 外部读取函数被拒绝
- 自动补 LIMIT
- 已有 LIMIT 不重复补
- SQL 语法错误被捕获
- 子查询中的表名检查
- JOIN 多表白名单检查
- CTE 中的表名检查
- UNION 被拒绝（Phase 1 简化）
- 空字符串被拒绝
- 带注释的 SQL 正确处理
- LIMIT 值过大被截断

## 5. 元数据与数据模型

### 5.1 SQLite 元数据表

```sql
meta_tables(
    id INTEGER PRIMARY KEY,
    table_name TEXT NOT NULL UNIQUE,
    display_name TEXT,
    description TEXT,
    domain TEXT,          -- sales / user / product / channel
    row_count INTEGER DEFAULT 0,
    enabled BOOLEAN DEFAULT 1
)

meta_columns(
    id INTEGER PRIMARY KEY,
    table_id INTEGER REFERENCES meta_tables(id),
    column_name TEXT NOT NULL,
    data_type TEXT,
    description TEXT,
    is_dimension BOOLEAN DEFAULT 0,
    is_metric BOOLEAN DEFAULT 0,
    sample_values TEXT,    -- JSON array
    UNIQUE(table_id, column_name)
)

meta_relationships(
    id INTEGER PRIMARY KEY,
    source_table TEXT NOT NULL,
    source_column TEXT NOT NULL,
    target_table TEXT NOT NULL,
    target_column TEXT NOT NULL,
    relationship_type TEXT DEFAULT 'many_to_one',
    source TEXT DEFAULT 'overlay',  -- database_fk / inferred / overlay
    confidence REAL DEFAULT 1.0,
    fanout_risk TEXT DEFAULT 'low',
    description TEXT
)
```

### 5.2 DuckDB 电商数仓

```text
dim_date          (date_key, date_value, year, quarter, month, week, day_of_week)
dim_users         (user_key, user_id, name, gender, age_group, register_date, city)
dim_products      (product_key, product_id, name, category, sub_category, brand, price)
dim_regions       (region_key, province, city, region_group)
dim_channels      (channel_key, channel_name, channel_type)
fact_orders       (order_id, user_key, region_key, channel_key, date_key,
                   total_amount, discount_amount, payment_amount, order_status)
fact_order_items  (item_id, order_id, product_key, date_key,
                   quantity, unit_price, item_amount)
```

Phase 1 固定 join 关系：

```text
fact_orders.user_key      -> dim_users.user_key
fact_orders.region_key    -> dim_regions.region_key
fact_orders.channel_key   -> dim_channels.channel_key
fact_orders.date_key      -> dim_date.date_key
fact_order_items.order_id -> fact_orders.order_id
fact_order_items.product_key -> dim_products.product_key
fact_order_items.date_key -> dim_date.date_key
```

### 5.3 数据规模

- 时间范围: 2024-01 ~ 2025-12（2 年）
- 数据日期锚点: 2025-12-31，用于解释“最近30天”“本月”“今年”等相对时间表达
- dim_users: ~50 用户
- dim_products: ~100 商品（5 大品类，每类 3-5 子类）
- dim_regions: ~30 城市（5 大区）
- dim_channels: 5 渠道
- fact_orders: ~10,000 订单
- fact_order_items: ~30,000 明细

### 5.4 Schema Explorer 定位

Schema 探知是本项目的核心能力，不只是 metadata sync 的附属脚本。成熟 NL2SQL / BI 产品的共同做法不是让 agent 每次查询时临场扫库，而是先构建可复用、可审计、可增量更新的 metadata layer。

Phase 1 开始就把它作为独立模块设计：

```text
Schema Explorer
  -> connector introspection
  -> physical metadata sync
  -> lightweight profiling
  -> relationship discovery
  -> semantic overlay
  -> schema context builder
```

基本原则：

- 物理 schema 以数据库 introspection 为真源，不在代码里硬编码表和字段
- agent 可以辅助解释和补全语义，但探知结果必须落库，不在查询时临时拼上下文
- schema context 只从已同步、已过滤、可审计的 metadata 生成
- relationship、metric、alias、business term 都要记录来源，避免把模型猜测伪装成事实
- SQL 执行仍必须经过 SQL Guard，不能因为 schema 探知更强就放松安全边界

### 5.5 成熟产品参考

**Data Catalog / Governance 产品**

Microsoft Purview、Alation、Collibra、DataHub、OpenMetadata、Atlan 这类产品说明了 metadata ingestion 的工业做法：先注册数据源，再扫描 schema、classification、lineage、profile、owner、tag 等信息。Microsoft Purview 的扫描会捕获 technical metadata、抽取 structured schema、应用分类和 lineage ingestion；这说明“探知”应该是可重复运行的离线/准实时能力，而不是 query-time 行为。

参考：

- [Microsoft Purview scans and ingestion](https://learn.microsoft.com/en-us/purview/concept-scans-and-ingestion)
- [Microsoft Purview scan data sources](https://learn.microsoft.com/en-us/azure/purview/scan-data-sources)
- [DataHub documentation](https://docs.datahub.com/)
- [OpenMetadata column-level lineage](https://docs.open-metadata.org/v1.11.x/how-to-guides/data-lineage/column)

**Semantic Layer / BI Modeling 产品**

Looker、dbt Semantic Layer、Cube、Tableau Pulse 说明了另一个事实：物理 schema 不等于业务可问数模型。Looker 用 LookML 的 Explore、View、Join、Dimension、Measure 建模查询入口和 join 关系；Snowflake Cortex Analyst 用 semantic model YAML 描述 logical tables、dimensions、facts、metrics、relationships、verified queries；Tableau Pulse Metrics Layer 强调 KPI 和业务指标口径。

参考：

- [LookML introduction](https://docs.cloud.google.com/looker/docs/what-is-lookml)
- [Looker Explore parameter](https://docs.cloud.google.com/looker/docs/reference/param-explore-explore)
- [Looker working with joins](https://docs.cloud.google.com/looker/docs/working-with-joins)
- [Snowflake Cortex Analyst semantic model specification](https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-analyst/semantic-model-spec)

**NL2SQL / Agent Analytics 产品**

Snowflake Cortex Analyst、Databricks AI/BI Genie、ThoughtSpot 的共性是：给自然语言问数提供一个受控的数据语义空间，而不是把整个数据库裸露给 LLM。Databricks Genie 使用 Genie space 和 trusted assets，把可问的数据资产限定在明确边界内；Snowflake Cortex Analyst 的 semantic model 还支持 verified queries，用已验证问答提升生成稳定性。

参考：

- [Databricks AI/BI Genie](https://docs.databricks.com/aws/genie/)
- [Snowflake Cortex Analyst semantic model specification](https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-analyst/semantic-model-spec)

### 5.6 参考项目对比

本地参考项目“掌柜问数”的做法不是完全自动 explore schema，而是：

- 用 `meta_config.yaml` 人工维护表、字段、role、description、alias、指标
- 从数据仓库补字段类型和字段取值
- 将字段/指标写入 MySQL 元数据库
- 用 Qdrant 做字段和指标向量召回
- 用 Elasticsearch 做字段值全文召回
- 查询时先召回候选字段、指标和值，再让 LLM 过滤上下文并生成 SQL

优点：

- 对大 schema 友好，不需要把全量表字段塞进 prompt
- alias、description、metric 是一等元数据，中文问数命中率更高
- 字段值召回能处理“华东”“会员等级”“品牌名”等自然语言值
- Graph 拆得细，方便展示召回、过滤、生成、校验、修复过程

缺点：

- schema 语义主要靠 YAML 人工维护，不是自动适配任意数据库
- 强绑定 MySQL、Qdrant、Elasticsearch、Embedding 服务，Phase 1 过重
- join 关系只靠 primary_key / foreign_key role，没有显式 relationship edge 和置信度
- SQL 安全主要靠 prompt、EXPLAIN 和执行错误修复，缺少强 SQL Guard
- query-time 召回链路较长，调试和部署成本高

### 5.7 本项目取舍

本项目采用“自动探知 + 语义补丁 + 强安全”的分层方案：

```text
metadata sync
  -> introspect physical schema
  -> profile columns
  -> infer relationships
  -> apply semantic overlay
  -> build_schema_context
```

Phase 1 只实现轻量但方向正确的版本：

- DB introspection 是物理 schema 真源
- 自动同步表名、字段名、字段类型、row count
- 自动采样少量 sample values
- relationship 先支持 `database_fk` / `inferred` / `overlay` 三类来源，并记录 confidence
- semantic overlay 只做业务补丁，不定义物理 schema
- 不引入 Qdrant / Elasticsearch / Embedding 服务
- 不做复杂召回，只生成完整但可控的 schema context
- SQL Guard 必须保留，不能只依赖 prompt 和 `EXPLAIN`

后续 Phase 2/4 再扩展字段别名、指标语义层、值召回、向量召回和 verified query。

### 5.8 成熟产品能力取舍矩阵

除 Schema Explorer 外，成熟产品还有几类值得借鉴的能力。按 value / cost 评估如下，满分 5。

| 能力 | 参考产品 | value | cost | 阶段 |
|------|----------|-------|------|------|
| Trusted Assets / Analysis Space | Databricks Genie | 5 | 2 | Phase 1 |
| Verified Queries | Snowflake Cortex Analyst | 5 | 2 | Phase 1/2 |
| Relationship Safety / Fanout Risk | Looker | 5 | 3 | Phase 1/2 |
| Metric Layer | Looker / Tableau Pulse / Snowflake | 5 | 3 | Phase 2 |
| Explainability | Genie / BI 产品 | 4 | 2 | Phase 1 |
| Feedback Loop | Genie / Cortex Analyst | 4 | 3 | Phase 2/3 |
| Value Recall | ThoughtSpot / 掌柜问数 | 4 | 4 | Phase 4 |
| Data Quality Profiling | Purview / DataHub / OpenMetadata | 3 | 3 | Phase 2 |
| Cost / Performance Governance | Databricks / ClickHouse | 4 | 3 | Phase 6 |
| Lineage / Impact Analysis | Purview / DataHub / OpenMetadata | 3 | 4 | Phase 6+ |
| Permission / Governance | Purview / Collibra | 3 | 5 | Phase 8 或文档说明 |

### 5.9 能力选择分析

**Trusted Assets / Analysis Space**

Databricks Genie 的 Genie Space 思路是：业务用户不是对整个 workspace 任意问数，而是在一个受控的数据空间里问数。这个能力 value 高、cost 低，适合 Phase 1。

Phase 1 轻量实现：

```text
analysis_space:
  name: ecommerce_demo
  datasource: duckdb_ecommerce
  tables: [fact_orders, fact_order_items, dim_date, dim_products, dim_regions, dim_channels, dim_users]
  enabled_metrics: [sales_amount, order_count, aov]
  allowed_operations: [select]
```

它和 SQL Guard 互补：Analysis Space 决定“可以问哪些可信资产”，SQL Guard 决定“生成 SQL 是否允许执行”。

**Verified Queries**

Snowflake Cortex Analyst 的 semantic model 支持 verified queries。它本质上是已验证的问题-SQL 对，可以同时服务 demo、few-shot、eval 和回归测试。

Phase 1/2 轻量实现：

```yaml
- id: recent_30d_sales
  question: 查询最近30天每日销售额和订单数
  sql: SELECT ...
  verified_by: system
  tags: [sales, time_series]
```

这个能力比单纯 prompt example 更工程化，因为它可以被 eval runner 和 prompt builder 同时复用。

**Relationship Safety / Fanout Risk**

Looker 对 join relationship 很谨慎，因为错误 join 会导致聚合膨胀。NL2SQL 项目里这比“能不能 join 上”更重要。

Phase 1/2 需要在 relationship metadata 里记录：

```text
relationship_type: many_to_one / one_to_one / one_to_many
join_type: left / inner
source: database_fk / inferred / overlay
confidence: 0.0-1.0
fanout_risk: low / medium / high
```

例如从 `fact_order_items` join 到 `fact_orders` 后再计算 `SUM(fact_orders.payment_amount)`，可能因为明细行重复导致销售额膨胀。Schema Context 必须提示这类风险，SQL 生成和 Guard 都要能利用。

Phase 1 的 confidence 规则：

- `database_fk`: `1.0`
- `overlay`: `1.0`
- `inferred`: `0.6-0.9`，按命名规则强弱赋值
- Phase 1 只记录并展示 confidence，不用于过滤、不参与 SQL Guard 决策
- Phase 2 开始用于关系过滤、join path 排序和 fanout 风险提示

**Metric Layer**

Looker、Tableau Pulse、Snowflake Cortex Analyst 都强调 metric / measure 的显式建模。指标不能只写在 prompt 文本里。

Phase 2 设计方向：

```text
metric:
  name: sales_amount
  label: 销售额
  expression: SUM(fact_orders.payment_amount)
  default_time_column: dim_date.date_value
  allowed_dimensions: [date, channel, region, category]
```

Metric Layer 应该和 relationship safety 联动：某个指标允许哪些维度，哪些 join path 会导致 fanout，都要可表达。

**Explainability**

成熟问数产品都会解释“为什么用了这些数据”。Phase 1 可以低成本实现：

```text
matched_tables
matched_columns
matched_metrics
join_paths
date_interpretation
guard_result
```

这对调试、演示和用户信任都很重要。

**Feedback Loop**

用户反馈可以沉淀为 verified query 或 eval case。Phase 2/3 再做：

```text
question
generated_sql
final_sql
result_snapshot
user_feedback
corrected_sql
promoted_to_verified_query
```

**Value Recall**

参考项目“掌柜问数”用 Elasticsearch 做字段值召回，这个方向是对的：用户说“华东”“天猫”“美妆”时，系统要知道这些值属于哪个字段。但它依赖 ES/Embedding，Phase 1 不做。Phase 4 再做规则 + 向量/全文混合召回。

### 5.10 与参考项目的能力对比

参考项目“掌柜问数”已经具备：

- 字段召回
- 指标召回
- 字段值召回
- LLM 过滤候选表字段
- SQL 生成
- EXPLAIN 校验
- SQL 修复
- 流式进度输出

本项目不直接照搬，原因：

- 它的 schema 语义主要由 `meta_config.yaml` 人工维护，不是 DB introspection 真源
- 它依赖 MySQL + Qdrant + Elasticsearch + Embedding，Phase 1 成本过高
- 它没有强 SQL Guard，执行器会直接执行生成 SQL
- 它没有 explicit trusted assets / analysis space 边界
- 它没有显式 fanout risk 建模
- 它没有 verified query 和 eval 的统一闭环

本项目选择：

- Phase 1 优先做 Schema Explorer、Analysis Space、SQL Guard、Explainability、最小 Verified Queries
- Phase 2 做 Metric Layer、Relationship Safety 完整化、Feedback Loop
- Phase 3 做 Eval Runner，把 verified queries 和 feedback 转成评测资产
- Phase 4 再做 Value Recall 和向量召回

## 6. API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat/query` | NL2SQL 查询（SSE 流式响应） |
| GET | `/api/metadata/tables` | 获取表列表 |
| GET | `/api/metadata/tables/{name}/columns` | 获取表字段 |
| POST | `/api/metadata/sync` | 触发元数据同步 |
| GET | `/api/query/history` | 查询历史（Phase 1 简化版） |

### 6.1 `/api/chat/query`

请求:
```json
{"question": "查询最近30天每日销售额和订单数"}
```

响应: SSE 流，见 3.4 节。前端使用 `fetch` + `ReadableStream` 读取，不使用原生 `EventSource`。

## 7. 前端

### 7.1 页面布局

```
┌─────────────────────────────────────────────┐
│  Header: NL2SQL Data Agent                  │
├─────────────────────────────────────────────┤
│  [MessageList]                              │
│    User message                             │
│    Assistant message:                       │
│      StepFlow (步骤进度)                     │
│      SqlDisplay (SQL 代码)                   │
│      ResultTable / ChartView (切换)          │
│      Summary (中文总结)                      │
│                                             │
│  [ChatInput] 输入你的数据分析问题...        │
└─────────────────────────────────────────────┘
```

### 7.2 组件

| 组件 | 职责 |
|------|------|
| ChatInput | 输入框 + 发送按钮 |
| MessageList | 消息列表 |
| StepFlow | 步骤进度条，实时 SSE 更新 |
| SqlDisplay | SQL 代码高亮（Prism.js） |
| ResultTable | 表格展示查询结果 |
| ChartView | ECharts 图表，支持 line/bar/pie |

### 7.3 前端流式客户端

`src/api/chat.ts` 用 `fetch('/api/chat/query', { method: 'POST', ... })` 发起请求，并从 `response.body.getReader()` 增量解析 SSE 文本。解析逻辑只处理 `step`、`done`、`error` 三类事件。

## 8. Iteration 拆分

Phase 1 不一次性实现完整链路，拆成 5 个可独立验收的小迭代。每个 iteration 都必须能运行、能验证，避免数据、Guard、LLM、SSE、前端问题混在一起。Iteration 1 内部再拆成 I1.1-I1.4 四个小任务，但不改变 Phase 1 的 5 个主迭代结构。

### Iteration 1：数据和元数据底座

目标：不接 LLM，不做前端，先让数据、元数据和 schema context 稳定。

Iteration 1 拆成 4 个小任务，避免把 schema explore、semantic overlay、analysis space 和 explainability 混在一起。

#### I1.1 Schema Explorer 补齐

目标：把 metadata sync 从“能同步表字段”升级为轻量 Schema Explorer。

交付：

- backend 基础骨架
- DuckDB 电商数据生成
- SQLite 元数据表
- `meta_tables` / `meta_columns` / `meta_relationships`
- metadata sync：从 DuckDB introspection 自动同步表、字段、类型和 row count
- lightweight profiling：采样少量 sample values
- relationship source：记录 `database_fk` / `inferred` / `overlay` 和 confidence
- relationship inference：支持简单命名规则推断

验收：

- 能生成 `data/ecommerce.duckdb`
- 能从 DuckDB 自动同步表字段、字段类型和 row count
- 能采样并保存少量 sample values
- sync 后能看到 relationship source、confidence 和 fanout_risk

后续注意事项：

- relationship inference 当前允许保留 demo 规则，例如 `order_id -> fact_orders`。后续泛化时需要抽成规则配置，支持 `*_id -> dim_*`、多事实表和复合键。
- sample values 当前使用小数据集上的 `SELECT DISTINCT ... ORDER BY ... LIMIT 5`。后续大表需要按列类型、基数和表规模选择采样策略，或使用数据库原生采样能力。
- 兼容旧 `metadata.sqlite` 的轻迁移代码只服务早期迭代。后续引入正式 migration 后可以删除。
- `list_relationships()` 已返回 source、confidence、fanout_risk，但 Phase 1 API 暂不暴露 relationship list。I1.4 或 metadata API 扩展时再补 `/api/metadata/relationships`。

#### I1.2 Semantic Overlay 重构

目标：把当前静态配置降级为业务语义补丁，不再作为物理 schema 定义源。

交付：

- `static.py` 重构为 `semantic_overlay.py`
- overlay 补充 demo 业务说明、字段说明、维度/指标角色、指标口径
- overlay 可以确认或覆盖 relationship metadata
- overlay 不允许定义不存在于 introspection 结果中的物理表字段

验收：

- 删除或清空 overlay 时，仍能同步物理表字段
- 启用 overlay 后，字段说明、指标角色和确认 join 能写入 metadata

#### I1.3 Analysis Space + Verified Queries

目标：建立最小可问数据空间和已验证问答资产。

交付：

- Analysis Space v1：可问表、可用指标、允许操作
- Verified Queries v0：至少 1 条 demo question-SQL
- `build_schema_context` 按 analysis space 过滤表和字段
- `build_schema_context`
- 固定 `dataset_current_date = 2025-12-31`

验收：

- schema context 只包含 analysis space 允许的表
- schema context 包含 demo verified query
- schema context 包含 `dataset_current_date`

#### I1.4 Explainability Context

目标：为后续 Agent 和前端步骤展示准备结构化解释信息。

交付：

- `build_explainability_context`
- 输出 tables、columns、metrics、join paths、date rule
- relationship 输出 source、confidence、fanout_risk

验收：

- 脚本能打印结构化 JSON explainability context
- JSON 中能看到 schema、join 来源、confidence、日期规则和指标口径
- 能打印或返回完整 schema context

### Iteration 2：SQL Guard 和只读执行器

目标：先把安全边界做实，再接 Agent 和前端。

Iteration 2 拆成 4 个小任务。原因是这一轮同时涉及 SQL AST 解析、安全策略、scope 绑定、SQL 改写、只读执行和测试；如果一次性做完，失败时很难判断问题来自 Guard、metadata scope 还是 executor。

交付：

- Syntax Guard: SQLGlot parse，单语句，指定方言
- Operation Guard: 只允许 SELECT，拒绝 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE
- Function Guard: 拒绝 DuckDB 外部读取函数和系统/扩展相关语句（`read_csv`、`read_parquet`、`read_json`、`COPY`、`INSTALL`、`LOAD`）
- Scope Guard: 表白名单 + 字段白名单
- Cost Guard: 无 LIMIT 自动追加 500，已有 LIMIT 超过 500 截断
- Connection Guard: DuckDB read-only 连接，执行器只接收 Guard 后的 `normalized_sql`
- SQL Guard 单元测试（15+）

#### I2.1 SQL Guard Core

目标：先把 SQL 的结构化解析和高危语句拦截做出来，不接执行器。

交付：

- 新增 `sql_guard/` 模块
- 引入 `sqlglot`
- 定义 Guard 输入输出模型：`allowed`、`normalized_sql`、`reason`、`stage`、`warnings`
- Syntax Guard：SQLGlot parse、单语句校验、DuckDB dialect
- Operation Guard：只允许 `SELECT`
- Function Guard：拒绝 DuckDB 外部读取函数和系统扩展语句

验收：

- 合法 `SELECT` 通过
- 多语句被拒绝
- `DELETE`、`DROP`、`CREATE`、`COPY`、`INSTALL`、`LOAD` 被拒绝
- `read_csv`、`read_parquet`、`read_json` 被拒绝

#### I2.2 Scope Guard

目标：把 Analysis Space 和 metadata 接入 Guard，让 SQL 只能访问可信表和可信字段。

交付：

- 从 Analysis Space 读取表白名单
- 从 metadata 读取字段白名单
- 校验 SQL 访问表必须在白名单内
- 校验 SQL 访问字段必须属于允许表
- 支持基础 alias 解析
- 对 unqualified column 使用保守策略：只能在已引用表中唯一匹配时通过

验收：

- 白名单表和字段通过
- 非白名单表被拒绝
- 非白名单字段被拒绝
- alias 场景可以正确识别真实表

#### I2.3 Cost Guard 和 Normalized SQL

目标：让后续执行器只执行 Guard 归一化后的 SQL，并限制结果规模。

交付：

- 无 `LIMIT` 自动追加 `LIMIT 500`
- 已有 `LIMIT > 500` 截断为 500
- 已有 `LIMIT <= 500` 保留
- 返回 `normalized_sql`
- 返回结构化 warning，例如 `LIMIT 500 was added automatically`

验收：

- 无 LIMIT 查询会被补 LIMIT
- 大 LIMIT 会被压到 500
- 小 LIMIT 不被改写
- 后续 executor 不接受原始 SQL，只接受 `normalized_sql`

#### I2.4 Readonly Executor 和 Guard Tests

目标：把 Guard 和 DuckDB 只读执行串起来，形成安全执行边界。

交付：

- 新增 `execution/runner.py`
- DuckDB 使用 read-only connection
- executor 只接收 Guard 后的 `normalized_sql`
- 返回 `columns`、`rows`、`row_count`
- SQL Guard 单元测试 15+

验收：

- 15+ Guard 测试通过
- 合法 SQL 能执行
- DELETE、DROP、CREATE、非白名单表、非白名单字段、`read_csv` 被拒绝
- 只读 executor 不暴露直接执行原始 SQL 的入口

### Iteration 3：Mock Agent 和 SSE

目标：先不接真实模型，用 Mock provider 跑通完整后端链路。

交付：

- `LLMProvider` 抽象
- `MockLLMProvider`
- LangGraph 节点（含 `agent/prompts/sql_generation.py` 和 `agent/prompts/summarize.py` 的接口签名）
- `/api/chat/query`
- POST SSE：`step` / `done` / `error`
- 基础图表推荐（`recommender.py` 输出结构：`chart_type` + `x_column` + `y_columns`）
- Query-level explainability 输出：`matched_tables`、`matched_columns`、`join_paths`、`date_interpretation`、`guard_result`

验收：

- 问”最近30天每日销售额和订单数”
- Mock provider 对 demo 问题返回固定 SQL；对安全 smoke case 返回对应危险 SQL，确保 Guard 能真实拦截
- SSE 能看到 `build_context`、`generate_sql`、`sql_guard`、`execute`、`summarize`、`recommend_chart` 步骤
- SSE done 结果包含 query-level explainability
- 图表推荐返回结构化 JSON，包含 chart_type 和列映射

### Iteration 4：DeepSeek 和前端页面

目标：接真实 LLM 和 Vue 页面，完成可演示体验。

交付：

- DeepSeek provider
- SQL generation prompt
- Vue 聊天页
- `fetch` + `ReadableStream` SSE 客户端
- SQL 展示
- 表格
- ECharts 折线图

验收：

- 前端能完整跑通 demo 问题
- 错误和 Guard 拒绝能展示
- SSE error 事件格式正确，前端能解析并展示拒绝原因

### Iteration 5：Smoke Eval 和 Demo 固化

目标：防回归，保证 Phase 1 可反复演示。

交付：

- 10 条 smoke eval
- 5 条正常查询（含 2 条 JOIN 场景）
- 5 条安全用例（含 CREATE 拒绝）
- README 更新当前启动方式和 demo 问题

验收：

- 无 API Key 时 Mock eval 可跑
- 有 API Key 时真实链路可演示
- Phase 1 验收项全部通过

## 9. Phase 1 实现顺序

按 iteration 推进，不跨 iteration 提前做后续功能。每个 iteration 验收通过后再进入下一步。

```
Iteration 1
  -> backend skeleton
  -> config.py
  -> core/db.py
  -> scripts/generate_ecommerce_data.py
  -> metadata/models.py + sync.py + service.py
  -> build_schema_context

Iteration 2
  -> I2.1 sql_guard/ core parse + operation/function guards
  -> I2.2 scope guard with Analysis Space + metadata whitelist
  -> I2.3 cost guard + normalized_sql
  -> I2.4 execution/runner.py + read-only connection + SQL Guard tests

Iteration 3
  -> core/llm_provider.py with Mock provider
  -> agent/state.py + nodes/
  -> agent/prompts/sql_generation.py + summarize.py（接口签名）
  -> agent/graph.py
  -> api/chat.py
  -> visualization/recommender.py

Iteration 4
  -> DeepSeek provider
  -> SQL generation prompt
  -> frontend chat page
  -> fetch + ReadableStream SSE client
  -> SQL display / result table / chart renderer

Iteration 5
  -> evals/smoke_cases.yaml
  -> smoke runner
  -> README demo instructions
```

## 10. Phase 1 最小评测

Phase 1 不做完整 eval runner 报告，但必须保留 smoke eval，防止后续改动破坏最小闭环。

- 5 条正常查询：最近30天销售额趋势、月销售额、渠道销售额、Top10 商品（JOIN）、地区销售额（JOIN）
- 5 条安全用例：删除数据、DROP 表、CREATE TABLE 被拒绝、访问非白名单表、调用 DuckDB 外部读取函数
- smoke runner 可以直接复用 Mock provider。正常查询返回确定性 SQL，安全用例返回对应危险 SQL，保证无 API Key 时也能跑通 Guard、执行器和图表推荐基础链路，并真实验证 Guard 拒绝路径

## 11. 验收标准

- 前端输入 "查询最近30天每日销售额和订单数" 返回完整结果
- SSE 展示每个步骤的执行状态，error 事件格式正确
- SQL 代码高亮展示
- 表格展示查询结果
- 折线图展示趋势
- 危险请求（如 "删除2024年数据"）被 SQL Guard 阻断，前端展示拒绝原因
- SQL Guard 至少 15 个单元测试全部通过
- 元数据同步可从 DuckDB 导入表结构
- `GET /api/metadata/tables` 返回正确表列表
- `GET /api/metadata/tables/{name}/columns` 返回正确字段列表
- Schema Explorer 可同步表、字段、类型、row count 和 sample values
- Analysis Space 可展示可问表、可用指标和允许操作
- 至少保留 1 条 verified query，并可被 demo / smoke eval 复用
- `build_schema_context` 包含 join 关系、relationship source、confidence 和 `dataset_current_date = 2025-12-31`
- 查询响应包含 query-level explainability：命中的表、字段、指标、join path、时间解释和 Guard 结果
- 最小 smoke eval 可运行，正常查询（含 JOIN 场景）和安全用例（含 CREATE 拒绝）都能给出确定性结果

## 12. 后续 Phase 概览

| Phase | 目标 | 预计周期 |
|-------|------|----------|
| 2 | 元数据语义层（指标口径、别名、示例 SQL） | 5-8 天 |
| 3 | 评测体系（eval runner + 30 条 case） | 3-5 天 |
| 4 | 向量召回与上下文增强 | 5-8 天 |
| 5 | SQL 修复与执行反馈闭环 | 4-6 天 |
| 6 | ClickHouse + OLAP 能力增强 | 5-8 天 |
| 7 | MCP 工具化 | 4-6 天 |
| 8 | 产品化与求职包装 | 3-5 天 |
