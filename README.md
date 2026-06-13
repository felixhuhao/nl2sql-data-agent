# Industrial NL2SQL Data Agent Platform

An OLAP-focused NL2SQL data agent that turns business questions into guarded SQL, executes them on DuckDB or ClickHouse, explains the result path, recommends charts, and validates behavior through repeatable evals.

The project is designed as a production-shaped data agent rather than a prompt-only demo: semantic metadata is persisted, retrieval is scoped, SQL execution is guarded by deterministic code, repair is bounded, and every major behavior is covered by smoke evals.

This repository currently represents the **V1 baseline**: a runnable and validated foundation for guarded NL2SQL over DuckDB and ClickHouse, not the final form of the platform.

## Highlights

- **Semantic metadata layer**: DB-backed tables, columns, metrics, aliases, verified queries, relationships, and analysis spaces.
- **Focused context retrieval**: rule retrieval, bundled Qdrant vector retrieval, value recall, and context compression before SQL generation.
- **Guarded execution**: SQLGlot-based SELECT-only guard, table and column scope checks, dangerous function/command blocking, fanout detection, and automatic LIMIT.
- **Multi-source OLAP support**: DuckDB for local analytics and ClickHouse for OLAP warehouse behavior, with dialect-aware prompts, guard rules, and EXPLAIN hints.
- **Multi-turn follow-up**: preserves dimensions, filters, metrics, and time windows across follow-up questions such as "只看华东", "换成订单数", and "改成最近90天".
- **Repair loop**: bounded SQL repair for syntax, scope, fanout, and execution errors; repaired SQL must pass Guard again before execution.
- **Automatic visualization**: line, bar, pie, dual-axis, and table fallback recommendations based on result shape and OLAP intent.
- **Evaluation loop**: mock smoke eval, DeepSeek real eval, result equivalence checks, error attribution, chart assertions, and performance hint assertions.
- **MCP tooling**: read-only schema, guarded query, EXPLAIN, and metric search tools that reuse the same backend safety path.

## Architecture

```text
Question
  -> datasource selection
  -> intent guard
  -> metadata retrieval
  -> focused schema context
  -> OLAP intent detection
  -> SQL generation
  -> SQL Guard
  -> optional SQL repair
  -> read-only execution
  -> performance explanation
  -> summary and chart recommendation
```

```text
Frontend (Vue 3)
  -> FastAPI / SSE
      -> Agent workflow nodes
          -> Metadata semantic layer (SQLite)
          -> LLM provider (Mock / DeepSeek)
          -> SQL Guard (SQLGlot)
          -> DataSourceManager
              -> DuckDB
              -> ClickHouse
          -> Chart recommender
```

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Demo Evidence

V1 regression report:

- [Mock smoke eval report](evals/reports/smoke_latest.md): `76/76` passed, including DuckDB `51/51` and ClickHouse `25/25`.
- [DeepSeek real eval report](evals/reports/deepseek_latest.md): real-model SQL generation and result-equivalence checks.

### Query Workflow

![ClickHouse daily sales query](docs/assets/screenshots/query_daily_sales_clickhouse.png)

### Multi-Turn Follow-Up

Try this chain:

```text
最近30天销售额
按地区拆分
只看华东
换成订单数
改成最近90天
```

The agent treats later turns as follow-ups and keeps the unchanged constraints while switching dimension, filter, metric, or time window.

![Multi-turn follow-up V1 step 5](docs/assets/screenshots/query_followup_step5_time_90d.png)

### SQL Guard

![SQL Guard blocks destructive intent](docs/assets/screenshots/sql_guard_blocked.png)

### Semantic Layer Admin

![ClickHouse table metadata](docs/assets/screenshots/admin_tables_clickhouse.png)

More screenshots:

- [TopN query with explainability](docs/assets/screenshots/query_top_products_explainability.png)
- [Verified queries admin](docs/assets/screenshots/admin_verified_queries.png)
- [Relationships admin](docs/assets/screenshots/admin_relationships.png)
- [ClickHouse channel sales query](docs/assets/screenshots/query_channel_sales_clickhouse.png)
- [Follow-up step 1: recent 30-day sales](docs/assets/screenshots/query_followup_step1_recent30_sales.png)
- [Follow-up step 2: region breakdown](docs/assets/screenshots/query_followup_step2_region_breakdown.png)
- [Follow-up step 3: East China filter](docs/assets/screenshots/query_followup_step3_filter_east.png)
- [Follow-up step 4: order count metric](docs/assets/screenshots/query_followup_step4_metric_order_count.png)
- [Follow-up step 5: recent 90-day time window](docs/assets/screenshots/query_followup_step5_time_90d.png)

