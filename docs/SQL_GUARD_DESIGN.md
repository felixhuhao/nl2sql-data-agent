# SQL Guard Design

## 目标

SQL Guard 的目标是回答一个工业 NL2SQL 系统最核心的问题：**这条 SQL 敢不敢执行**。

大模型生成 SQL 只是候选结果，不能成为安全边界。Guard 必须是确定性代码，必须在所有执行入口复用，包括 HTTP Agent workflow、metadata verified query 校验和 MCP `query_readonly` 工具。

## 执行不变量

- 所有 SQL 执行前都经过 `guard_sql(sql, scope, datasource_name)`。
- `execute_guarded_sql` 只接受 `GuardResult`；`allowed=False` 时直接抛错，不执行。
- Guard scope 来自 active Analysis Space，不由模型决定。
- 修复后的 SQL 重新走完整 Guard，不跳过任一阶段。
- MCP 工具不持有独立 DB 凭据，不绕过 Guard 直连数据库。

核心文件：

- `backend/app/sql_guard/guard.py`
- `backend/app/sql_guard/scope.py`
- `backend/app/sql_guard/models.py`
- `backend/app/execution/runner.py`

## Guard Pipeline

```text
raw SQL
  -> pre-parse dangerous command checks
  -> SQLGlot parse with datasource dialect
  -> single statement check
  -> operation guard
  -> function guard
  -> scope guard
  -> fanout guard
  -> cost guard
  -> normalized SQL
```

### Pre-parse checks

某些 ClickHouse 语法或危险命令不一定能被 SQLGlot 完整解析，例如 `SYSTEM`、`KILL`、`INSERT INTO FUNCTION url(...)`。这些在 parse 前用轻量规则直接拦截。

### Syntax guard

使用 SQLGlot 按 datasource dialect 解析 SQL。空 SQL、解析失败、多语句都拒绝。方言来自 datasource registry，而不是用户输入。

### Operation guard

只允许单条 `SELECT`。DDL/DML 和数据导入类操作拒绝，包括：

- `INSERT` / `UPDATE` / `DELETE`
- `DROP` / `ALTER` / `TRUNCATE` / `CREATE`
- `COPY` / `LOAD` / `INSTALL`
- ClickHouse `SYSTEM` / `KILL` / `RENAME` / `EXCHANGE`

### Function guard

拦截外部读取、远程访问和高风险函数：

- DuckDB：`read_csv`、`read_json`、`read_parquet`
- ClickHouse：`s3`、`url`、`hdfs`、`remote`、`remoteSecure`

这保证模型不能通过 SELECT 包一层外部读取绕过只读数据库。

### Scope guard

`build_default_guard_scope(datasource_name)` 从 SQLite metadata 读取 active Analysis Space：

```text
Analysis Space
  -> allowed_tables
  -> MetaTable / MetaColumn
  -> GuardScope(allowed_tables, table_columns)
```

Guard 检查访问的物理表必须在 allowed tables 中，访问的物理字段必须在对应 table_columns 中。CTE 和子查询别名单独处理，避免把派生列误判为物理字段。

### Fanout guard

订单级金额 `fact_orders.payment_amount` 在 join 到 `fact_order_items` 后聚合会重复计算。Guard 对这类已知 fanout 风险做确定性拦截，引导 repair 使用 `fact_order_items.item_amount`。这个规则体现了 Guard 不只是安全，也承载最小业务正确性保护。

### Cost guard

无 LIMIT 的非标量查询会自动追加最大行数限制；超大 LIMIT 会截断。标量聚合查询不强制 LIMIT，例如 `SELECT SUM(...)`。

## 方言感知

Guard 根据 datasource 获取 dialect：

- DuckDB：标准本地 OLAP 演示。
- ClickHouse：额外拦截 ClickHouse 管理命令和外部表函数，normalized SQL 输出 ClickHouse 方言。

方言不由用户输入决定，而由 `DataSourceManager` 中注册的数据源决定。

## Repair 关系

Guard 输出结构化结果：

```text
allowed
stage
reason
normalized_sql
warnings
```

`repair.py` 根据 stage 判断是否可修复：

- 可修复：`syntax_guard`、`scope_guard`、`function_guard`、`fanout_guard`、`cost_guard`
- 不可修复：`operation_guard`

不可修复代表用户意图或 SQL 操作本身危险，例如 DELETE。系统不会让模型“帮用户改成 SELECT”，而是直接拒绝。

## 测试策略

测试覆盖三层：

- Unit tests：SQL parse、operation、function、scope、fanout、cost、ClickHouse 方言。
- Workflow tests：生成 SQL、Guard 拒绝、repair 后重新 Guard。
- Smoke eval：危险请求被预期 stage 拦截，且与 HTTP/MCP 行为一致。

## 取舍

- 不做完整 SQL 成本估算器，只做自动 LIMIT 和少量高风险规则。
- 不做权限系统，当前用 Analysis Space 作为可信资产边界。
- 不尝试证明 SQL 语义绝对正确，复杂业务正确性由 semantic layer 和 eval 承担。

## 技术说明

> 我把 SQL Guard 设计成执行路径的承重墙，而不是 prompt 的附属品。模型可以犯错，但执行器只认 GuardResult；HTTP 和 MCP 都走同一条 Guard + readonly executor 路径。所以这个系统的安全性不依赖模型是否听话。
