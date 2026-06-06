# Phase 7：MCP 工具化 — 设计规格

> 日期：2026-06-05
> 状态：设计完成，待实现
> 前置：Phase 6（ClickHouse 接入与多数据源方言适配）、Phase 6.5（OLAP 分析工具与性能治理）
> 建议用时：3-4 天（lean core；profile/quality backlog 另计）
> 设计基线（brainstorming 已确认）：**in-process 复用 + lean-core 5 工具 + stdio**，real & runnable 而非脚手架。

## 1. 定位与目标

把已经建好的核心能力（SQL Guard、只读执行、metadata service、retrieval、DuckDB/ClickHouse EXPLAIN、性能提示）以 MCP server 方式暴露，体现现代 Agent 工具生态能力。

Phase 7 的重点**不是新增数据能力**，而是把后端服务层封装成一组安全、只读、结构化的 MCP 工具，让外部 Agent（如 Claude Desktop / `mcp` CLI）可以复用本项目的语义层和安全边界。

定位为可展示、可运行的工具化交付，但要求 **真实可运行**（带冒烟测试与真实 client 演示），而不是只能截图的脚手架。可行性来自一个关键事实：**每个工具都只是对既有后端函数的薄封装**，因此 real 相比 demo-only 的边际成本很小。

最具说服力的演示瞬间：把真实 MCP client 接到数仓，用户提出“删除 2024 年订单数据”，外部 Agent 调用 `query_readonly` 时传入 `DELETE FROM fact_orders ...`，并被与 HTTP 链路同一套 SQL Guard 当场拦截。

## 2. 范围

### 做（lean core，5 工具）

| Server | 工具 | 复用的后端能力 |
|--------|------|---------------|
| db_tools | `list_tables` | `metadata.service.list_tables` |
| db_tools | `get_table_schema` | `service.list_columns` + `list_relationships` + 表级 row_count/描述 |
| db_tools | `query_readonly` | `build_default_guard_scope` + `sql_guard.guard_sql` + `execution.execute_guarded_sql` |
| olap_tools | `explain_query` | `guard_sql`(必须通过) + `connector.explain` + `performance.parse_plan_hints` |
| olap_tools | `metric_catalog_search` | `metadata.retrieval.retrieve_metadata_assets` |

### Deferred（移入 backlog，需新建后端模块）

| 工具 | 原因 | Revisit 条件 |
|------|------|-------------|
| `profile_table` | 需新增 `metadata/profiling.py`（row_count、null_rate、distinct_count、数值列 min/max、sample values） | lean core 落地并演示后 |
| `data_quality_check` | 需新增 `metadata/quality.py`（非空率、join key 唯一性、row_count>0、时间列新鲜度），兑现 Phase 6.5 backlog 的"数据质量检查" | profiling 就绪后接续 |

> 落地时核心逻辑放后端模块（`profiling.py` / `quality.py`）而非 MCP server 文件，以便单测与 HTTP 复用，且统计/校验 SQL 同样经过 Guard。

### 不做

- 不做 HTTP-backed MCP 变体（用 in-process 复用）。
- 不暴露任何写/CRUD MCP 工具（metric / alias / verified query / analysis space 维护仍只走 HTTP Admin）。
- 不新增集中式审计模块（沿用既有日志；审计接口后移到 Phase 8）。
- 不改现有 FastAPI / Agent 链路的任何行为（MCP 是新增旁路消费者，零回归）。

## 3. 架构与复用契约

### 3.1 调用链路

```text
MCP client (Claude Desktop / mcp CLI)
  → stdio (JSON-RPC)
  → mcp_servers/{db_tools,olap_tools}  (FastMCP server)
  → import backend.app.*               (in-process，无网络跳)
  → guard_sql / execute_guarded_sql / metadata.service / retrieval / connector.explain
  → 结构化 dict → client
```

### 3.2 为什么用 in-process import

