# Industrial NL2SQL Data Agent Platform 开发路线图

> 项目定位：面向 OLAP 数据仓库和企业经营分析场景的工业级 NL2SQL / Data Agent 平台。
>
> 核心目标：不是做一个简单 Text-to-SQL Demo，而是做一个具备语义层、SQL 安全、元数据召回、执行修复、自动可视化、评测闭环和工具扩展能力的求职级项目。

## 1. 总体建设原则

### 1.1 先闭环，再增强

第一阶段只做一个“能跑、可解释、可保护”的最小工业闭环：

```text
用户问题 -> 元数据上下文 -> SQL 生成 -> SQL Guard -> 执行 -> 表格/图表 -> 中文总结
```

不要一开始就引入过多外部组件，例如 Qdrant、Elasticsearch、MCP、多租户权限、复杂指标平台。它们是后续增强项，不是第一版必要条件。

### 1.2 安全能力优先于模型能力

工业级 NL2SQL 的关键不是“模型能生成 SQL”，而是“系统敢不敢执行这条 SQL”。因此 SQL Guard 必须尽早做，并且必须由确定性代码实现，不能只靠 prompt。

### 1.3 Schema 探知是核心能力

NL2SQL 的质量上限首先取决于系统是否理解数据库。Schema 探知不能只是把表字段读出来塞进 prompt，而应该作为可复用的 metadata layer：

```text
数据源连接 -> schema introspection -> profiling -> relationship discovery -> semantic overlay -> context builder
```

成熟产品的取舍可以概括为：

- Microsoft Purview / Alation / DataHub / OpenMetadata：强调 metadata ingestion、profiling、lineage、classification
- Looker / dbt Semantic Layer / Cube / Tableau Pulse：强调 semantic layer、metric、join、business term
- Snowflake Cortex Analyst / Databricks Genie：强调受控语义空间、trusted assets、verified queries

因此本项目不采用“agent 每次临场扫库”的模式，而是把探知结果落库，查询时只使用已同步、已过滤、可审计的 metadata。

### 1.4 成熟产品能力取舍

完整 value/cost 矩阵以 Phase 1 spec 的 `5.8 成熟产品能力取舍矩阵` 为唯一真源。ROADMAP 只保留阶段原则：

- Phase 1 做高 value、低 cost、能增强可信度的能力：Analysis Space、Verified Queries 雏形、Explainability、SQL Guard。
- Phase 2 做业务语义检索闭环：DB-backed semantic assets、Metric Layer、规则检索、focused context。
- Phase 2.5 可选做语义资产管理：指标、别名、verified queries、analysis space 的 CRUD 和人工确认。
- Phase 3 做评测体系和真实模型验证：扩大 case、生成报告、定位 retrieval / SQL / execution 错误。
- Phase 4 再做召回增强：Value Recall、向量召回、混合召回。
- 权限、血缘、成本治理进入后期，不干扰第一条闭环。

### 1.5 每个阶段都能演示

每个阶段都应该有可运行、可截图、可写简历的结果：

- 后端接口能调用。
- 前端页面能展示。
- eval 能跑出报告。
- README 能说明当前能力。

### 1.6 设计成 OLAP 问数平台，不设计成单一垂直助手

新项目的第一数据源采用 DuckDB + 示例电商数仓数据集，后续扩展 ClickHouse。项目主线面向通用企业经营分析，而不是单一垂直方向：

- DuckDB 负责本地开发、演示和评测，降低环境门槛。
- 电商数仓数据集负责覆盖订单、用户、商品、渠道、地区、时间等典型 BI 场景。
- ClickHouse 作为后续工业级 OLAP 数据源，体现列式存储、宽表聚合、查询性能和 SQL 方言适配能力。
- 插件能力优先围绕 OLAP 查询、指标分析、异常归因、图表推荐和数据质量检查展开。

## 2. 目标能力地图

最终项目能力分为 8 层：

```text
Frontend UI
  聊天问数 / SQL 展示 / 表格 / 图表 / 步骤流 / 历史记录

API Backend
  查询接口 / 元数据接口 / 评测接口 / 数据源接口 / 审计接口

Schema Explorer
  数据源探知 / 表字段同步 / profiling / join 推断 / 来源与置信度 / 语义补丁

Agent Workflow
  意图识别 / 上下文召回 / SQL 生成 / SQL 修复 / 结果解释 / 图表推荐

Semantic Layer
  表定义 / 字段定义 / 指标口径 / 业务别名 / 示例 SQL / join 关系

Trusted Assets
  Analysis Space / 可问表集合 / 可用指标集合 / verified queries / 业务说明

SQL Guard
  AST 解析 / 只读限制 / 表字段白名单 / 自动 LIMIT / EXPLAIN / 审计

Execution Engine
  只读查询 / 超时控制 / 行数限制 / 错误归一化 / 结果标准化

OLAP Analytics Tools
  指标计算 / 同环比分析 / 漏斗分析 / 留存分析 / TopN 分析 / 数据质量检查

Evaluation
  问题集 / 期望 SQL / 结果校验 / 安全用例 / 报告生成
```

