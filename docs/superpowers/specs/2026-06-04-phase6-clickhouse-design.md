# Phase 6: ClickHouse 接入与多数据源架构设计

> 日期: 2026-06-04
> 状态: 设计完成，待实现
> 前置: Phase 1-5 已完成
> 建议用时: 5-7 天

## 1. 目标

从 DuckDB 本地演示升级到支持工业级 OLAP 数据源（ClickHouse），体现多数据源方言适配能力。Phase 6 只做最小 ClickHouse 接入，OLAP 分析工具（同环比、漏斗、留存等）拆到 Phase 6.5。

### 交付范围

- Connector 抽象层（DataSourceConnector Protocol）
- ClickHouse Connector 实现 + Docker Compose 环境
- DuckDB Connector 适配（包装现有逻辑）
- SQL Guard 动态方言 + ClickHouse 安全规则扩展
- ClickHouse 元数据同步（含 OLAP 特有字段）
- Agent 方言适配（Prompt 分方言 + Repair 适配）
- 前端数据源切换器 + 展示增强
- Eval 扩展（方言适配 + ClickHouse case 15+）
- ROADMAP 更新（新增 Phase 6.5）

### 不做

- OLAP 分析工具（同环比、漏斗、留存、TopN）→ Phase 6.5
- EXPLAIN 查询计划展示 → Phase 6.5
- 性能提示（分区命中、排序键利用）→ Phase 6.5
- SQLAlchemy 引入 → 不引入，用原生驱动 + sqlglot + 自定义 Connector
- 连接池、查询队列、自动重连 → 过度设计

## 2. 架构方案：Connector 抽象层

### 核心接口

```python
# backend/app/connectors/base.py
class DataSourceConnector(Protocol):
    name: str                    # "duckdb_ecommerce" / "clickhouse_ecommerce"
    dialect: str                 # "duckdb" / "clickhouse"
    display_name: str            # "DuckDB (本地)" / "ClickHouse (OLAP)"

    def get_connection(self, read_only: bool = True) -> Any: ...
    def sync_schema(self) -> SchemaSnapshot: ...
    def execute(self, sql: str, timeout: int | None = None) -> RawResult: ...
    def close(self) -> None: ...
```

**ClickHouse 连接级安全设置：** ClickHouseConnector 在 `get_connection()` 时通过连接 settings 注入安全参数：

```python
# ClickHouseConnector.get_connection()
import clickhouse_connect

def get_connection(self, read_only: bool = True) -> clickhouse_connect.Client:
    settings = {}
    if read_only:
        settings["readonly"] = 1                # 连接级只读，DDL/DML 全部拒绝
    settings["max_execution_time"] = self.max_execution_time  # 查询超时
    settings["max_result_rows"] = self.max_result_rows        # 返回行数上限

    return clickhouse_connect.get_client(
        host=self.host,
        port=self.port,
        username=self.user,
        password=self.password,
        database=self.database,
        settings=settings,
    )
```

这确保即使 SQL Guard 漏拦截，数据库层面也有只读 + 超时 + 行数限制三重防线。DuckDB 已有 `read_only=True` 参数，行为一致。

### 数据源管理器

```python
# backend/app/connectors/manager.py
class DataSourceManager:
    _registry: dict[str, DataSourceConnector]
    _default: str

    def register(self, connector: DataSourceConnector) -> None
    def get(self, name: str) -> DataSourceConnector
    def get_default(self) -> DataSourceConnector
    def list_sources(self) -> list[DataSourceInfo]
```

### 注册逻辑

```python
# backend/app/connectors/registry.py
def create_datasource_manager(settings) -> DataSourceManager:
    manager = DataSourceManager()

    # DuckDB 始终注册（零依赖）
    manager.register(DuckDBConnector(settings))

    # ClickHouse 按配置注册（有配置且可连接才注册）
    if settings.clickhouse_enabled:
        try:
            manager.register(ClickHouseConnector(settings))
        except ConnectionError:
            logger.warning("ClickHouse connection failed, skipping registration")

    return manager
```

### 配置扩展

```python
# backend/app/config.py 新增
clickhouse_host: str = "localhost"
clickhouse_port: int = 8123
clickhouse_user: str = "default"
clickhouse_password: str = ""
clickhouse_database: str = "ecommerce"
clickhouse_enabled: bool = False    # 默认关闭，不影响现有行为
clickhouse_readonly: bool = True    # 连接级只读模式
clickhouse_max_execution_time: int = 30   # 查询超时（秒）
clickhouse_max_result_rows: int = 10000   # 最大返回行数（驱动层防线）
default_datasource: str = "duckdb_ecommerce"
```