| 维度 | in-process（采用） | HTTP-backed（不采用） |
|------|-------------------|----------------------|
| Guard/scope 继承 | 直接调用，天然继承 | 需经 API，但要新建 profile/explain 端点 |
| 执行路径数量 | 1 条（与 HTTP API 同一套 guard_sql） | 1 条，但多一层网络 |
| 运行依赖 | 无需后端常驻 | 需要 FastAPI 在跑 |
| 安全故事 | "MCP 不可能绕过 Guard"最干净 | 同样安全但链路更长 |
| 取舍 | MCP 进程需与后端共享文件系统（同一 SQLite/DuckDB） | 完全解耦 |

### 3.3 关键原则

- **复用后端，不新建链路。** 直接 `import backend.app.*` 调现有 service 函数，绝不另起 DB 连接或重写 SQL 执行。
- **`query_readonly` 必须走 SQL Guard。** 任何经 MCP 执行的 SQL 都先经过 `guard_sql(sql, build_default_guard_scope(ds), ds)`，再交给 `execute_guarded_sql`。Guard 拒绝时返回 `allowed=false` + `stage` + `reason`，**绝不执行**。
- **MCP 不持有独立 DB 凭据。** 统一通过 `get_datasource_manager()` 拿只读连接，沿用 `clickhouse_readonly`、超时和行数限制。
- **结构化返回 + 统一错误信封。** 所有工具返回可序列化 dict；不可用数据源、表不存在、Guard 拒绝都返回结构化错误，而非抛栈。
- **datasource 作为统一参数。** 每个工具都接受 `datasource`，默认 `settings.default_datasource`；ClickHouse 未启用时相关工具优雅降级。

### 3.4 统一错误信封

```python
# 成功
{ "ok": True, "data": { ... } }

# 失败（datasource 不存在 / 表不存在 / Guard 拒绝等）
{ "ok": False, "error": { "kind": "datasource_unavailable" | "not_found" | "guard_rejected" | "internal",
                          "message": "<人类可读>", "detail": { ... } } }
```

> `query_readonly` 的 Guard 拒绝是**预期业务结果**而非异常，单独走 `data.allowed=false` 返回（见 4.3），不包成 `error`。

## 4. 工具详细设计

### 4.1 list_tables

**职责：** 列出当前 analysis space 内可问的表。

**输入：** `datasource: str = settings.default_datasource`

**实现：**
```python
from backend.app.metadata.service import list_tables as svc_list_tables
def list_tables(datasource: str = DEFAULT) -> dict:
    return ok(svc_list_tables(datasource_name=datasource))   # list[dict]: 表名/描述/row_count
```

### 4.2 get_table_schema

**职责：** 返回单表的字段列表 + 关系（join key）+ 表级元信息。

**输入：** `table_name: str`, `datasource: str = DEFAULT`

**实现：** 组合 `service.list_columns(table_name, ds)` 与 `service.list_relationships(ds)`（筛出涉及该表的关系）；表不存在 → `error.kind="not_found"`。

```python
{
  "ok": True,
  "data": {
    "table": "fact_orders",
    "columns": [ { "name": "...", "type": "...", "description": "...", "aliases": [...] } ],
    "relationships": [ { "from": "...", "to": "...", "type": "...", "confidence": "..." } ]
  }
}
```

### 4.3 query_readonly（核心安全工具）

**职责：** 执行用户/Agent 提供的 SQL，**强制经过 SQL Guard**，只读返回结果。

**输入：** `sql: str`, `datasource: str = DEFAULT`

**实现（不可妥协的顺序）：**
```python
from backend.app.sql_guard.scope import build_default_guard_scope
from backend.app.sql_guard.guard import guard_sql
from backend.app.execution.runner import execute_guarded_sql

def query_readonly(sql: str, datasource: str = DEFAULT) -> dict:
    scope = build_default_guard_scope(datasource)
    result = guard_sql(sql, scope, datasource)          # GuardResult
    if not result.allowed:
        return ok({                                      # 业务结果，非异常
            "allowed": False,
            "stage": result.stage,                       # e.g. operation_guard / scope_guard
            "reason": result.reason,
            "warnings": result.warnings,
        })
    qr = execute_guarded_sql(result, datasource)         # QueryResult
    return ok({
        "allowed": True,
        "normalized_sql": result.normalized_sql,
        "warnings": result.warnings,
        "columns": qr.columns,
        "rows": qr.rows,
        "row_count": qr.row_count,
        "elapsed_ms": qr.elapsed_ms,
    })
```