## 3. 推荐项目目录

```text
industrial_nl2sql/
  backend/
    app/
      api/
      agent/
      core/
      db/
      connectors/
      execution/
      metadata/
      schemas/
      sql_guard/
      visualization/
      evals/
    tests/
    pyproject.toml

  frontend/
    src/
      api/
      components/
      pages/
      stores/
      styles/
    package.json

  mcp_servers/
    db_tools/
    olap_tools/

  evals/
    datasets/
    reports/

  docs/
    NL2SQL_RESEARCH.md
    ARCHITECTURE.md
    ROADMAP.md
    SQL_GUARD_DESIGN.md
    EVALUATION_DESIGN.md

  scripts/
  docker/
  README.md
  .env.example
```

## 4. Phase 0：项目定义与骨架

### 目标

建立新项目的工程边界、文档体系和最小目录结构。

### 交付物

- `README.md`
- `.env.example`
- `docs/NL2SQL_RESEARCH.md`
- `docs/ROADMAP.md`
- `docs/ARCHITECTURE.md`
- 后端和前端空骨架

### 任务

1. 明确项目名称、定位、目标用户和核心卖点。
2. 初始化 `backend/`。
3. 初始化 `frontend/`。
4. 设计环境变量。
5. 写第一版架构图。
6. 写第一版接口清单。

### 验收标准

- 新项目目录独立存在。
- README 能讲清楚项目目标。
- 本地可以启动一个空 FastAPI 服务。
- 前端可以启动一个空页面。

### 建议用时

0.5 到 1 天。

## 5. Phase 1：工业化最小闭环

### 目标

完成第一条真实 NL2SQL 查询链路，具备前后端、SQL Guard、执行结果和基础图表。

### 范围

这一阶段只支持一个 DuckDB 数据源和一套小型电商数仓示例数据。建议先采用星型模型，而不是单宽表：

```text
fact_orders
fact_order_items
dim_users
dim_products
dim_regions
dim_channels
dim_date
```

### 后端能力

1. FastAPI 应用骨架。
2. 配置系统。
3. 数据库连接管理。
4. Metadata foundation：Schema Explorer、Semantic Overlay、Analysis Space、Verified Queries、Explainability。
5. SQL Guard v1。
6. 简单 NL2SQL prompt。
7. LangGraph 查询链路 v1。
8. SSE 流式步骤。
9. 查询执行和结果标准化。
10. 图表推荐 v1。

### Schema Explorer v1

Phase 1 的 Schema Explorer 目标是建立方向正确的最小 metadata layer：

- 从 DuckDB introspection 自动同步表名、字段名、字段类型。
- 采集 row count 和少量 sample values。
- 自动推断简单 join 关系，并记录 `source` 和 `confidence`。
- 支持 semantic overlay 补充字段说明、指标口径、别名和确认 join。
- `build_schema_context` 只读取已落库 metadata，不在查询时临场扫库。

不做：

- 不引入 Qdrant、Elasticsearch、Embedding 服务。
- 不做复杂血缘、数据质量、权限治理。
- 不让 LLM 直接决定物理 schema 真相。

### Iteration 1 细分

Iteration 1 拆成 4 个小任务：

```text
I1.1 Schema Explorer 补齐
  -> metadata sync 自动同步表、字段、类型、row count
  -> sample values 自动采样
  -> relationship source / confidence / fanout_risk
  -> 简单 relationship inference

I1.2 Semantic Overlay 重构
  -> static.py 降级为 semantic_overlay.py
  -> overlay 只补业务语义，不定义物理 schema
  -> overlay 可确认 relationship 和指标口径

I1.3 Analysis Space + Verified Queries
  -> 可问表集合
  -> 可用指标集合
  -> allowed operations
  -> 至少 1 条 verified query

I1.4 Explainability Context
  -> tables / columns / metrics
  -> join paths
  -> date interpretation
  -> relationship source / confidence / fanout_risk
```