### 依赖项

- `clickhouse-connect`：官方 ClickHouse Python 客户端（HTTP 接口）
- 不引入 SQLAlchemy——原生驱动通过 Connector 封装差异

## 3. SQL Guard 方言适配与安全规则扩展

### 动态方言机制

```python
# backend/app/sql_guard/guard.py 改动
# Before: DIALECT = "duckdb"
# After: 从 Connector 获取

def check_sql(sql: str, datasource_name: str = "duckdb_ecommerce") -> GuardResult:
    connector = get_datasource_manager().get(datasource_name)
    dialect = connector.dialect  # "duckdb" 或 "clickhouse"
    expressions = sqlglot.parse(sql, read=dialect)
    normalized = expression.sql(dialect=dialect)
```

sqlglot 原生支持 ClickHouse 方言解析和生成，不需要自己处理方言差异。

### 安全规则扩展

#### operation_guard 扩展

```python
# 现有
BLOCKED_OPERATIONS = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
                      "TRUNCATE", "CREATE"}

# 新增（ClickHouse 特有）
CLICKHOUSE_BLOCKED_OPERATIONS = {
    "SYSTEM",      # SYSTEM FLUSH / RELOAD 等
    "KILL",        # KILL QUERY / KILL MUTATION
    "RENAME",      # RENAME TABLE
    "EXCHANGE",    # EXCHANGE TABLES
}

def _get_blocked_operations(dialect: str) -> set[str]:
    if dialect == "clickhouse":
        return BLOCKED_OPERATIONS | CLICKHOUSE_BLOCKED_OPERATIONS
    return BLOCKED_OPERATIONS
```

#### function_guard 扩展

```python
# 现有 DuckDB 危险函数
BLOCKED_FUNCTIONS = {"read_csv", "read_parquet", "read_json"}

# 新增 ClickHouse 危险函数
CLICKHOUSE_BLOCKED_FUNCTIONS = {
    "s3", "url", "hdfs",        # 外部存储读取函数
    "remote", "remoteSecure",   # 远程服务器查询
}

def _get_blocked_functions(dialect: str) -> set[str]:
    # 注意：ClickHouse 不需要拦截 DuckDB 函数（read_csv 等），
    # 因为它们在 ClickHouse 上本来就不存在；反之亦然。
    if dialect == "clickhouse":
        return CLICKHOUSE_BLOCKED_FUNCTIONS
    return BLOCKED_FUNCTIONS
```

#### INSERT INTO FUNCTION 检测

```python
def _check_insert_into_function(expression, dialect: str) -> list[str]:
    """ClickHouse: 拦截 INSERT INTO FUNCTION url(...) 等外部写入"""
    if dialect != "clickhouse":
        return []
    errors = []
    for stmt in walk(expression):
        if isinstance(stmt, exp.Insert) and isinstance(stmt.this, exp.Anonymous):
            errors.append("不允许 INSERT INTO FUNCTION 操作")
    return errors
```

### scope_guard 适配

scope_guard 需要改造成 datasource-aware。`build_default_guard_scope(datasource_name)` 从对应数据源的 Analysis Space 读取表白名单，并用同一 `datasource_name` 过滤 `MetaTable` / `MetaColumn`，避免前端选择 ClickHouse 后仍套用 DuckDB 的 scope。ClickHouse 的 Analysis Space seed 数据也要单独注册。

### cost_guard 适配

LIMIT 逻辑对 ClickHouse 同样适用，不需要改动。sqlglot 会正确处理 ClickHouse 的 LIMIT 语法。

### 测试覆盖

新增测试用例：

| 测试场景 | 方言 | 说明 |
|---------|------|------|
| ClickHouse 合法 SELECT 通过 | clickhouse | 基本正向 |
| `SYSTEM FLUSH LOGS` 被拦截 | clickhouse | operation |
| `KILL QUERY` 被拦截 | clickhouse | operation |
| `RENAME TABLE` 被拦截 | clickhouse | operation |
| `INSERT INTO FUNCTION url(...)` 被拦截 | clickhouse | function |
| `SELECT * FROM s3(...)` 被拦截 | clickhouse | function |
| DuckDB 现有测试全部通过 | duckdb | 回归 |

