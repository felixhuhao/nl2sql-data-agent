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
│   │   ├── metadata/
│   │   │   ├── models.py            # SQLAlchemy 元数据模型
│   │   │   ├── sync.py              # DuckDB schema -> SQLite 同步
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

### 5.4 元数据同步

`POST /api/metadata/sync` 触发，读取 DuckDB information_schema，upsert 到 SQLite。

Phase 1 的 join 关系不依赖 DuckDB 自动推断，随数据生成脚本一起写入 `meta_relationships`。字段说明、维度/指标标记、少量枚举样例值可以用静态配置补齐，避免第一版过度开发元数据编辑后台。

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

Phase 1 不一次性实现完整链路，拆成 5 个可独立验收的小迭代。每个 iteration 都必须能运行、能验证，避免数据、Guard、LLM、SSE、前端问题混在一起。

### Iteration 1：数据和元数据底座

目标：不接 LLM，不做前端，先让数据、元数据和 schema context 稳定。

交付：

- backend 基础骨架
- DuckDB 电商数据生成
- SQLite 元数据表
- `meta_tables` / `meta_columns` / `meta_relationships`
- metadata sync
- `build_schema_context`
- 固定 `dataset_current_date = 2025-12-31`

验收：

- 能生成 `data/ecommerce.duckdb`
- 能同步表字段；能写入固定 join 关系并被 `build_schema_context` 读取
- 能打印或返回完整 schema context

### Iteration 2：SQL Guard 和只读执行器

目标：先把安全边界做实，再接 Agent 和前端。

交付：

- Syntax Guard: SQLGlot parse，单语句，指定方言
- Operation Guard: 只允许 SELECT，拒绝 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE
- Function Guard: 拒绝 DuckDB 外部读取函数和系统/扩展相关语句（`read_csv`、`read_parquet`、`read_json`、`COPY`、`INSTALL`、`LOAD`）
- Scope Guard: 表白名单 + 字段白名单
- Cost Guard: 无 LIMIT 自动追加 500，已有 LIMIT 超过 500 截断
- Connection Guard: DuckDB read-only 连接，执行器只接收 Guard 后的 `normalized_sql`
- SQL Guard 单元测试（15+）

验收：

- 15+ Guard 测试通过
- 合法 SQL 能执行
- DELETE、DROP、CREATE、非白名单表、`read_csv` 被拒绝

### Iteration 3：Mock Agent 和 SSE

目标：先不接真实模型，用 Mock provider 跑通完整后端链路。

交付：

- `LLMProvider` 抽象
- `MockLLMProvider`
- LangGraph 节点（含 `agent/prompts/sql_generation.py` 和 `agent/prompts/summarize.py` 的接口签名）
- `/api/chat/query`
- POST SSE：`step` / `done` / `error`
- 基础图表推荐（`recommender.py` 输出结构：`chart_type` + `x_column` + `y_columns`）

验收：

- 问”最近30天每日销售额和订单数”
- Mock provider 对 demo 问题返回固定 SQL；对安全 smoke case 返回对应危险 SQL，确保 Guard 能真实拦截
- SSE 能看到 `build_context`、`generate_sql`、`sql_guard`、`execute`、`summarize`、`recommend_chart` 步骤
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
  -> sql_guard/
  -> execution/runner.py
  -> SQL Guard tests

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
- `build_schema_context` 包含固定 join 关系和 `dataset_current_date = 2025-12-31`
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