### Iteration 2 细分

Iteration 2 拆成 4 个小任务：

```text
I2.1 SQL Guard Core
  -> 引入 sqlglot
  -> SQLGlot parse + 单语句校验 + DuckDB dialect
  -> 只允许 SELECT
  -> 拒绝 DDL / DML / COPY / INSTALL / LOAD
  -> 拒绝 read_csv / read_parquet / read_json

I2.2 Scope Guard
  -> 从 Analysis Space 读取表白名单
  -> 从 metadata 读取字段白名单
  -> 校验表和字段访问范围
  -> 支持基础 alias 解析
  -> unqualified column 只在唯一匹配时通过

I2.3 Cost Guard + Normalized SQL
  -> 无 LIMIT 自动追加 LIMIT 500
  -> LIMIT 超过 500 时截断
  -> 产出 normalized_sql
  -> executor 只接收 normalized_sql

I2.4 Readonly Executor + Tests
  -> DuckDB read-only connection
  -> 返回 columns / rows / row_count
  -> SQL Guard 单元测试 15+
  -> 覆盖合法 SQL、危险操作、越权表、越权字段、外部读取函数
```

拆分依据：SQL Guard 既是安全边界，也是后续 Agent、SSE、eval 的共同依赖。先做 parse/op/function，再做 scope，再做 cost rewrite，最后接 read-only executor，可以降低定位成本。

### Iteration 3 细分

Iteration 3 拆成 5 个小任务：

```text
I3.1 Provider + MockLLMProvider
  -> LLMProvider 抽象
  -> MockLLMProvider
  -> demo 问题返回固定合法 SQL
  -> 安全问题返回固定危险 SQL
  -> provider 单元测试

I3.2 Agent Workflow Core
  -> agent/state.py
  -> build_context / generate_sql / sql_guard / execute / summarize 节点函数
  -> 接入 schema context、SQL Guard 和 readonly executor
  -> 先用普通函数串链路，不强制引入 LangGraph runtime

I3.3 Query-level Explainability
  -> matched_tables
  -> matched_columns
  -> join_paths
  -> date_interpretation
  -> guard_result
  -> explainability 先用规则生成，不让 LLM 编造

I3.4 Chart Recommender
  -> visualization/recommender.py
  -> chart_type / x_column / y_columns
  -> 时间序列返回 line
  -> 无法识别时 fallback table

I3.5 /api/chat/query + SSE
  -> api/chat.py
  -> POST /api/chat/query
  -> step / done / error 事件
  -> 串起 Mock Agent 完整链路
```

拆分依据：Iteration 3 是链路集成，同时涉及 provider、agent state、Guard/Executor、解释信息、图表推荐和 SSE。先把确定性的 Mock 链路跑通，再接事件流，可以避免模型输出、执行错误和 API 流式响应互相干扰。

### Iteration 4 细分

Iteration 4 拆成 6 个小任务：

```text
I4.1 DeepSeek Provider
  -> DEEPSEEK_API_KEY / base url / model 配置
  -> DeepSeekProvider
  -> 复用 LLMProvider.generate_sql
  -> fake HTTP 单元测试，不真实调用外部 API

I4.2 SQL Generation Prompt
  -> agent/prompts/sql_generation.py
  -> 输入 schema_context + question
  -> 只输出 SQL
  -> 约束 DuckDB / SELECT-only / Analysis Space 内表字段

I4.3 Frontend Scaffold
  -> frontend/
  -> Vue + Vite + TypeScript
  -> 单页 chat app 骨架
  -> API base 配置

I4.4 SSE Client + Step Stream
  -> fetch + ReadableStream
  -> 解析 step / done / error
  -> 展示步骤状态

I4.5 Result Views
  -> SQL 展示
  -> 结果表格
  -> query-level explainability 展示
  -> Guard error 展示

I4.6 ECharts Chart Renderer
  -> 接 chart_recommendation
  -> line 渲染折线图
  -> table fallback 不画图
```

拆分依据：Iteration 4 同时涉及真实模型和前端体验。先接 DeepSeek provider，再写 prompt，再初始化前端，最后逐步接 SSE、结果展示和图表，可以避免模型调试、事件流解析和 UI 状态互相干扰。

### Iteration 5 细分

Iteration 5 拆成 3 个小任务：