## Quick Start

The fastest path is Docker Compose. It starts ClickHouse, Qdrant, backend, and frontend; the backend entrypoint generates DuckDB data, seeds ClickHouse, and syncs metadata for both datasources.

```bash
docker compose up -d --build
```

Open:

```text
Frontend: http://127.0.0.1:5174/
Backend:  http://127.0.0.1:8000/api/health
```

The default Compose stack uses `LLM_PROVIDER=auto`: it uses DeepSeek when `DEEPSEEK_API_KEY` is configured and falls back to mock otherwise. Qdrant is bundled into the stack and persisted in a Docker volume. Vector embeddings use the default MiniLM sentence-transformers model with CPU-only PyTorch; CUDA and external model mounts are not required. For local development, DeepSeek, vector retrieval, and MCP client setup, see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Try These Questions

Core analytics:

- 查询最近30天每日销售额和订单数
- 按地区统计最近30天销售额
- 按渠道统计最近30天销售额
- 最近30天销量最高的10个商品
- 各渠道销售占比
- 按月统计销售额同比
- 最近30天每日销售额7日移动平均

Multi-turn:

- 最近30天销售额
- 按地区拆分
- 只看华东
- 换成订单数
- 改成最近90天

Safety:

- 删除2024年的订单数据
- DROP fact_orders
- SELECT * FROM read_csv('orders.csv')

Dangerous requests are rejected before execution, with the blocking stage and reason shown in the UI.

## Evaluation

Run the full mock smoke suite:

```bash
python scripts/run_smoke_eval.py
```

V1 full run with ClickHouse available:

```text
76/76 smoke cases passed.
DuckDB (本地) - 51 cases: 51/51 passed.
ClickHouse (OLAP) - 25 cases: 25/25 passed.
focused context: avg=2080 chars, full=7707 chars, avg_reduction=73.2%
```

The suite covers:

- DuckDB and ClickHouse SQL execution.
- Retrieval expected hits and focused context reduction.
- SQL Guard, safety blocks, fanout repair, and max-repair exhaustion.
- OLAP TopN, share, YoY/MoM, moving average, and ClickHouse functions.
- Multi-turn follow-up persistence for filter, metric, and time changes.
- Chart recommendation and ClickHouse performance hints.

Run DeepSeek real eval:

```bash
python scripts/run_smoke_eval.py --provider deepseek --report-path evals/reports/deepseek_latest.md
```

Real eval requires `DEEPSEEK_API_KEY`. Mock eval does not.

## MCP Tools

The project exposes read-only MCP servers that reuse the same backend metadata, SQL Guard, datasource manager, and execution path.

```text
mcp_servers.db_tools
  list_tables
  get_table_schema
  query_readonly

mcp_servers.olap_tools
  explain_query
  metric_catalog_search
```

Example safety behavior:

```text
query_readonly("DELETE FROM fact_orders WHERE order_id = 'O00000001'")
```

The tool returns a blocked `operation_guard` response and never calls the executor.

Setup details: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#mcp-tools).

## Tech Stack

| Layer | Stack |
|-------|-------|
| Frontend | Vue 3, TypeScript, Vite, ECharts |
| API | FastAPI, SSE |
| Agent | Node-based workflow, bounded repair loop |
| LLM | Mock provider, DeepSeek provider |
| SQL safety | SQLGlot AST parsing and normalization |
| Metadata | SQLite, SQLAlchemy |
| OLAP engines | DuckDB, ClickHouse |
| Vector retrieval | Qdrant, sentence-transformers MiniLM embeddings, CPU-only PyTorch |
| Eval | Pytest, smoke eval runner, result-equivalence checks |
| Tooling | MCP stdio servers |
| Packaging | Docker Compose |

## Beyond V1

Near-term extensions:

- Query audit log and run history.
- Data quality and table profiling tools.
- More MCP tools for governed metadata operations.
- More warehouse connectors.
- Tenant-aware analysis spaces and permission-aware Guard scopes.
- Better chart grammar and lightweight dashboard composition.
- Production observability around latency, token usage, repair rate, and Guard blocks.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Project Brief](docs/PROJECT_BRIEF.md)
- [Demo Guide](docs/DEMO_GUIDE.md)
- [Development Guide](docs/DEVELOPMENT.md)
- [Agent Workflow](docs/AGENT_WORKFLOW.md)
- [SQL Guard Design](docs/SQL_GUARD_DESIGN.md)
- [Metadata Semantic Layer](docs/METADATA_SEMANTIC_LAYER.md)
- [Evaluation Design](docs/EVALUATION_DESIGN.md)
- [Roadmap](docs/ROADMAP.md)