## 4. ClickHouse 元数据同步与 Schema 探知

### SchemaSnapshot 数据结构

```python
# backend/app/connectors/schema.py
@dataclass
class ColumnMeta:
    name: str
    data_type: str
    nullable: bool
    sample_values: list[str]
    is_partition_key: bool = False      # ClickHouse: PARTITION BY 字段
    is_sorting_key: bool = False        # ClickHouse: ORDER BY 字段
    is_primary_key: bool = False        # ClickHouse: PRIMARY KEY 字段
    low_cardinality: bool = False       # ClickHouse: LowCardinality 标记

@dataclass
class TableMeta:
    name: str
    row_count: int
    columns: list[ColumnMeta]
    engine: str = ""                    # ClickHouse: MergeTree/ReplacingMergeTree 等
    partition_key: str = ""             # ClickHouse: PARTITION BY 表达式
    sorting_key: str = ""               # ClickHouse: ORDER BY 表达式

@dataclass
class SchemaSnapshot:
    tables: list[TableMeta]
    datasource_name: str
    synced_at: datetime
```

### DuckDB 同步（适配现有逻辑）

现有 `metadata/sync.py` 的 `_read_duckdb_columns()` 等函数不动，在 `DuckDBConnector.sync_schema()` 里调用它们，包装成 `SchemaSnapshot` 返回。OLAP 特有字段留空。

### ClickHouse 同步

通过 ClickHouse 系统表获取元数据。注意以下实现细节：

```sql
-- 表列表：使用参数化查询防止 SQL 注入
-- ClickHouse HTTP 接口支持 params
SELECT name, engine, total_rows,
       partition_key, sorting_key, primary_key
FROM system.tables
WHERE database = {database:String}
  AND engine NOT IN ('View', 'MaterializedView', 'SystemView')

-- 字段信息：ClickHouse 的 system.columns 没有 nullable 列
-- nullable 信息需要从 type 字符串解析：Nullable(X) 表示可空
SELECT name, type,
       is_in_partition_key, is_in_sorting_key, is_in_primary_key
FROM system.columns
WHERE database = {database:String}

-- LowCardinality 也从 type 字符串解析：LowCardinality(X)
-- 解析逻辑：
--   nullable = type.startswith("Nullable(")
--   low_cardinality = "LowCardinality(" in type
--   base_type = 去掉 Nullable() 和 LowCardinality() 包装后的类型
```

**采样值查询：**

```sql
-- 使用 backtick quoting 处理保留字和特殊字段名
-- 使用 database-qualified 表名避免歧义
SELECT DISTINCT `{col}` FROM `{database}`.`{table}` LIMIT 10
```

`{col}` 和 `{table}` 在 Python 侧做白名单校验（只允许字母、数字、下划线），防止 SQL 注入。不直接拼接到 SQL 字符串中。

OLAP 特有元数据：engine、partition_key、sorting_key、low_cardinality。存入元数据库，检索和 schema context 构建时可用。

### 元数据存储扩展：datasource 命名空间

当前元数据模型中，只有 `MetaAnalysisSpace` 有 `datasource` 列，其余表（`MetaTable`、`MetaColumn`、`MetaMetric`、`MetaVerifiedQuery`、`MetaRelationship`）都没有。这会导致 ClickHouse 同名表覆盖 DuckDB 元数据。必须给所有元数据表加 `datasource` 列。