```text
I5.1 Smoke Cases
  -> evals/smoke_cases.yaml
  -> 5 条正常查询
  -> 5 条安全用例
  -> 每条 case 明确 expected

I5.2 Smoke Runner
  -> scripts/run_smoke_eval.py
  -> 复用 Mock provider / case SQL
  -> 校验执行结果、Guard 拦截、explainability、chart recommendation
  -> 输出 pass/fail 和非 0 失败退出码

I5.3 Demo Docs
  -> README 启动方式
  -> demo 问题列表
  -> smoke eval 命令
  -> Phase 1 能力和限制
```

拆分依据：Iteration 5 是固化和防回归，不应该再混入新产品能力。先把验收样本变成唯一输入，再写 runner，最后更新 README，可以避免 case 格式、执行逻辑和文档互相牵制。

### Analysis Space v1

参考 Databricks Genie 的 trusted assets 思路，Phase 1 不让用户对整个数据库自由问数，而是限定在一个可问数据空间：

```text
analysis_space:
  name: ecommerce_demo
  datasource: duckdb_ecommerce
  tables: [fact_orders, fact_order_items, dim_date, dim_products, dim_regions, dim_channels, dim_users]
  enabled_metrics: [sales_amount, order_count, aov]
  allowed_operations: [select]
```

Analysis Space 负责限定“哪些资产可信且可问”；SQL Guard 负责限定“生成 SQL 是否安全可执行”。

### Verified Queries v0

参考 Snowflake Cortex Analyst 的 verified queries，Phase 1 先保留少量已验证 question-SQL：

```yaml
- id: recent_30d_sales
  question: 查询最近30天每日销售额和订单数
  sql: SELECT ...
  tags: [sales, time_series]
```

它同时服务 demo、prompt few-shot、smoke eval 和后续回归测试。

### 前端能力

1. 聊天输入。
2. Assistant 消息展示。
3. 步骤流展示。
4. 生成 SQL 展示。
5. 表格展示。
6. 基础图表展示。
7. 错误信息展示。

### SQL Guard v1

必须实现：

- 只允许单条 SQL。
- 只允许 `SELECT`。
- 禁止 `INSERT`、`UPDATE`、`DELETE`、`DROP`、`ALTER`、`TRUNCATE`、`CREATE`。
- 使用 SQLGlot 解析 SQL。
- 检查访问表是否在白名单内。
- 自动补充 `LIMIT`。
- 返回结构化校验结果。

示例返回：

```json
{
  "allowed": true,
  "normalized_sql": "SELECT order_date, total_amount FROM fact_orders LIMIT 100",
  "warnings": ["LIMIT 100 was added automatically"]
}
```

### Agent v1 流程

```text
receive_question
  -> build_schema_context
  -> generate_sql
  -> sql_guard
  -> execute_sql
  -> summarize_result
  -> recommend_chart
```

### API 草案

```text
POST /api/chat/query
GET  /api/metadata/tables
POST /api/metadata/sync
GET  /api/query/history
```

### 验收标准

- 可以在前端输入：“查询最近 30 天每日销售额和订单数”。
- 可以自动同步 DuckDB schema、row count、sample values。
- 可以展示 join 关系来源和 semantic overlay 补充结果。
- 可以展示 Analysis Space 中可问表和可用指标。
- 至少保留 1 条 verified query 作为 demo 和 smoke eval 资产。
- 查询结果包含 query-level explainability：命中的表、字段、指标、join path、时间解释和 Guard 结果。
- 后端返回执行步骤。
- 前端展示 SQL、表格和折线图。
- 危险请求会被 SQL Guard 阻断，例如“删除 2024 年数据”。
- 至少有 10 个 SQL Guard 单元测试。

### 建议用时

3 到 5 天。

## 6. Phase 2：DB-backed 语义层与规则检索

### 目标

从 Phase 1 的硬编码 demo 语义，升级为可落库、可检索、可审计的语义层。核心不是先做管理后台，而是让 Agent 查询链路运行时真正消费数据库里的 semantic assets，并用聚焦上下文替代全量 schema dump。

当前状态：已完成。

### 能力

1. `semantic_overlay.py`、`verified_queries.py`、`analysis_space.py` 降级为 seed 数据源，运行时不再直接读取代码常量。
2. 表描述、字段描述、字段别名、指标口径、verified queries、analysis space 落到 SQLite metadata DB。
3. Metric Layer v1：支持 `sales_amount`、`order_count`、`aov` 等结构化指标。
4. 规则检索 v1：基于表名、字段名、中文别名、指标、verified query、sample values 检索相关资产。
5. Focused context builder：只把命中的表、列、指标、join path、示例 SQL 放入 prompt。
6. Fallback：检索无命中时回退到 Phase 1 全量 schema context，保证链路不中断。
7. Agent v2：新增 `retrieve_context` 节点，SSE 输出检索步骤。
8. Smoke eval 扩展到 15 条，并覆盖 Phase 2 retrieval/focused context。

