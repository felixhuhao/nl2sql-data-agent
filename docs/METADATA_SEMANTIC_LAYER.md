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
  -> coverage score (hybrid: match strength × structural joinability)
  -> [low band] deterministic graph expansion -> re-score
  -> [still low] full-schema fallback (size-capped)  else focused context render
```

### 规则召回

规则召回覆盖表/字段名、中文 alias、metric label/expression、verified query、sample values 和时间短语。

### 向量召回

`VECTOR_ENABLED=auto` 时，Qdrant 可保存 table/column/metric/verified query/value 向量；当 `EMBEDDING_MODEL` 为空时，系统使用默认模型 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，Docker 会把首次下载的模型缓存在 `model_cache` volume。PyTorch 只用于运行 embedding model，Docker 镜像安装的是 CPU-only PyTorch，不需要 CUDA 或外部模型挂载。Qdrant、依赖或索引不可用时，系统自动回退到规则召回。Hybrid retrieval 将规则分和向量分合并；只有显式设置 `VECTOR_ENABLED=disabled` 才会强制关闭向量召回。

### Coverage、Expansion 与 Fallback

召回后计算 `RetrievalCoverage`（`backend/app/metadata/retrieval_coverage.py`）：hybrid 分数 = match strength × structural joinability，产出 `high`/`low` band。fact-role 由 `MetaRelationship` 拓扑推断（source of ≥`FACT_MIN_DIM_EDGES` many_to_one 边），不依赖表命名。

两段式恢复（`RETRIEVAL_EXPANSION_ENABLED` / `RETRIEVAL_FALLBACK_MODE`，**默认 on**，经 vector-active 校准）：

- band 为 `low` 时，先做确定性 **graph expansion**（沿 `MetaRelationship` 1-hop、双向、跳过 high fanout、受 analysis space 约束并 capped），再 re-score。
- 仍为 `low` 且 full schema 在 size budget 内时，回退到 full schema context；否则保留扩展后的 focused context。
- **空召回是无条件不变量**：始终回退 full schema，与新 flag/budget 无关，保持历史行为。

match strength 由 retrieval 层直接产出 `coverage_match_strength ∈ [0,1]`（rule-only 与 hybrid 两条路径同尺度），避免规则/向量分数尺度混淆导致的误触发。SSE/Eval 记录 `retrieval_coverage`（score、band、`expanded`、`fallback_used`、signals）。阈值/权重经 **vector-active** 校准：threshold `0.7`、strength/structural weight `0.5/0.5`，vector-on gate 0/66 high-conf 回归后默认开启（见 `docs/design/retrieval-recall-expansion/`、`retrieval-expansion-closeout/`、`coverage-strength-recalibration/`）。

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

## 设计取舍：手工语义 overlay vs 自动发现

### 决策

物理 schema 自动 introspection，业务语义（metric、alias、confirmed relationship、verified query、analysis space）由**人工 overlay 落库**。系统不做自动指标发现、不从 query history 挖掘语义、也没有 self-learning 回写。连接一个全新数据源时，物理元数据开箱即用；业务语义需要为该源编写 overlay（除非表命名恰好匹配 `fact_`/`dim_`/`_key` 约定，relationship 才会被启发式推断）。

### 为什么这是合理的（市场对照，2026）

这条路线和主流成熟方案是同一个设计族，不是落后：

- Snowflake Cortex Analyst：YAML semantic model（metric/dimension/filter/verified query），全部人工定义。
- Databricks Genie：Unity Catalog + 人工 curated 的 "trusted assets"。
- Wren AI（最接近的开源同类）：MDL modeling language，relation 和 description 在 UI 中人工编写。
- dbt Semantic Layer / Cube / LookML：语义层全部人工 YAML/LookML 定义。

行业共识是 NL2SQL 的瓶颈在 metadata 而非模型：无语义层 accuracy 约 50–70%，有语义层可达 86–95%，Snowflake 实测仅加语义模型即提升约 20%。**要求人工 overlay 是行业 table stakes，被普遍作为 governance 卖点而非缺陷。** 真正不成熟的是自动发现（query-log 挖掘、从 BI 工具反推），业界普遍视为"下一章"，尚无成熟落地。因此不做自动语义发现，是与市场对齐的选择，不是缺口。

### 取舍

优点：
- 语义可信、可审计，口径不由模型临场发明。
- 与 SQL Guard 共享 analysis space，accuracy 和安全边界来自同一份治理模型。
- 差异化能力：query 时对活数据做 `DISTINCT` value grounding（见 `backend/app/agent/semantic_grounding.py`），拒绝生成 schema 中不存在的字段/状态值——这一点多数大厂只对着模型校验、并不下探真实数据。

代价（已知，且接受）：
- 新数据源需要人工编写 overlay 才能获得业务语义；当前 seed 与 demo 星型 schema 耦合（属于打包问题，非架构问题，同类产品同样需要 per-project 编写）。
- 没有 self-learning 回路（Vanna 会自动把成功的 question→SQL 回写训练集）。我们有 verified query 与 `promoted_patterns`，但不自动从成功执行中晋升。这是一个明确、成本可控的后续增强点，非当前范围。
- Connector 覆盖窄（DuckDB + ClickHouse），足够 demo，企业级偏薄。

### 定位建议

对外不以"自动适配语义"为卖点（市场证明这既非预期也非制胜点）。定位在：**Cortex-Analyst / Genie 同族的受治理语义层 + 对活数据的 value-level grounding + accuracy 与安全共用一套模型。**

## 技术说明

> 我没有把 schema 写死在 prompt 里，而是做了一个轻量 metadata semantic layer。物理 schema 自动同步，业务语义通过指标、别名和 verified query 落库。查询时先召回可信资产构建 focused context，再由 SQL Guard 用同一份 analysis space 做执行白名单，所以准确率和安全边界来自同一套治理模型。