```sql
-- 所有元数据表新增 datasource 列（迁移时用现有数据填充 'duckdb_ecommerce'）
ALTER TABLE meta_tables ADD COLUMN datasource TEXT NOT NULL DEFAULT 'duckdb_ecommerce';
ALTER TABLE meta_columns ADD COLUMN datasource TEXT NOT NULL DEFAULT 'duckdb_ecommerce';
ALTER TABLE meta_metrics ADD COLUMN datasource TEXT NOT NULL DEFAULT 'duckdb_ecommerce';
ALTER TABLE meta_verified_queries ADD COLUMN datasource TEXT NOT NULL DEFAULT 'duckdb_ecommerce';
ALTER TABLE meta_relationships ADD COLUMN datasource TEXT NOT NULL DEFAULT 'duckdb_ecommerce';
ALTER TABLE meta_column_aliases ADD COLUMN datasource TEXT NOT NULL DEFAULT 'duckdb_ecommerce';

-- 唯一约束改为 (datasource, ...) 复合键
-- meta_tables: UNIQUE(datasource, table_name) 替代 UNIQUE(table_name)
-- meta_metrics: UNIQUE(datasource, name) 替代 UNIQUE(name)
-- meta_verified_queries: UNIQUE(datasource, query_id) 替代 UNIQUE(query_id)
-- meta_relationships: UNIQUE(datasource, source_table, source_column, target_table, target_column)
-- meta_column_aliases: UNIQUE(datasource, table_name, column_name, alias)
-- meta_columns: 外键 table_id 对应的 meta_tables 行已含 datasource

-- OLAP 特有字段
ALTER TABLE meta_tables ADD COLUMN engine TEXT DEFAULT '';
ALTER TABLE meta_tables ADD COLUMN partition_key TEXT DEFAULT '';
ALTER TABLE meta_tables ADD COLUMN sorting_key TEXT DEFAULT '';

ALTER TABLE meta_columns ADD COLUMN is_partition_key BOOLEAN DEFAULT FALSE;
ALTER TABLE meta_columns ADD COLUMN is_sorting_key BOOLEAN DEFAULT FALSE;
ALTER TABLE meta_columns ADD COLUMN low_cardinality BOOLEAN DEFAULT FALSE;
```

**检索适配：** 所有查询元数据的函数（`retrieve_metadata_assets`、`build_default_guard_scope`、`build_focused_context`）都需要增加 `datasource` 过滤条件。现有函数在无 `datasource` 参数时默认使用 `"duckdb_ecommerce"`，保持向后兼容。

**向量存储适配：** 向量索引的 `asset_id` 需要加 datasource 前缀，避免跨数据源碰撞：

```python
# Before: asset_id = table.table_name
# After:  asset_id = f"{table.datasource}:{table.table_name}"

# Before: asset_id = f"{table_name}.{column.column_name}"
# After:  asset_id = f"{table.datasource}:{table_name}.{column.column_name}"
```

向量检索时增加 `metadata.datasource` 过滤条件，只返回当前数据源的向量结果。

迁移策略：不引入 Alembic，但不能只依赖 `ALTER TABLE`。SQLite 不能用简单 `ALTER` 删除旧 unique 约束，迁移需要二选一：

1. 演示环境可接受重建 metadata DB：备份旧库，删除并重新 seed，现有 DuckDB 元数据写入 `duckdb_ecommerce`。
2. 需要保留现有元数据时：写版本化迁移脚本，按 SQLite table-rebuild 流程创建新表、复制数据、重建索引和复合 unique 约束。

ORM 模型也必须同步更新列定义和 `UniqueConstraint`，否则数据库迁移后应用层仍会按旧模型创建全局唯一约束。

### 关系推断

复用现有 `infer_relationships()` 函数——表结构和字段命名是同一套电商数仓。

### 指标口径策略：共享 + dialect_overrides

```yaml
metric:
  name: sales_amount
  expression: SUM(fact_orders.payment_amount)    # 默认，两者通用

metric:
  name: monthly_sales
  expression: SUM(fact_orders.payment_amount)     # DuckDB 用这个
  dialect_overrides:
    clickhouse: "SUM(toFloat64(payment_amount))"  # ClickHouse 用这个
```

简单指标共享，复杂指标按方言覆盖。当前 3 个指标（sales_amount、order_count、aov）都是基本聚合，不需要 dialect_overrides。

### 语义资产种子数据

ClickHouse 数据源需要独立的语义资产种子：analysis_space（datasource 指向 clickhouse_ecommerce）、verified queries（SQL 使用 ClickHouse 方言）。

## 5. ClickHouse Docker 环境与数据导入

### Docker Compose

```yaml
# docker/docker-compose.yml
services:
  clickhouse:
    image: clickhouse/clickhouse-server:24.8-alpine
    ports:
      - "8123:8123"   # HTTP 接口
      - "9000:9000"   # Native 接口
    volumes:
      - ./clickhouse/init.sql:/docker-entrypoint-initdb.d/init.sql
    environment:
      CLICKHOUSE_USER: default
      CLICKHOUSE_PASSWORD: ""
      CLICKHOUSE_DB: ecommerce
    healthcheck:
      test: ["CMD", "clickhouse-client", "--query", "SELECT 1"]
      interval: 5s
      timeout: 3s
      retries: 10
```

### 表引擎设计