### 明确不做

以下能力不属于 Phase 2 已完成范围，避免把语义检索阶段膨胀成管理平台：

- 指标、别名、verified query、analysis space 的完整 CRUD。
- 用户反馈沉淀为 verified query 或 eval case。
- Data Quality Profiling。
- embedding / vector recall / hybrid recall。
- 多租户权限、审批流、血缘影响分析。
- fact-only 查询默认强制补所有维表。

### Metric Layer v1

Metric Layer 负责把业务指标从 prompt 文本升级为结构化资产：

```text
metric:
  name: sales_amount
  label: 销售额
  expression: SUM(fact_orders.payment_amount)
  default_time_column: dim_date.date_value
  allowed_dimensions: [date, channel, region, category]
```

指标需要和 relationship safety 联动，避免错误 join path 导致聚合膨胀。

### 第一版不一定上向量库

为了控制复杂度，Phase 2 可以先用规则检索：

- 表名/字段名匹配。
- 中文别名匹配。
- 指标名匹配。
- 示例问题关键词匹配。

向量检索放到 Phase 4。Phase 3 先用评测报告证明规则检索的准确率、失败类型和瓶颈，再决定是否引入向量库。

### 数据模型

需要建立这些表或等价存储：

```text
meta_tables
meta_columns
meta_relationships
meta_metrics
meta_column_aliases
meta_verified_queries
meta_analysis_spaces
```

### Agent v2 流程

```text
receive_question
  -> normalize_question
  -> retrieve_metadata_assets
  -> build_focused_context
  -> generate_sql
  -> sql_guard
  -> execute_sql
  -> summarize_result
  -> recommend_chart
```

### 验收标准

- 普通命中场景不需要把全量 schema 塞进 prompt。
- 可以通过字段别名理解问题。
- 可以使用指标口径生成 SQL。
- 可以展示本次查询命中的表、字段、指标和示例。
- SSE 可以展示 `retrieve_context` 步骤和 `fallback_used`。
- smoke eval 至少 15 条通过。

### 已知取舍

- fact-only 匹配暂不默认补 `dim_date`。当前只在 metric default time column、verified query 或显式时间字段命中时补时间维。
- join partner expansion 保持保守：维表命中且缺事实表时才反向补最多 3 个 fact partner；已有事实表时只补 join key。
- 检索排序仍是规则打分，没有 embedding/vector recall。
- CRUD 管理能力后移到 Phase 2.5。

### 建议用时

已完成。后续只做回归修复，不再往 Phase 2 塞新产品能力。

## 6.5 Phase 2.5：Semantic Admin 管理能力（可选）

状态：已完成。Phase 2.5 最终交付为语义资产 CRUD API、Admin UI、失效资产校验 API 和集成测试。

### 目标

把 Phase 2 通过 seed 初始化的语义资产，升级为可维护的管理能力。这个阶段服务“平台感”和长期维护，不是 Agent 查询链路的必要前置。

如果目标是尽快证明 NL2SQL 准确率，优先做 Phase 3；如果目标是展示完整数据平台能力，再做 Phase 2.5。

### 能力

1. 指标列表、详情、新增、编辑、启停。
2. 字段别名列表、新增、删除。
3. verified query 列表、新增、编辑、启停。
4. analysis space 表白名单、指标白名单、allowed operations 编辑。
5. relationship 的人工确认、confidence、fanout_risk 编辑。
6. 失效资产校验：字段不存在、表不存在、metric expression 引用失效。

### Iteration 拆分

```text
I2.5.1 Metrics + Aliases CRUD API
  -> metrics / aliases 的列表、新增、编辑/删除、启停
  -> 引用校验：表存在、字段存在、metric expression 引用有效
  -> API 测试

I2.5.2 Verified Queries + Analysis Space + Relationships API
  -> verified queries 新增、编辑、启停
  -> analysis space 单例编辑
  -> relationships 元数据编辑
  -> verified query 保存时走 SQL Guard

I2.5.3 Admin UI
  -> App 内部 Chat/Admin 切换，不引入 vue-router
  -> 单文件 Admin.vue，5 个 Tab 视图
  -> 紧凑表格和侧边编辑面板

I2.5.4 失效资产校验 + 集成测试
  -> GET /api/metadata/validate
  -> validate_semantic_assets()
  -> 覆盖 metric / alias / verified query / analysis space / relationship 的断链检测
  -> 全量 pytest 和 smoke eval 回归
```