**安全断言（必须有测试覆盖）：**
- 非 SELECT / DDL / DML（如 `DELETE FROM fact_orders WHERE order_date >= '2024-01-01'`、`DROP`、`INSERT`...）→ `allowed=false`，stage=`operation_guard`，`execute_guarded_sql` 永不被调用。
- 越权表 / 越权字段（不在 analysis space 白名单）→ `allowed=false`，stage=`scope_guard`。
- 合法 SELECT → 自动补 LIMIT，返回 normalized_sql 与结果。

### 4.4 explain_query

**职责：** 对一条 SQL 跑 EXPLAIN，返回查询计划 + 可读性能提示。

**输入：** `sql: str`, `datasource: str = DEFAULT`

**实现：**
```python
from backend.app.connectors.registry import get_datasource_manager
from backend.app.agent.performance import parse_plan_hints

def explain_query(sql: str, datasource: str = DEFAULT) -> dict:
    scope = build_default_guard_scope(datasource)
    result = guard_sql(sql, scope, datasource)
    if not result.allowed:
        return ok({"allowed": False, "stage": result.stage, "reason": result.reason})
    connector = get_datasource_manager().get(datasource)
    explain_result = connector.explain(result.normalized_sql)   # DuckDB 与 ClickHouse 均已实现
    hints = parse_plan_hints(explain_result, sql=result.normalized_sql) if explain_result else []
    return ok({
        "allowed": True,
        "normalized_sql": result.normalized_sql,
        "explain": explain_result,        # dict | None
        "plan_hints": hints,              # list[str]，ClickHouse 更丰富，DuckDB 最小
    })
```

> **方言差异：** `connector.explain` 在 DuckDB 和 ClickHouse 均已实现（与 Phase 6.5 文档里"DuckDB 返回 None"的早期设想不同，代码已演进）。`parse_plan_hints` 的分区/排序键提示偏 ClickHouse；DuckDB 上提示最小或仅返回 plan，属预期降级。

### 4.5 metric_catalog_search

**职责：** 用自然语言/关键词检索语义层资产（指标为主，附带命中的表和 verified query）。

**输入：** `query: str`, `datasource: str = DEFAULT`

**实现：**
```python
from backend.app.metadata.retrieval import retrieve_metadata_assets

def metric_catalog_search(query: str, datasource: str = DEFAULT) -> dict:
    assets = retrieve_metadata_assets(query, datasource_name=datasource)
    return ok({
        "query": query,
        "metrics": assets["metrics"],                 # 带分数/原因，已按 analysis space 白名单过滤
        "tables": assets["tables"],
        "verified_queries": assets["verified_queries"],
        "fallback_used": assets["fallback_used"],
    })
```

> `retrieve_metadata_assets` 已内置规则 + 向量混合检索（受 `settings.vector_enabled` 控制），并已尊重 analysis space 白名单，无需在 MCP 层重复实现。

## 5. MCP Server 脚手架

### 5.1 包结构

```text
mcp_servers/
  __init__.py
  _common.py            # settings 加载、确保 metadata schema、datasource 解析、
                        # 错误信封 helper(ok/err)、JSON 安全序列化
  db_tools/
    __init__.py
    __main__.py         # python -m mcp_servers.db_tools  (stdio FastMCP server)
    server.py           # 3 个工具定义 → 调后端函数
  olap_tools/
    __init__.py
    __main__.py         # python -m mcp_servers.olap_tools
    server.py           # 2 个工具定义
scripts/run_mcp_smoke.py
```