**关键约束：ClickHouse 表的字段名必须与 DuckDB 电商数仓完全一致**，确保现有语义层（metrics、aliases、relationships、verified queries、向量索引）无需修改即可复用。现有字段命名使用 `_key` 后缀（`date_key`、`region_key`、`channel_key`、`user_key`、`product_key`）。

```sql
-- fact_orders: 字段名与 DuckDB 电商数仓完全一致
CREATE TABLE ecommerce.fact_orders (
    order_id       UInt64,
    total_amount   Decimal(12,2),
    discount_amount Decimal(12,2),
    payment_amount Decimal(12,2),
    order_status   LowCardinality(String),
    user_key       UInt64,
    region_key     UInt32,
    channel_key    UInt32,
    date_key       UInt32,
    created_at     DateTime
)
ENGINE = MergeTree()
PARTITION BY intDiv(date_key, 100)         -- date_key 为 YYYYMMDD 整数，按 YYYYMM 分区
ORDER BY (date_key, region_key, channel_key)
PRIMARY KEY (date_key, region_key, channel_key);

-- fact_order_items
CREATE TABLE ecommerce.fact_order_items (
    item_id     UInt64,
    order_id    UInt64,
    product_key UInt32,
    quantity    UInt32,
    unit_price  Decimal(12,2),
    item_amount Decimal(12,2),
    date_key    UInt32
)
ENGINE = MergeTree()
PARTITION BY intDiv(date_key, 100)
ORDER BY (date_key, product_key)
PRIMARY KEY (date_key, product_key);

-- 维表用简单 MergeTree，按主键排序
CREATE TABLE ecommerce.dim_date (
    date_key    UInt32,
    date_value  Date,
    year        UInt16,
    quarter     UInt8,
    month       UInt8,
    week        UInt8,
    day_of_week UInt8
)
ENGINE = MergeTree()
ORDER BY date_key;

CREATE TABLE ecommerce.dim_regions (
    region_key   UInt32,
    region_group LowCardinality(String),
    province     LowCardinality(String),
    city         LowCardinality(String)
)
ENGINE = MergeTree()
ORDER BY region_key;

CREATE TABLE ecommerce.dim_channels (
    channel_key  UInt32,
    channel_name LowCardinality(String),
    channel_type LowCardinality(String)
)
ENGINE = MergeTree()
ORDER BY channel_key;

CREATE TABLE ecommerce.dim_products (
    product_key   UInt32,
    product_id    UInt64,
    category      LowCardinality(String),
    sub_category  LowCardinality(String),
    brand         LowCardinality(String),
    price         Decimal(12,2)
)
ENGINE = MergeTree()
ORDER BY product_key;

CREATE TABLE ecommerce.dim_users (
    user_key      UInt64,
    user_id       UInt64,
    name          String,
    gender        LowCardinality(String),
    age_group     LowCardinality(String),
    register_date Date,
    city          LowCardinality(String)
)
ENGINE = MergeTree()
ORDER BY user_key;
```

设计要点：
- 所有字段名与 DuckDB 电商数仓完全一致，共享语义层零修改
- `date_key` 使用现有 `YYYYMMDD` 整数键，`PARTITION BY intDiv(date_key, 100)` 直接得到 `YYYYMM` 月分区，避免 `toDate(UInt32)` 误解析
- `ORDER BY` 按常见查询维度排列
- `LowCardinality` 用于低基数字段（订单状态、品类、渠道类型等）
- 维表用简单 MergeTree，按主键排序

### 数据导入

复用现有 `scripts/generate_ecommerce_data.py` 作为唯一数据源，ClickHouse 导入分三步：

1. `python scripts/generate_ecommerce_data.py` 生成 DuckDB 标准电商数据集
2. 新增 `python scripts/export_ecommerce_csv.py --output-dir data/clickhouse_csv`，从 DuckDB 导出同名表 CSV
3. 新增 `python scripts/seed_clickhouse.py --input-dir data/clickhouse_csv`，建表 + 导入 + 验证行数

### 启动集成

- `clickhouse_enabled=True` 且连接成功 → 注册 Connector
- `clickhouse_enabled=True` 但连接失败 → 启动警告，只注册 DuckDB
- `clickhouse_enabled=False` → 不注册

## 6. Agent 方言适配与 SQL 生成增强

### 三层方言引导

```text
Layer 1: Schema Context（告诉 LLM 当前数据源类型和约束）
Layer 2: SQL Generation Prompt（方言特定的生成指令）
Layer 3: SQL Guard + Repair（方言感知的校验和修复）
```