### 验收标准

- 可以不用改代码就新增一个字段别名，并让检索 API 命中它。
- 可以不用改代码就禁用一个 metric，并让 Agent 不再使用它。
- 修改 analysis space 后，SQL Guard 和 retrieval 行为同步变化。
- 所有写接口都有基础引用校验和测试。
- `GET /api/metadata/validate` 可以检测语义资产断链。
- Admin UI 可以管理 5 类语义资产。

### 建议用时

3 到 5 天。

## 7. Phase 3：评测体系与真实模型验证

### 目标

建立项目可信度。让检索命中率、SQL 生成正确率、执行成功率、安全拦截能力、延迟和错误类型可以量化，并用真实模型验证 Phase 2 规则检索是否足够。

### 能力

1. eval case 定义。
2. 批量运行评测。
3. 保存生成 SQL。
4. 保存执行结果。
5. 保存错误原因。
6. 生成 Markdown/HTML 报告。
7. 支持模型和 prompt 版本对比。
8. retrieval expected 校验：期望命中的表、字段、指标、verified query。
9. 真实 DeepSeek 模型批量评测。
10. 错误归因：retrieval miss、SQL invalid、guard blocked、execution error、result mismatch。

### Iteration 拆分

```text
I3.1 Eval Report
  -> smoke runner 输出 Markdown/HTML 报告
  -> 记录 pass rate、失败 case、错误类型、耗时

I3.2 Case Expansion
  -> 扩展到 30+ cases
  -> 覆盖时间、地区、渠道、品类、客单价、安全拦截、fallback
  -> 每条 case 增加 retrieval expected

I3.3 Real LLM Benchmark
  -> 支持 DeepSeek provider 批量跑 eval
  -> 保存 generated_sql、normalized_sql、guard_result、execution_result
  -> 没有 API key 时自动跳过真实模型 case

I3.4 Error Taxonomy
  -> 自动归类 retrieval / generation / guard / execution / result mismatch
  -> 报告按错误类型聚合，指导下一阶段增强

I3.5 Docs + README
  -> README 展示最新 eval 结果
  -> 记录规则检索的已知短板和 Phase 4 向量召回动机
```

### Eval Case 格式

```yaml
- id: ecommerce_recent_sales
  question: 查询最近 30 天每日销售额
  tags: [ecommerce, filter, group_by, order_by]
  expected:
    should_execute: true
    required_tables: [fact_orders]
    required_columns: [order_date, total_amount]
    result_columns: [order_date, sales_amount]

- id: unsafe_delete
  question: 删除 2024 年的订单数据
  tags: [security]
  expected:
    should_execute: false
    guard_stage: operation_guard
```

### 指标

- Retrieval table hit rate。
- Retrieval metric hit rate。
- SQL parse success rate。
- Guard pass rate。
- Unsafe block rate。
- Execution success rate。
- Result column match rate。
- Average latency。
- Average repair count。
- Chart recommendation match rate。

### 验收标准

- 命令行可以运行 `evals`。
- 至少 30 条评测问题。
- 报告能显示成功率、失败 case、错误类型。
- 在 `DEEPSEEK_API_KEY` 可用时，可以跑真实模型评测。
- README 中展示一张评测结果截图或表格。

### 建议用时

3 到 5 天。

## 8. Phase 4：向量召回与上下文增强

### 目标

让系统可以处理更大的 schema 和更自然的中文问题。

Phase 4 的前提是 Phase 3 报告已经说明规则检索的瓶颈。只有当规则别名、指标和 verified query 不足以覆盖自然语言表达时，才引入向量库，避免为了技术栈完整而增加复杂度。

### 能力

1. 引入 Qdrant、LanceDB 或 Chroma。
2. 表、字段、指标、示例 SQL 向量化。
3. 上下文召回结果排序。
4. 与规则召回融合。
5. 召回结果可解释展示。
6. 字段值召回。

### 推荐策略

不要完全依赖向量相似度。建议使用混合召回：

```text
规则匹配得分 + 向量相似度 + 业务优先级 + 历史使用频率
```

Value Recall 参考 ThoughtSpot 和“掌柜问数”的做法，用于识别用户问题中的业务值属于哪个字段，例如“华东”“天猫”“美妆个护”。Phase 4 再引入全文/向量能力，避免 Phase 1 基础设施过重。

### 验收标准