### 5.2 SDK 与传输

- 采用官方 **Python MCP SDK（FastMCP）**，stdio 传输。
- 作为 `backend/pyproject.toml` 的 **optional extra**（如 `[project.optional-dependencies].mcp = ["mcp>=1.0.0"]`），不污染核心运行时依赖。

### 5.3 _common.py 职责

- `resolve_datasource(name) -> str`：缺省回落 `settings.default_datasource`；不存在 → 抛可被工具捕获的 `DatasourceUnavailable`。
- `ok(data)` / `err(kind, message, detail=None)`：统一信封。
- `ensure_ready()`：进程启动时确保 metadata schema 存在（复用 `create_metadata_schema`）。
- JSON 安全：rows 中 `Decimal` / `date` / `datetime` 转可序列化形式。

### 5.4 server.py 形态（示意）

```python
from mcp.server.fastmcp import FastMCP
from mcp_servers._common import ok, err, resolve_datasource

mcp = FastMCP("nl2sql-db-tools")

@mcp.tool()
def query_readonly(sql: str, datasource: str | None = None) -> dict:
    ds = resolve_datasource(datasource)
    ...  # 见 4.3
```

## 6. 安全与 Guard 复用（不可妥协）

1. 任何 SQL 执行前必经 `guard_sql(sql, build_default_guard_scope(ds), ds)`。
2. `guard_sql` 返回 `allowed=False` 时，`execute_guarded_sql` / `connector.execute` **绝不被调用**。
3. MCP 进程不构造任何独立 DB 连接，只用 `get_datasource_manager()`。
4. 工具不接受任意 `connection string`、不暴露 `read_csv/read_parquet/s3/url` 等被 Guard 封禁的函数（这些已由 Guard 在 SQL 层拦截）。
5. 无写工具，无 schema 变更工具。

## 7. Iteration 拆分

```text
I7.1 MCP 脚手架 + 复用契约
  → 新增 mcp optional extra（FastMCP）
  → mcp_servers/ 包 + _common.py（datasource 解析 / ok-err 信封 / ensure_ready / JSON 安全）
  → 两个 stdio 入口：python -m mcp_servers.db_tools / olap_tools
  → 冒烟：MCP client 能 list_tools

I7.2 db_tools server
  → list_tables / get_table_schema / query_readonly
  → query_readonly 严格 build_default_guard_scope + guard_sql；拒绝即结构化返回，不执行
  → 单测：合法 SELECT、越权表、越权字段、DELETE/DDL 拦截、datasource fallback、表不存在

I7.3 olap_tools server
  → explain_query：Guard 必须通过；DuckDB + ClickHouse 均调 connector.explain；
     ClickHouse 走 parse_plan_hints 输出更丰富提示，DuckDB 最小降级
  → metric_catalog_search：复用 retrieve_metadata_assets
  → 单测：指标命中、EXPLAIN 解析、Guard 拒绝路径、ClickHouse 未启用时降级

I7.4 文档 + 冒烟
  → README 新增 MCP 章节：5 工具清单、运行方式、示例 client 配置 JSON
  → scripts/run_mcp_smoke.py：进程内拉起两 server，断言每工具结构化返回，
     并验证 query_readonly 对 `DELETE FROM fact_orders WHERE order_date >= '2024-01-01'`
     返回 allowed=false、stage=operation_guard 且不执行
  → 记录 backlog（profile_table / data_quality_check、DuckDB explain 提示最小、无写工具）
```

拆分依据：先把"脚手架 + 复用契约"定死（确保所有工具共享同一条 Guard/连接器路径），再按"纯读 schema → 读+执行 → 解释/检索 → 固化文档与冒烟"推进，避免 MCP 传输与安全边界互相干扰。