### Layer 1: Schema Context 增强

在 schema_context 头部追加数据源信息段：

```python
# 根据 connector.dialect 选择对应的提示模板
DIALECT_CONTEXT_HINTS = {
    "clickhouse": """
## 数据源信息
- 数据源: ClickHouse (OLAP)
- 方言: ClickHouse SQL
- 注意事项:
  - 使用 ClickHouse 日期函数: toStartOfDay(), toStartOfMonth(), toYYYYMM()
  - 类型转换使用 toFloat64(), toString(), toInt32() 等函数
  - 条件聚合可使用 countIf(), sumIf() 等函数
  - LIMIT BY 可用于去重限流
  - 如果用户问题未指定时间范围，不要自行添加时间过滤条件，保持用户意图
""",
    "duckdb": "...",
}
```

**关于时间过滤的设计决策：** 不在 prompt 中强制"大表必须带时间过滤"。原因是"华东销售额"这类无时间限定的问题应当返回全量结果，如果 prompt 隐式要求 LLM 补时间条件，会静默改变查询语义。时间过滤的正确处理方式：

1. **LLM 生成 SQL 时尊重用户意图**——用户没说时间就不加
2. **如果后续需要性能治理**——在 Guard 层增加"大表缺时间过滤"的 warning（非阻断），或返回提示让用户缩小范围。这属于 Phase 6.5 范围

### Layer 2: SQL Generation Prompt 分方言

```python
DIALECT_INSTRUCTIONS = {
    "duckdb": "生成 DuckDB SQL。约束：标准 SQL 语法，日期函数 DATE_TRUNC/DATE_DIFF，类型转换 col::TYPE",
    "clickhouse": "生成 ClickHouse SQL。约束：使用 ClickHouse 函数 toStartOfMonth()/dateDiff()/toFloat64()，条件聚合 countIf()/sumIf()。不要自行添加用户未提到的时间过滤条件",
}
```

### Layer 3: Repair 方言适配

在 repair prompt 中加入方言信息：

```python
f"当前数据源方言: {dialect}，请生成符合该方言语法的 SQL。"
```

不需要改 repair 引擎本身——repair 把错误信息 + schema context + 方言提示一起喂给 LLM。

### Agent State 扩展

```python
class AgentState(TypedDict):
    ...
    datasource_name: str              # 当前查询使用的数据源
    datasource_dialect: str           # "duckdb" / "clickhouse"
```

### datasource 参数完整贯穿清单

`datasource_name` 从 API 请求进入后，必须贯穿以下所有环节。任何一环遗漏都会导致前端选择 ClickHouse 后仍用 DuckDB 执行或校验：

```text
API 请求 (datasource 参数)
  → run_query_workflow(datasource_name)
    → retrieve_context_node
        → retrieve_metadata_assets(question, datasource_name=datasource_name)  # 按数据源检索
    → build_context_node
        → build_focused_context(retrieval_result, datasource_name=datasource_name)  # 按数据源构建
    → generate_sql_node
        → prompt 选择方言 (duckdb/clickhouse)
    → sql_guard_node
        → guard_sql(sql, datasource_name=datasource_name)  # 动态方言 + 对应 scope
        → build_default_guard_scope(datasource_name)  # 按数据源的 Analysis Space
    → execute_node
        → execute_guarded_sql(guard_result, datasource_name=datasource_name)  # 对应 Connector
    → repair_sql_node
        → repair_sql(sql, error, datasource_name=datasource_name)  # 方言感知修复
```

**具体函数签名变更：**

```python
# Before:
def guard_sql(sql: str, scope: GuardScope | None = None) -> GuardResult
def execute_guarded_sql(guard_result: GuardResult) -> QueryResult
def retrieve_metadata_assets(question: str) -> RetrievalResult
def build_focused_context(retrieval: RetrievalResult) -> str
def build_default_guard_scope(datasource_name: str) -> GuardScope

# After (全部增加 datasource_name 参数):
def guard_sql(sql: str, scope: GuardScope | None = None,
              datasource_name: str = "duckdb_ecommerce") -> GuardResult
def execute_guarded_sql(guard_result: GuardResult,
                        datasource_name: str = "duckdb_ecommerce") -> QueryResult
def retrieve_metadata_assets(question: str,
                             datasource_name: str = "duckdb_ecommerce") -> RetrievalResult
def build_focused_context(retrieval: RetrievalResult,
                          datasource_name: str = "duckdb_ecommerce") -> str
def build_default_guard_scope(datasource_name: str = "duckdb_ecommerce") -> GuardScope
```