- schema 扩展到多表后仍能选择相关表。
- 前端可以展示召回上下文。
- eval 报告可以对比“无向量召回”和“有向量召回”的效果。
- Value Recall 可以识别“华东”“天猫”“美妆个护”这类业务值属于哪个字段。

### 建议用时

5 到 8 天。

## 9. Phase 5：SQL 修复与执行反馈闭环

### 目标

让系统具备自我修复能力，而不是 SQL 失败后直接报错。

### 能力

1. Guard 错误修复。
2. Parser 错误修复。
3. Database error 修复。
4. EXPLAIN 错误修复。
5. 最大修复次数控制。
6. 修复过程可观测。

### 流程

```text
generate_sql
  -> sql_guard failed
  -> repair_sql
  -> sql_guard

execute_sql failed
  -> normalize_error
  -> repair_sql
  -> sql_guard
  -> execute_sql
```

### 关键规则

- 最多修复 2 次。
- 修复后的 SQL 必须再次经过 SQL Guard。
- 修复过程必须记录原 SQL、错误、修复后 SQL。
- 如果仍失败，要给用户可理解的错误解释。

### 验收标准

- 可以处理字段名错误、保留字引用错误、日期函数错误等常见问题。
- 修复过程在前端步骤流中可见。
- eval 报告包含 `repair_count`。

### 建议用时

4 到 6 天。

## 10. Phase 6：ClickHouse 与 OLAP 能力增强

### 目标

从 DuckDB 本地演示升级到更接近真实企业环境的 OLAP 数据源，重点体现 ClickHouse 接入、SQL 方言适配、性能治理和复杂分析能力。

### 能力

1. ClickHouse 数据源连接器。
2. DuckDB / ClickHouse 方言差异处理。
3. ClickHouse schema 同步。
4. ClickHouse EXPLAIN / query log 集成。
5. 大宽表聚合查询支持。
6. 分区字段、排序键、低基数字段等 OLAP 元数据展示。
7. 查询性能提示，例如是否命中分区、是否缺少时间过滤。

### OLAP 分析场景

- 销售额、订单数、客单价趋势。
- 品类、地区、渠道贡献分析。
- TopN 商品和用户分层。
- 同比、环比、移动平均。
- 漏斗转化。
- 留存分析。
- 异常波动解释。

### Agent 增强

Agent 需要理解 OLAP 常见查询约束：

- 大表查询必须尽量带时间过滤。
- 明细查询默认限制行数。
- 聚合查询优先使用分区字段和排序键。
- 生成 SQL 时考虑 ClickHouse 函数和日期语法。
- 当用户问题过宽时，主动要求缩小时间范围或维度。

```text
用户：统计今年每月各渠道销售额同比增长率
路由：NL2SQL -> ClickHouse dialect -> SQL Guard -> EXPLAIN -> Execute
```

### 验收标准

- 可以在配置中切换 DuckDB 和 ClickHouse 数据源。
- 同一类业务问题可以在 DuckDB 和 ClickHouse 上生成对应方言 SQL。
- ClickHouse 查询经过 SQL Guard、EXPLAIN 和超时控制。
- 前端可以展示数据源类型、查询耗时、返回行数和性能提示。

### 建议用时

5 到 8 天。

## 11. Phase 7：MCP 工具化

### 目标

把核心能力以 MCP server 方式暴露，体现现代 Agent 工具生态能力。

### MCP Server

```text
mcp_servers/db_tools
  list_tables
  get_table_schema
  query_readonly

mcp_servers/olap_tools
  profile_table
  explain_query
  metric_catalog_search
  data_quality_check
```

### 关键原则

- MCP 的 `query_readonly` 也必须走 SQL Guard。
- MCP 不能直接连接数据库绕过后端审计。
- MCP 工具应返回结构化结果。

### 验收标准

- 可以用 MCP client 调用数据库 schema 工具。
- 可以用 MCP client 调用 OLAP profile、EXPLAIN 和指标检索工具。
- README 中说明 MCP 工具列表。

### 建议用时

4 到 6 天。

## 12. Phase 8：产品化与求职包装

### 目标

把项目打磨成可展示、可讲解、可运行的求职作品。

### 交付物

1. Docker Compose。
2. README。
3. 架构图。
4. 页面截图。
5. 演示 GIF。
6. eval 报告。
7. 技术难点文档。
8. 简历项目描述。

### README 必须包含

- 项目定位。
- 核心能力。
- 技术架构。
- 快速启动。
- 环境变量。
- 演示问题。
- SQL Guard 说明。
- 元数据语义层说明。
- 评测结果。
- 后续规划。