## 8. 关键文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `mcp_servers/__init__.py` | 新增 | 包标识 |
| `mcp_servers/_common.py` | 新增 | settings/datasource 解析、错误信封、JSON 安全、ensure_ready |
| `mcp_servers/db_tools/__main__.py` | 新增 | stdio 入口 |
| `mcp_servers/db_tools/server.py` | 新增 | list_tables / get_table_schema / query_readonly |
| `mcp_servers/olap_tools/__main__.py` | 新增 | stdio 入口 |
| `mcp_servers/olap_tools/server.py` | 新增 | explain_query / metric_catalog_search |
| `scripts/run_mcp_smoke.py` | 新增 | 进程内冒烟，含 Guard 拦截断言 |
| `backend/pyproject.toml` | 修改 | 新增 `mcp` optional extra |
| `backend/tests/test_mcp_db_tools.py` | 新增 | db_tools 单测（含 Guard 拦截） |
| `backend/tests/test_mcp_olap_tools.py` | 新增 | olap_tools 单测 |
| `README.md` | 修改 | MCP 工具清单、运行方式、client 配置示例 |

> 后端 `backend/app/**` **零修改**——这是本阶段的关键约束（MCP 只读复用，不改既有行为）。

## 9. 验收标准

1. **schema 工具** — MCP client 调 `list_tables` / `get_table_schema` 返回结构化表与字段。
2. **OLAP 工具** — MCP client 调 `explain_query`（返回 plan + plan_hints）/ `metric_catalog_search`（返回带分数的指标）。
3. **Guard 一致性** — `query_readonly` 对非 SELECT、越权表字段、危险操作的拦截行为与 HTTP 链路完全一致（同一套 `guard_sql`）；危险 SQL 返回 `allowed=false` 且 `execute_guarded_sql` 不被调用。
4. **无独立凭据** — MCP 进程统一复用后端连接器管理与只读约束，无独立 DB 连接。
5. **可演示** — 可在真实 MCP client（如 Claude Desktop）注册 `python -m mcp_servers.db_tools` 并完成端到端演示：用户提出“删除 2024 年订单数据”，外部 Agent 传入 `DELETE FROM fact_orders ...` 后被 `operation_guard` 拦截。
6. **文档** — README 说明 5 工具清单和运行方式。
7. **回归** — MCP 工具单测 + `scripts/run_mcp_smoke.py` 全绿；全量 pytest 与既有 smoke eval 无回归（后端未改，应天然通过）。

## 10. 验证方式（端到端）

1. `pip install -e backend[mcp]` 安装 MCP SDK extra。
2. `pytest backend/tests/test_mcp_*.py` —— 关键断言：`query_readonly` 对 DELETE 返回 `allowed=false` 且从不执行。
3. `python scripts/run_mcp_smoke.py` —— 进程内拉起两 server，逐个调用 5 工具，断言结构化输出与危险 SQL 拦截。
4. 手动：MCP client 配置注册 `python -m mcp_servers.db_tools`，确认 `list_tables` / `query_readonly` 响应；跑危险 SQL 确认被 Guard 拦截。
5. 全量 `pytest` + 既有 smoke eval 保持绿。

## 11. Deferred 工具预期设计（backlog）

### 11.1 profile_table

- 新增 `backend/app/metadata/profiling.py::profile_table(table, datasource) -> dict`。
- 用 guarded 确定性 SELECT 计算：`row_count`、各列 `null_count`/`null_rate`、`distinct_count`、数值列 `min`/`max`；sample values 复用 metadata。
- 所有统计 SQL 经 `guard_sql` + `execute_guarded_sql`，与查询同一条安全路径。
- MCP 工具 `profile_table` 仅薄封装该后端函数。

### 11.2 data_quality_check

- 新增 `backend/app/metadata/quality.py::run_quality_checks(table, datasource) -> dict`。
- 最小确定性规则集：非空率阈值、join key 唯一性、`row_count>0`、时间列新鲜度；逐项 `pass/warn/fail`。
- 兑现 Phase 6.5 backlog 的"数据质量检查"。
- Revisit 条件：lean core 落地并演示后接续；漏斗/留存类质量校验仍后移。