所有函数默认值保持 `"duckdb_ecommerce"`，确保不改调用方时行为不变。

### SSE 步骤流增强

```text
StepType.DATASOURCE_SELECTED = "datasource_selected"

# SSE 事件
event: step
data: {
    "step": "datasource_selected",
    "status": "completed",
    "name": "clickhouse_ecommerce",
    "dialect": "clickhouse",
    "display_name": "ClickHouse (OLAP)"
}
```

沿用现有 SSE 协议：前端仍监听 `event === "step"`，通过 `payload.step` 识别 `datasource_selected`。

### 设计决策

- 不在 Agent 里做方言翻译——直接让 LLM 生成目标方言 SQL，翻译层容易引入语义错误
- Prompt 是方言适配的核心载体——通过 schema context + generation prompt 双重约束
- Repair 自然适配——加上方言信息即可

## 7. 前端数据源切换与展示增强

### 数据源选择器

在聊天界面顶部栏添加下拉选择器，切换后立即生效。不引入 vue-router，沿用现有 App 内 Tab 切换模式。

### API 扩展

```python
# 新增
GET /api/datasources
Response: {
    "sources": [
        {"name": "duckdb_ecommerce", "display_name": "DuckDB (本地)",
         "dialect": "duckdb", "status": "available"},
        {"name": "clickhouse_ecommerce", "display_name": "ClickHouse (OLAP)",
         "dialect": "clickhouse", "status": "available"}
    ],
    "default": "duckdb_ecommerce"
}

# 修改
POST /api/chat/query
Request: {
    "question": "...",
    "datasource": "clickhouse_ecommerce"    # 新增，可选
}
```

### 前端状态管理

```typescript
// stores/datasource.ts (新增)
interface DatasourceState {
    sources: DatasourceInfo[]
    current: string
    loading: boolean
}

// fetchSources()     启动时调用
// selectSource(name) 切换数据源
```

### 查询结果展示增强

- 步骤流新增 `datasource_selected` step 展示
- SQL 展示区域新增：数据源名称、方言、查询耗时、返回行数
- 查询耗时由 execute 节点记录

### Admin 页面适配

- 表列表：增加按数据源筛选
- 字段详情：展示 OLAP 特有字段（partition_key、sorting_key、engine）
- Relationship 管理：按数据源分组展示
- 指标管理：展示 dialect_overrides 列

### 设计决策

- 不存 localStorage——数据源选择是会话级别，刷新回到默认
- 优雅降级——ClickHouse 不可用时下拉框只显示 DuckDB，不报错

## 8. Eval 扩展

### Eval Case 格式扩展

```yaml
- id: ch_recent_sales
  question: 查询最近 30 天每日销售额
  tags: [ecommerce, filter, group_by, clickhouse]
  datasource: clickhouse_ecommerce
  expected:
    should_execute: true
    required_tables: [fact_orders]
    dialect_hints:
      - function: toStartOfDay
      - no_duckdb_syntax: true
```

### 方言适配测试维度

| 维度 | DuckDB | ClickHouse | 说明 |
|------|--------|------------|------|
| 基本聚合 | 现有 case | 同问题新增 | 同一问题两种方言 |
| 日期函数 | `DATE_TRUNC` | `toStartOfMonth` | 方言差异核心 |
| 类型转换 | `::INT` | `toInt32()` | 方言差异 |
| 条件聚合 | `COUNT(IF(...))` | `countIf()` | 方言差异 |
| 安全拦截 | DuckDB 拦截 | CH 拦截 | 各自特有危险操作 |

### Eval Runner 扩展

- 按 datasource 字段过滤 case
- 跳过不可用数据源的 case
- 报告按数据源分组展示

### 报告格式

```markdown
### DuckDB (本地) - 35 cases
| 指标 | 结果 |
|------|------|
| 执行成功率 | ... |

### ClickHouse (OLAP) - 20 cases
| 指标 | 结果 |
|------|------|
| 执行成功率 | ... |
| 方言正确率 | ... |
```

## 9. 验收标准

### 必须通过

