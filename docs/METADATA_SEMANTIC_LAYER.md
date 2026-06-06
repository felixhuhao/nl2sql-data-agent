# Metadata Semantic Layer Design

## 目标

NL2SQL 的质量上限首先取决于系统是否理解数据。这个项目不采用“每次查询临场扫库 + 全量 schema 塞 prompt”的方式，而是把数据源探知和业务语义资产落库，查询时只检索可信资产并构建 focused context。

## 核心原则

- 物理 schema 来自数据源 introspection，不由手写静态文件定义。
- 业务语义通过 overlay 资产补充：metric、alias、verified query、analysis space。
- 查询时只使用 enabled metadata 和 active analysis space。
- Retrieval、context builder、SQL Guard 共享同一份 metadata。

核心文件：

- `backend/app/metadata/models.py`
- `backend/app/metadata/sync.py`
- `backend/app/metadata/seed.py`
- `backend/app/metadata/retrieval.py`
- `backend/app/metadata/service.py`
- `backend/app/metadata/hybrid.py`
- `backend/app/metadata/vector/*`

## 资产模型

```text
DataSource
  -> MetaTable
      -> MetaColumn
  -> MetaRelationship
  -> MetaMetric
  -> MetaAlias
  -> MetaVerifiedQuery
  -> MetaAnalysisSpace
```

### MetaTable / MetaColumn

来自 connector introspection，记录表名、字段名、类型、nullable、row count、sample values。ClickHouse 还同步 OLAP 元数据，例如 engine、partition_key、sorting_key、low_cardinality。

### MetaRelationship

记录 join 关系、confidence、source 和 fanout_risk。关系既用于 context，也用于 explainability 和 Guard 的 fanout 检查。

### Metric

定义业务指标口径，例如 `sales_amount`、`order_count`、`aov`。指标进入 retrieval 和 prompt，减少模型临场发明口径。

### Alias

把中文业务词映射到物理资产或指标，例如“销售额”“渠道”“类目”。

### Verified Query

保存经过人工确认的高频问题 SQL。Mock provider 和真实 prompt 都会优先参考这些 trusted examples。

### Analysis Space

限定当前可问资产集合，包括 allowed tables、allowed metrics、enabled 状态和 datasource namespace。它同时影响 retrieval 和 SQL Guard scope。

## Sync 流程

```text
connector.sync_schema()
  -> SchemaSnapshot
  -> _sync_tables_and_columns
  -> relationship inference
  -> semantic seed / overlay
  -> validate semantic assets
```

DuckDB 和 ClickHouse 通过 connector Protocol 提供统一 schema snapshot，因此 metadata 层不再硬编码具体数据库。

## Retrieval 流程

```text
question
  -> rule recall
      table / column / metric / alias / sample value / verified query
  -> optional vector recall
  -> hybrid score merge
  -> analysis space filter
  -> focused context render
```

### 规则召回

规则召回覆盖表/字段名、中文 alias、metric label/expression、verified query、sample values 和时间短语。

### 向量召回

开启 `VECTOR_ENABLED=true` 时，Qdrant 保存 table/column/metric/verified query/value 向量。Hybrid retrieval 将规则分和向量分合并，默认关闭以保持本地开发轻量。

### Fallback

如果 focused retrieval 为空，系统回退到 full schema context，保证用户不会因为检索 miss 完全不可用。Eval 报告会记录 `fallback_used`。

## Focused Context

Context 不是全量 schema dump，而是渲染命中资产：

- datasource dialect hints
- relevant tables and columns
- metric definitions
- aliases
- verified queries
- join paths
- time interpretation
- ClickHouse OLAP metadata when available

Mock smoke 中 focused context 相比 full schema 大约减少 75%，这是语义层可量化的收益。

## Admin 和校验

`metadata.service` 暴露语义资产 CRUD 和校验：

- metric / alias / verified query / analysis space / relationship
- validate dangling references
- verified query 保存前通过 SQL Guard
- 前端 Admin 页面可查看和维护资产

## 与 SQL Guard 的关系

Semantic Layer 和 Guard 分工：

- Semantic Layer：告诉模型“应该用什么资产和口径”。
- SQL Guard：在执行前检查“是否只用了允许的资产和安全操作”。

二者通过 Analysis Space 连接。修改可问空间会同时影响 context 和 Guard scope。

## 技术说明

> 我没有把 schema 写死在 prompt 里，而是做了一个轻量 metadata semantic layer。物理 schema 自动同步，业务语义通过指标、别名和 verified query 落库。查询时先召回可信资产构建 focused context，再由 SQL Guard 用同一份 analysis space 做执行白名单，所以准确率和安全边界来自同一套治理模型。