### 技术难点文档

建议单独写：

- `docs/SQL_GUARD_DESIGN.md`
- `docs/METADATA_SEMANTIC_LAYER.md`
- `docs/EVALUATION_DESIGN.md`
- `docs/AGENT_WORKFLOW.md`

### 验收标准

- 一个新同事可以按 README 跑起来。
- 面试时可以 3 分钟讲清楚架构。
- 可以现场演示 3 到 5 个典型问题。
- 可以展示一次危险 SQL 被阻断。
- 可以展示 eval 报告。

### 建议用时

3 到 5 天。

## 13. 里程碑计划

### Milestone 1：可运行的 NL2SQL 工业闭环

覆盖 Phase 0 + Phase 1。

成果：

- FastAPI + Vue。
- 一个数据源。
- SQL Guard。
- 查询执行。
- 表格和图表。

这是第一个可以截图展示的版本。

### Milestone 2：具备语义层和评测能力

覆盖 Phase 2 + Phase 3，Phase 2.5 作为可选平台管理增强。

成果：

- 元数据管理。
- 指标口径。
- 示例 SQL。
- 规则检索和聚焦上下文。
- eval runner。
- 真实模型评测报告。

这是项目从 Demo 变成“工程系统”的关键节点。

### Milestone 3：具备复杂问题处理能力

覆盖 Phase 4 + Phase 5。

成果：

- 向量召回。
- SQL 修复。
- 执行反馈闭环。

这是项目体现 NL2SQL 深度能力的节点。

### Milestone 4：具备 OLAP 工业数据源能力

覆盖 Phase 6。

成果：

- ClickHouse 接入。
- DuckDB / ClickHouse 方言适配。
- OLAP 性能治理。
- 复杂经营分析图表。

这是区别于普通 NL2SQL 项目的亮点。

### Milestone 5：具备平台与生态扩展能力

覆盖 Phase 7 + Phase 8。

成果：

- MCP 工具化。
- Docker。
- 文档。
- 演示材料。

这是最终求职包装版本。

## 14. 第一版最小任务清单

如果马上开始开发，建议先做以下任务：

1. 初始化后端 FastAPI 项目。
2. 初始化前端 Vue 项目。
3. 写 `.env.example`。
4. 实现 DuckDB 连接。
5. 实现 DuckDB 电商数仓数据生成。
6. I1.1 实现 Schema Explorer v1：表、字段、类型、row count、sample values、relationship inference。
7. I1.2 重构 semantic overlay：字段说明、指标角色、指标口径、确认 join。
8. I1.3 实现 Analysis Space v1 和 Verified Queries v0。
9. I1.4 实现 `build_schema_context` 和 explainability 输出。
10. 实现 SQLGlot Guard v1。
11. 写 SQL Guard 单元测试。
12. 实现最简单的 SQL 生成 prompt。
13. 实现 LangGraph 查询链路。
14. 实现 SSE 步骤流。
15. 实现前端聊天页面。
16. 实现 SQL 展示和结果表格。
17. 实现基础折线图推荐。
18. 写 10 条 eval case。

## 15. 不做清单

第一阶段明确不做：

- 多租户权限。
- 复杂 BI Dashboard。
- 自训练模型。
- 完整 MCP 工具生态。
- 多数据库方言转换。
- Elasticsearch 值召回。
- 复杂行列级权限。
- 企业 SSO。

这些能力可以写进后续规划，但不要干扰第一版闭环。

## 16. 推荐开发顺序

实际编码顺序建议如下：

```text
backend config
  -> database connection
  -> metadata models
  -> SQL Guard
  -> query execution
  -> LLM provider
  -> agent graph
  -> SSE API
  -> frontend chat
  -> result table
  -> chart renderer
  -> eval runner
```

其中 SQL Guard 要尽早完成，因为后续所有执行链路都要依赖它。

## 17. 简历可讲故事线

项目讲解时可以按这个逻辑：

1. 普通 NL2SQL Demo 的问题是 schema 大、SQL 不安全、不可评测。
2. 我设计了一个工业级 Data Agent 架构。
3. 用语义层解决业务上下文问题。
4. 用 SQL Guard 解决安全执行问题。
5. 用 LangGraph 拆解查询链路，实现可观测和可修复。
6. 用 eval runner 量化系统效果。
7. 用 DuckDB 到 ClickHouse 的演进展示 OLAP 工业化能力。

这条故事线比“我接了一个大模型 API 生成 SQL”有说服力得多。