1. Docker Compose 一键启动 ClickHouse + 导入电商数据
2. 前端可以切换 DuckDB / ClickHouse 数据源
3. 同一问题在两个数据源上生成对应方言 SQL
4. ClickHouse SQL 经过 SQL Guard（含 CH 特有规则拦截）
5. ClickHouse 查询经过只读连接 + 超时控制
6. ClickHouse schema 同步到元数据库（含 OLAP 特有字段）
7. 前端展示数据源类型、方言、查询耗时
8. DuckDB 现有 eval 全部回归通过（0 退化）
9. ClickHouse eval 至少 15 条通过

### 加分项

- EXPLAIN 集成展示查询计划
- 性能提示（是否命中分区键）
- Admin UI 展示 OLAP 元数据

## 10. Iteration 拆分

```text
I6.1 Connector 抽象层 + DuckDB 适配
  -> DataSourceConnector Protocol
  -> DuckDBConnector（包装现有 db.py + sync.py）
  -> DataSourceManager
  -> config.py 扩展
  -> 后端启动时自动注册可用数据源

I6.2 ClickHouse Connector + Docker 环境
  -> docker-compose.yml
  -> ClickHouseConnector
  -> 表引擎设计 + 建表脚本
  -> 数据导入脚本（复用 seed 数据）
  -> 健康检查和连接测试

I6.3 SQL Guard 方言适配 + 安全扩展
  -> 动态 dialect（从 Connector 获取）
  -> CH operation 拦截: SYSTEM, KILL, RENAME, EXCHANGE
  -> CH function 拦截: s3, url, remote
  -> INSERT INTO FUNCTION 检测
  -> Guard 测试用例 10+

I6.4 元数据同步 + 方言 Context
  -> ClickHouse schema introspection
  -> OLAP 元数据字段（engine, partition_key, sorting_key）
  -> 元数据库表结构扩展
  -> Schema Context 方言提示模板
  -> 语义资产 seed 数据

I6.5 Agent 方言适配 + API 扩展
  -> SQL Generation Prompt 分方言
  -> Repair 方言适配
  -> AgentState 新增 datasource 字段
  -> GET /api/datasources
  -> POST /api/chat/query 增加 datasource 参数
  -> SSE datasource_selected 事件

I6.6 前端数据源切换 + 展示
  -> 数据源下拉选择器
  -> 查询结果展示数据源信息
  -> Admin UI OLAP 字段展示
  -> 查询耗时展示

I6.7 Eval 扩展 + ROADMAP 更新
  -> ClickHouse eval case 15+
  -> Eval runner 数据源分组
  -> 报告按数据源分 section
  -> DuckDB 回归测试
  -> 更新 ROADMAP.md（新增 Phase 6.5）
```

## 11. ROADMAP 更新

### 新增 Phase 6.5: OLAP 分析工具

从原 Phase 6 移出的能力：

- 指标计算工具: 同比、环比、移动平均
- 漏斗转化分析
- 留存分析
- TopN 分析
- 数据质量检查
- EXPLAIN 查询计划展示
- 性能提示（分区命中、排序键利用）
- Agent 按需调用 OLAP 工具

### 更新后阶段顺序

```text
Phase 0    项目骨架
Phase 1    工业化最小闭环
Phase 2    语义层与规则检索
Phase 2.5  Semantic Admin
Phase 3    评测体系
Phase 4    向量召回
Phase 5    SQL 修复
Phase 6    ClickHouse 接入           ← 本次设计
Phase 6.5  OLAP 分析工具              ← 新增
Phase 7    MCP 工具化
Phase 8    产品化与求职包装
```

## 12. 技术决策总结

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 架构方案 | Connector 抽象层 | 可扩展，面试好讲 |
| 数据源部署 | Docker Compose 自启 | 一键演示 |
| 数据源切换 | 前端会话级切换 | 演示体验好 |
| 元数据存储 | 共享 SQLite 元数据库 | 复用现有检索链路 |
| 指标口径 | 共享 + dialect_overrides | 当前零冲突，后续可覆盖 |
| SQL Guard | 动态方言 + 最小安全扩展 | 必须的拦截做，性能治理后做 |
| ORM | 不引入 SQLAlchemy | 原生驱动更稳定，sqlglot 已覆盖方言转换 |
| 方言适配 | Prompt 引导，不做翻译层 | 翻译层容易引入语义错误 |
| Phase 范围 | 最小 ClickHouse 接入 | OLAP 工具拆到 Phase 6.5 |
