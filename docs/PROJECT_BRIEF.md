# Project Brief

## Summary

Industrial NL2SQL Data Agent Platform is an OLAP-focused data agent for turning business questions into safe, executable SQL over DuckDB and ClickHouse. It is designed around metadata governance, deterministic SQL safety, explainable workflow steps, automatic charting, and repeatable evaluation.

The current repository is the **V1 baseline**: a complete, runnable, and evaluated foundation. It is intentionally not positioned as the final platform; the design leaves room for governance, observability, connector, and dashboard extensions.

The core idea is simple: the LLM proposes SQL, but the system decides what context it receives, whether the SQL is safe, how failures are repaired, and how behavior is measured.

## Problem

Enterprise NL2SQL is hard for reasons that go beyond SQL generation:

- The model needs trusted table, column, metric, alias, and join context.
- Generated SQL can be syntactically valid but unsafe or outside the allowed analysis scope.
- Warehouse dialects differ, especially for date, window, and OLAP functions.
- Errors need attribution: retrieval miss, SQL generation error, guard block, execution failure, chart mismatch, or result mismatch.
- A useful agent needs to handle follow-up questions, not just isolated one-shot prompts.

## Approach

The platform separates model behavior from system guarantees:

```text
Question
  -> metadata retrieval
  -> focused schema context
  -> SQL generation
  -> SQL Guard
  -> read-only execution
  -> repair / explain / chart
  -> eval report
```

Key choices:

- Persist metadata and semantic assets in SQLite instead of scanning or dumping full schema per request.
- Use focused retrieval to build compact, relevant prompt context.
- Treat SQL Guard as an execution boundary, not a prompt instruction.
- Run repaired SQL through the same Guard again.
- Keep HTTP and MCP query paths on the same safety and execution pipeline.
- Use smoke evals and real-model evals to keep changes measurable.

## Current Capabilities

- DuckDB and ClickHouse datasources.
- DB-backed semantic layer: physical schema, business descriptions, metrics, aliases, verified queries, relationships, and analysis spaces.
- Rule retrieval plus optional Qdrant vector retrieval and value recall.
- Multi-turn follow-up handling for dimension, filter, metric, and time-window changes.
- SQLGlot-based SQL Guard with SELECT-only enforcement, scope checks, dangerous function blocking, fanout detection, and automatic LIMIT.
- Bounded SQL repair loop.
- ClickHouse EXPLAIN-derived performance hints.
- Result summaries and chart recommendations.
- Admin UI for semantic metadata.
- MCP read-only tools for schema, guarded query, EXPLAIN, and metric search.
- Mock and DeepSeek eval reports.

## V1 Validation Snapshot

```text
76/76 smoke cases passed.
DuckDB (本地) - 51 cases: 51/51 passed.
ClickHouse (OLAP) - 25 cases: 25/25 passed.
```

Coverage includes retrieval expectations, SQL Guard safety cases, repair paths, chart recommendations, OLAP intent patterns, ClickHouse-specific functions, EXPLAIN hints, and multi-turn follow-up persistence.

## Design Strengths

### Deterministic Safety Boundary

The model never executes SQL directly. `guard_sql` parses and validates SQL before execution, and `execute_guarded_sql` refuses any `allowed=false` result. This keeps destructive SQL, external reads, unauthorized tables/columns, and risky fanout aggregations out of the execution path.

### Governed Context

The semantic layer controls what the model sees. Analysis spaces define the allowed tables and metrics, while retrieval selects the relevant subset. This reduces prompt size and makes the Guard scope match the business surface.

### Observable Workflow

The frontend receives each workflow step over SSE: datasource selection, intent check, retrieval, context build, SQL generation, Guard, repair, execution, performance explanation, answer generation, and chart recommendation.

### Multi-Turn Continuity

Follow-up turns preserve the prior query structure unless the user explicitly changes a dimension, filter, metric, or time window. This supports realistic analytical exploration such as narrowing to a region, switching from sales to order count, and expanding the time window.

### Evaluation as a Product Feature

The eval runner is not an afterthought. It records generated SQL, normalized SQL, retrieval hits, Guard stages, repair history, result shape, chart type, plan hints, and error categories. This makes regressions visible and debuggable.

## Beyond V1

- Query audit log and run history.
- Data profiling and data quality tools.
- More governed MCP tools.
- Additional warehouse connectors.
- Tenant-aware analysis spaces and permission-aware Guard scopes.
- Richer chart grammar and dashboard composition.
- Production observability for latency, token usage, repair rate, and Guard blocks.
