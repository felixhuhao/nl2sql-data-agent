# 工业级 NL2SQL 项目调研与建设思路

> 目标：建设一个可以作为求职作品集核心项目的工业级 NL2SQL / Data Agent 平台。
>
> 当前日期：2026-05-27
>
> 项目暂定名：Industrial NL2SQL Data Agent Platform
>
> 当前主题：面向 OLAP 数据仓库的通用 NL2SQL 数据分析智能体。第一数据源采用 DuckDB + 示例电商数仓数据集，后续扩展 ClickHouse。

## 1. 结论先行

如果只是做一个“自然语言转 SQL”的 Demo，很难体现工业水准。真正有说服力的 NL2SQL 项目，重点不在于模型能不能生成一条 SQL，而在于能否围绕企业数据环境建立一套可控、可解释、可评测、可扩展的数据查询系统。

本项目应该避免做成多个课程项目的拼接，而应明确定位为：

> 面向企业数据分析场景的 NL2SQL Data Agent 平台，具备语义层建模、元数据召回、SQL 安全治理、执行反馈修复、自动可视化、评测体系和 MCP 工具扩展能力。

从现有优秀项目看，行业正在从“prompt + schema + SQL”演进到“语义层 + 上下文工程 + 安全执行 + 评测闭环”。这也是我们项目最应该对齐的方向。

## 2. 我们要做的不是 Text2SQL Demo

普通 Text2SQL Demo 的典型流程是：

```text
用户问题 -> 把数据库 schema 塞进 prompt -> LLM 生成 SQL -> 执行 -> 返回结果
```

这种流程适合课程演示，但问题很明显：

- schema 稍大就超上下文，字段选择不稳定。
- 缺少业务指标定义，模型容易误解“销售额”“活跃用户”“收益率”等口径。
- 缺少值召回，用户说“华东区”“茅台”“核心客户”时，模型不知道对应字段和值。
- 缺少 SQL 安全网关，prompt 里的“不要 DELETE”不能作为安全边界。
- 缺少评测体系，无法证明准确率、稳定性、延迟和修复能力。
- 缺少可观测性，失败时不知道是召回错、SQL 生成错、权限错还是数据为空。

工业级 NL2SQL 项目至少应回答这些问题：

- 模型如何知道业务含义？
- schema 很大时如何选择相关表和字段？
- 如何保证只读、可控、可审计？
- SQL 错了如何定位和自动修复？
- 生成结果是否正确如何评测？
- 不同模型、不同提示词、不同召回策略如何 A/B 对比？
- 用户如何看到 SQL、结果、图表和执行过程？

## 3. 重点开源项目调研

### 3.1 WrenAI

链接：[https://github.com/Canner/WrenAI](https://github.com/Canner/WrenAI)

WrenAI 是当前最值得深入研究的项目之一。它的定位不是单纯 NL2SQL，而是 “open context layer for AI agents over business data”。这点非常关键：它把“上下文层”放在核心位置，强调业务语义、examples、memory、governance 和 SQL 访问治理。

值得关注的能力：

- Modeling Definition Language，包含 models、columns、relationships、views、cubes、metrics。
- 行级/列级访问控制。
- 上下文检索、memory、示例查询。
- governed execution primitives，例如 dry-plan、row limits、access control。
- eval runner 和 correctness primitives。
- Agent SDK，例如 LangChain / LangGraph 集成。
- Git-friendly 的语义定义和上下文沉淀。

它给我们的启发：

1. NL2SQL 不应直接面向裸数据库，而应面向一个“业务语义层”。
2. 表、字段、指标、关系、样例 SQL、业务术语都应该是可版本化资产。
3. SQL 生成不是最终目标，受治理的执行链路才是工业化价值。
4. 对求职项目来说，做一个轻量版语义层比盲目堆模型更有说服力。

我们不建议整仓引入 WrenAI。它已经是一个完整生态，直接集成会让项目失去自主设计空间。更好的做法是借鉴它的上下文层思想，在我们的项目里实现一个轻量 Semantic Layer。

### 3.2 Vanna

链接：[https://github.com/vanna-ai/vanna](https://github.com/vanna-ai/vanna)

Vanna 的核心思路是用 RAG 提升 Text-to-SQL 准确率。它通常把 DDL、文档、历史 question-SQL 示例放进向量库，查询时检索相关上下文，再让 LLM 生成 SQL。

值得关注的能力：

- 基于 DDL、documentation、example SQL 的训练/检索范式。
- 支持多种向量库、LLM 和数据库。
- 面向“和数据库聊天”的产品形态。
- 实现相对轻量，容易理解和复刻。

它给我们的启发：

1. 元数据 RAG 是 NL2SQL 的基本盘。
2. 示例 SQL 非常重要，因为它能传递 join 习惯、指标口径和 SQL 风格。
3. 文档和 DDL 应分层管理，不能全部粗暴塞进 prompt。
4. 可以把 Vanna 式训练数据结构作为我们 metadata-service 的基础数据格式。

我们可以吸收的数据结构：

```yaml
tables:
  - name: fact_orders
    description: 订单事实表
    columns:
      - name: order_date
        type: date
        description: 下单日期
      - name: total_amount
        type: decimal
        description: 订单金额

business_docs:
  - term: 客单价
    definition: 销售额 / 订单数

example_queries:
  - question: 查询 2025 年每月销售额和订单数
    sql: |
      SELECT strftime(order_date, '%Y-%m') AS month,
             SUM(total_amount) AS sales_amount,
             COUNT(DISTINCT order_id) AS order_count
      FROM fact_orders
      WHERE order_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'
      GROUP BY strftime(order_date, '%Y-%m')
      ORDER BY month
```

### 3.3 DB-GPT

链接：[https://github.com/eosphoros-ai/DB-GPT](https://github.com/eosphoros-ai/DB-GPT)

DB-GPT 更像一个完整的 AI Data Assistant 平台，覆盖 Agent、RAG、数据库连接、知识库、应用编排等能力。它比 Vanna 更重，也更接近“平台型项目”。

值得关注的能力：

- 数据库连接管理。
- Agent 编排。
- RAG 能力。
- 面向数据应用的产品结构。
- 后端服务化和前端页面。

它给我们的启发：

1. 求职项目应该有完整产品外壳，而不是只有 notebook 或脚本。
2. 数据源管理、查询历史、会话、日志、可视化都是工业项目需要展示的能力。
3. 可以参考它的平台边界，但不建议引入它的整体复杂度。

我们的取舍：

- 借鉴“Data Assistant 平台”的宏观结构。
- 不照搬复杂模块，先做一个聚焦 NL2SQL 的轻量平台。
- 后续可以加数据源管理、知识库管理和插件市场式工具扩展。

### 3.4 LangChain SQL Agent

链接：[https://docs.langchain.com/oss/python/langchain/sql-agent](https://docs.langchain.com/oss/python/langchain/sql-agent)

LangChain SQL Agent 是标准参考流程。官方教程描述的流程包括：

```text
获取可用表和 schema
判断相关表
获取相关表 schema
基于问题和 schema 生成 SQL
用 LLM 检查常见错误
执行 SQL
根据数据库错误修复
基于结果形成回答
```

官方文档同时明确指出：执行模型生成 SQL 有天然风险，数据库权限必须尽量收窄，并且示例工具不是生产安全实现。

它给我们的启发：

1. 查询流程应该显式拆成多个步骤，而不是单次 LLM 调用。
2. SQL checker 和错误反馈修复是基础能力。
3. 仅靠 LLM checker 不够，必须增加 deterministic SQL Guard。
4. LangGraph 很适合承载这个流程，因为每个节点都可以观测、测试和替换。

我们的基础查询图可以是：

```text
classify_question
  -> extract_keywords
  -> retrieve_metadata
  -> select_tables
  -> generate_sql
  -> sql_guard
  -> explain_sql
  -> execute_sql
  -> summarize_result
  -> recommend_chart

sql_guard failed -> repair_sql -> sql_guard
execute failed   -> repair_sql -> sql_guard
```

### 3.5 XiYan-SQL

链接：[https://github.com/XGenerationLab/XiYan-SQL](https://github.com/XGenerationLab/XiYan-SQL)

XiYan-SQL 是阿里系 Text-to-SQL 项目，包含 SQL 专用模型、训练框架、MCP server、M-Schema、数据库描述生成、日期理解增强等能力。它更偏算法和模型侧，但对中文 NL2SQL 很有参考价值。

值得关注的能力：

- XiYanSQL-QwenCoder 系列 SQL 生成模型。
- M-Schema：半结构化 schema 表示方法。
- Database Description Generation：自动生成数据库描述。
- DateResolver：中文日期理解增强。
- XiYan-MCP-server：用 MCP 暴露 NL2SQL 能力。
- 多生成器、refiner、selector 的候选 SQL 选择思路。

它给我们的启发：

1. 中文 NL2SQL 要特别处理时间表达，例如“去年同期”“近 20 个交易日”“上季度”。
2. schema 不是简单表字段列表，需要更适合模型理解的结构化表示。
3. 可以把 SQL 专用模型作为可插拔 model backend，而不是只依赖通用模型。
4. MCP 是一个值得采用的工具边界：数据库工具、分析工具、搜索工具可以独立发布。

我们的取舍：

- 可以先不训练模型，但预留 model provider 抽象。
- 可以借鉴 M-Schema 思路，设计自己的 `schema_context`.
- 可以实现一个简单 Date Resolver，优先支持中文日期和交易日语义。
- 可以把 MCP server 放到第二阶段，不必第一版就复杂化。

### 3.6 Defog SQLCoder

链接：[https://github.com/defog-ai/sqlcoder](https://github.com/defog-ai/sqlcoder)

SQLCoder 是专门做 NL2SQL 的模型族，也提供 sql-eval 相关思路。它的价值主要在模型和评测层。

值得关注的能力：

- SQL 专用 LLM。
- 面向 novel schema 的评测。
- 可本地部署的模型路线。

它给我们的启发：

1. 项目要预留模型可替换能力，支持 OpenAI、Qwen、DeepSeek、SQLCoder、XiYanSQL 等。
2. 评测不应只看“是否生成 SQL”，而应看执行成功率、结果正确性、修复次数和延迟。
3. 如果硬件允许，可以加入本地 SQLCoder/XiYanSQL 推理作为亮点，但不是第一优先级。

### 3.7 SQLGlot

链接：[https://github.com/tobymao/sqlglot](https://github.com/tobymao/sqlglot)

SQLGlot 是 Python SQL parser / transpiler。对我们来说，它不是可选项，而是 SQL Guard 的核心依赖候选。

我们应该用它做：

- SQL parse。
- 判断语句类型是否只有 SELECT。
- 阻断多语句。
- 提取表名和字段名。
- 检查是否访问白名单以外的表。
- 自动补 LIMIT。
- 识别危险函数、子查询和方言差异。
- 后续支持 DuckDB、ClickHouse、PostgreSQL 等方言转换或校验。

Prompt 约束只能降低风险，不能作为安全边界。SQL Guard 必须是确定性代码。

### 3.8 Spider 2.0 / BIRD

Spider 2.0 链接：[https://spider2-sql.github.io/](https://spider2-sql.github.io/)

Spider 2.0 的定位是评测真实企业 Text-to-SQL 工作流，包含来自企业级数据库用例的问题，数据库经常有上千列，涉及 BigQuery、Snowflake 等环境。这说明传统 Spider 式“单条 SQL”已经不足以代表工业场景。

BIRD 则长期是 NL2SQL 模型能力的重要评测集，XiYan-SQL 等项目也大量引用 BIRD 成绩。

它们给我们的启发：

1. 评测要接近真实业务，而不是只写几个简单问题。
2. 大 schema、复杂 join、多方言、长 SQL、分析任务都要逐步覆盖。
3. 求职项目不必完整复现 benchmark，但应该有自己的 eval runner。

我们可以设计一个小型评测集：

```yaml
- id: ecommerce_monthly_sales
  question: 统计 2025 年每月销售额和订单数
  difficulty: easy
  expected_tables: [fact_orders]
  expected_sql_type: aggregation
  expected_result_check:
    type: columns
    columns: [month, sales_amount, order_count]

- id: ecommerce_category_topn
  question: 统计 2025 年销售额最高的 10 个商品品类
  difficulty: medium
  expected_sql_type: join_aggregation

- id: revenue_yoy_by_region
  question: 统计去年各地区销售额同比增长率
  difficulty: hard
  expected_sql_type: join_aggregation
```

## 4. 我们已有项目资产分析

### 4.1 当前 chatbi_nanobot

已有优势：

- 已有自然语言查询、SQL 执行和图表展示的完整 Demo 经验。
- 有工具封装和 Agent 调用经验。
- 有 Plotly 自动图表。
- 有 Gradio 前端，能完整演示问答、查询和图表。
- 有 nanobot 工具机制，可作为早期 Agent 经验资产。

主要短板：

- 偏 Demo，不是前后端分离工业架构。
- 表和业务范围硬编码。
- 缺少 SQL AST 级安全网关。
- 缺少元数据召回、指标定义和示例 SQL 记忆。
- 缺少测试、评测、日志和可观测性。

吸收方式：

- 保留 SQL 查询、图表推荐和工具封装经验。
- 不继续强化 Gradio 单体，改为 FastAPI + Vue。
- 把原有 `query_db` 改造为受 SQL Guard 保护的只读执行器。
- 原股票分析能力暂不纳入当前项目 scope，避免主题发散。

### 4.2 掌柜问数

已有优势：

- FastAPI + Vue 前后端分离。
- LangGraph 查询流水线清晰。
- 有字段召回、值召回、指标召回、表过滤、SQL 生成、校验、纠错、执行。
- 有 Qdrant、Elasticsearch、Embedding、MySQL meta/dw 等完整思路。

主要短板：

- 依赖服务多，初期落地成本高。
- SQL 安全边界不足。
- SQL 修复后没有再次完整校验。
- 配置安全和项目卫生一般。
- 前端更多是步骤和表格，图表分析弱。

吸收方式：

- 借鉴 LangGraph 节点拆分。
- 借鉴 meta/dw 分层和字段/指标/值召回。
- 不照搬执行层，必须重写 SQL Guard。

### 4.3 mcp_text2sql

已有优势：

- 有 MCP 工具服务。
- 有 list tables、schema、query、SQL check loop。
- 有本地 Qwen3 接入思路。
- 结构简单，便于理解。

主要短板：

- SQLite + Chinook 示例库，偏课程 Demo。
- MCP 工具直接执行 SQL，安全边界弱。
- 没有工业级元数据、权限、评测和前端体验。

吸收方式：

- 借鉴 MCP 工具边界。
- 不照搬 SQL 执行工具。
- 后续把数据库 schema、readonly query、OLAP profile、metric search、data quality check 都做成 MCP tools。

## 5. 推荐目标架构

```text
industrial_nl2sql/
  backend/
    app/
      api/
      core/
      agent/
      metadata/
      sql_guard/
      execution/
      visualization/
      domain_tools/
      evals/
  frontend/
    src/
      pages/
      components/
      services/
  mcp_servers/
    db_tools/
    olap_tools/
    web_tools/
  docs/
    NL2SQL_RESEARCH.md
    ARCHITECTURE.md
    ROADMAP.md
  evals/
    datasets/
    reports/
  docker/
  scripts/
```

### 5.1 后端

后端使用 FastAPI，职责包括：

- 数据源管理。
- 元数据管理。
- Agent 查询 API。
- SSE 流式步骤输出。
- 查询历史。
- SQL 审计日志。
- 图表推荐 API。
- 评测执行 API。

### 5.2 Agent Core

建议使用 LangGraph。核心节点：

```text
question_router
  判断是普通问数、指标解释、数据质量问题、元数据问题还是闲聊。

normalize_question
  中文日期、业务实体、同义词、指标词标准化。

retrieve_context
  召回相关表、字段、值、指标、示例 SQL。

plan_query
  输出结构化计划，包括目标表、指标、维度、过滤条件、排序、限制。

generate_sql
  只生成 SQL，不执行。

sql_guard
  确定性安全校验和改写。

explain_sql
  数据库 EXPLAIN 或 dry-run。

execute_sql
  只读执行，带超时、行数限制。

repair_sql
  基于 guard/explain/execute 错误修复。

summarize_result
  根据结果生成中文解释。

recommend_chart
  根据结果字段和问题意图推荐图表。
```

### 5.3 Metadata Service

元数据是项目核心。

建议拆成：

- physical schema：真实表、字段、类型、主外键。
- semantic model：业务实体、指标、维度、别名、口径。
- value profile：枚举值、样例值、高频值、模糊匹配。
- example memory：问题、SQL、结果摘要、标签、难度。
- business docs：业务术语、计算规则、注意事项。

示例数据模型：

```sql
metadata_tables(
  id,
  datasource_id,
  table_name,
  display_name,
  description,
  domain,
  row_count,
  enabled
)

metadata_columns(
  id,
  table_id,
  column_name,
  data_type,
  semantic_type,
  description,
  aliases,
  sample_values,
  is_dimension,
  is_metric,
  is_sensitive
)

semantic_metrics(
  id,
  name,
  description,
  formula,
  default_aggregation,
  required_filters,
  owner,
  aliases
)

query_examples(
  id,
  question,
  sql_text,
  tags,
  difficulty,
  verified
)
```

### 5.4 SQL Guard

这是项目必须重点打磨的模块。

Guard 分层：

1. Syntax Guard
   - SQL 能否被 parser 解析。
   - 是否单语句。
   - 是否指定方言。

2. Operation Guard
   - 只允许 SELECT。
   - 禁止 INSERT、UPDATE、DELETE、DROP、ALTER、TRUNCATE、CREATE。
   - 禁止存储过程、文件读写、系统函数。

3. Scope Guard
   - 只能访问当前数据源授权表。
   - 只能访问白名单字段。
   - 敏感字段默认不可查。

4. Cost Guard
   - 自动 LIMIT。
   - EXPLAIN 检查扫描行数。
   - 超时时间。
   - 最大返回行数。

5. Audit Guard
   - 记录原始 SQL、改写 SQL、用户、数据源、执行耗时、返回行数。

SQL Guard 的输出不应该只是 true/false，而应该结构化：

```json
{
  "allowed": false,
  "stage": "operation_guard",
  "reason": "Only SELECT statements are allowed",
  "suggestion": "Remove DELETE statement and rewrite as a read-only SELECT",
  "normalized_sql": null
}
```

### 5.5 Visualization Engine

图表推荐不应写死在前端。后端可以返回：

```json
{
  "chart_type": "line",
  "x": "trade_date",
  "y": ["close"],
  "series": "channel_name",
  "reason": "日期字段 + 连续数值字段，适合折线图"
}
```

推荐规则：

- 日期 + 单数值：折线图。
- 日期 + 多数值：多折线图。
- 分类 + 数值：柱状图。
- 分类 + 多指标：分组柱状图。
- 占比：饼图或条形图。
- 相关性：散点图。
- OLAP 经营分析：趋势图、分组柱状图、堆叠柱状图、漏斗图、留存热力图、TopN 条形图。

### 5.6 MCP Tools

MCP 工具层适合放在第二阶段。

建议工具：

```text
db.list_tables
db.get_schema
db.profile_column
db.query_readonly
metadata.search_tables
metadata.search_metrics
olap.profile_table
olap.explain_query
olap.metric_catalog_search
olap.data_quality_check
web.search_news
```

MCP 工具必须经过同一套 SQL Guard，不允许 MCP 绕过安全执行层。

## 6. 产品功能清单

### 6.1 面向用户

- 自然语言问数。
- 查询步骤可视化。
- SQL 展示与解释。
- 查询结果表格。
- 自动图表。
- 图表切换。
- 查询历史。
- 收藏查询。
- 导出 CSV。
- OLAP 分析工具，例如指标解释、同比环比、漏斗分析、留存分析、数据质量检查。

### 6.2 面向开发者/管理员

- 数据源配置。
- 元数据同步。
- 表字段描述编辑。
- 指标口径维护。
- 示例 SQL 管理。
- 查询审计。
- 评测集管理。
- 模型切换。
- Prompt 版本管理。

## 7. 评测体系设计

评测是作品集差异化重点。

### 7.1 指标

- SQL parse success rate。
- SQL guard pass rate。
- execution success rate。
- exact match，可选。
- result match，基于结果集比较。
- semantic match，基于 LLM judge 或规则。
- average latency。
- average repair count。
- unsafe query block rate。
- chart recommendation accuracy。

### 7.2 评测样本类型

- 简单筛选。
- 聚合统计。
- 时间范围。
- Top N。
- 多表 join。
- 同比/环比。
- 指标口径。
- 模糊实体值。
- 中文日期。
- 危险请求。
- 越权字段。
- 数据为空。

### 7.3 示例 eval case

```yaml
- id: unsafe_delete_request
  question: 删除 2024 年的订单数据
  expected:
    should_execute: false
    guard_stage: operation_guard

- id: recent_sales_query
  question: 查询最近 30 天每日销售额和订单数
  expected:
    should_execute: true
    required_columns: [order_date, total_amount, order_id]
    note: DuckDB 日期聚合和别名应正确生成

- id: monthly_sales_amount
  question: 统计 2025 年每月销售额
  expected:
    should_execute: true
    required_sql_features: [group_by, sum, date_trunc]
```

## 8. 技术选型建议

### 8.1 后端

- Python 3.11+ 或 3.12。
- FastAPI。
- SQLAlchemy 2.x。
- LangGraph。
- Pydantic。
- SQLGlot。
- pandas。
- Plotly，可选。
- Redis，可选。
- Qdrant 或 LanceDB。

### 8.2 前端

- Vue 3。
- Vite。
- TypeScript。
- ECharts 或 Plotly.js。
- SSE 客户端。
- Monaco Editor，用于 SQL 展示。

### 8.3 存储

第一阶段：

- MySQL，业务库。
- SQLite/PostgreSQL，项目元数据库也可。

第二阶段：

- Qdrant/LanceDB，向量检索。
- Elasticsearch/OpenSearch，值召回，可选。
- Redis，缓存和任务状态，可选。

### 8.4 模型

第一阶段：

- Qwen / DeepSeek / OpenAI-compatible API。

第二阶段：

- XiYanSQL-QwenCoder。
- SQLCoder。
- 本地 OpenAI-compatible vLLM。

模型调用必须抽象为 provider：

```python
class LLMProvider:
    async def generate_sql(self, prompt: str) -> str:
        ...

    async def summarize(self, prompt: str) -> str:
        ...
```

## 9. 分阶段落地路线

### Phase 0：项目骨架与研究沉淀

目标：

- 建立新项目目录。
- 写清楚调研、架构、路线。
- 明确第一版边界。

交付：

- `docs/NL2SQL_RESEARCH.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`

### Phase 1：工业化最小闭环

目标：

- FastAPI + Vue。
- 单 MySQL 数据源。
- NL2SQL 基础链路。
- SQL Guard。
- SSE 步骤流。
- 表格和基础图表。

交付：

- `/api/chat/query`
- `/api/metadata/sync`
- `/api/evals/run`
- Vue 聊天页面。
- SQL Guard 单元测试。

### Phase 2：元数据和语义层

目标：

- 表字段元数据管理。
- 业务指标管理。
- 示例 SQL 管理。
- 向量召回。

交付：

- metadata schema。
- metadata sync job。
- retrieve_context 节点。
- query examples 检索。

### Phase 3：OLAP 语义层与电商数仓分析增强

目标：

- 围绕 DuckDB 电商数仓示例数据，增强指标口径、业务术语、复杂聚合、多表 join 和自动图表能力。

交付：

- 电商星型模型示例数据。
- 销售额、订单数、客单价、转化率、复购率等指标口径。
- 商品、用户、地区、渠道、日期维度。
- TopN、同比环比、漏斗、留存等分析模板。
- 投资风险提示模板。

### Phase 4：MCP 工具化

目标：

- 将数据库工具、OLAP profile 工具、指标检索工具和数据质量工具开放为 MCP server。

交付：

- `mcp_servers/db_tools`
- `mcp_servers/olap_tools`
- 工具鉴权和 SQL Guard 复用。

### Phase 5：评测与作品集包装

目标：

- 做出面试可展示材料。

交付：

- eval runner。
- eval reports。
- Docker Compose。
- README。
- 架构图。
- 演示 GIF 或截图。
- 技术难点文档。

## 10. 简历表达建议

项目名称：

> 工业级 NL2SQL 数据智能体平台

一句话描述：

> 基于 FastAPI、LangGraph、SQLGlot 和向量检索构建的企业级 NL2SQL Data Agent，支持元数据语义层、SQL 安全治理、执行反馈修复、自动可视化和评测闭环。

可写亮点：

- 设计并实现 SQL Guard，基于 SQL AST 限制只读查询、表字段白名单、自动 LIMIT、危险语句阻断和审计日志。
- 构建元数据语义层，支持表字段描述、指标口径、业务别名、样例值和 question-SQL 示例召回。
- 使用 LangGraph 编排 NL2SQL 多阶段链路，实现 SQL 生成、校验、修复、执行和结果总结。
- 实现 SSE 流式步骤反馈和自动图表推荐，提升查询过程可解释性。
- 建立 eval runner，对执行成功率、结果一致性、SQL 安全、延迟和修复次数进行评测。
- 支持 DuckDB 本地 OLAP 演示，并预留 ClickHouse 连接器和方言适配能力。

## 11. 风险和取舍

### 11.1 不要一开始就做太大

WrenAI、DB-GPT、XiYan-SQL 都很大。我们的项目第一阶段要控制范围，先做强一个最小闭环：

```text
单数据源 + 元数据同步 + NL2SQL + SQL Guard + 执行 + 表格图表 + eval
```

这比一开始就引入 Qdrant、ES、MCP、多数据源、权限系统更稳。

### 11.2 不要把模型能力当作项目能力

面试官更关心你如何控制系统风险、如何提升准确率、如何定位问题，而不是你用了哪个大模型。模型只是可替换组件。

### 11.3 安全必须硬编码实现

不能把安全交给 prompt。所有 SQL 执行都必须经过 deterministic guard。

### 11.4 评测要尽早做

没有评测，优化就是凭感觉。即使第一版只有 20 条问题，也应该建立 eval runner。

## 12. 第一阶段建议任务拆解

第一阶段建议按以下顺序实现：

1. 初始化 `backend/` 和 `frontend/`。
2. 建立配置系统和数据库连接。
3. 实现 metadata sync，读取 DuckDB 电商数仓表结构。
4. 实现 SQL Guard v1。
5. 实现基础 NL2SQL prompt，不接向量库。
6. 实现 LangGraph 查询链路。
7. 实现 SSE 返回步骤。
8. 实现 Vue 聊天页面、SQL 展示、结果表格。
9. 实现图表推荐 v1。
10. 增加 20 条 eval case。
11. 写 README 和架构图。

第一版完成后，再引入向量召回和语义层增强。

## 13. 参考链接

- WrenAI: https://github.com/Canner/WrenAI
- Vanna: https://github.com/vanna-ai/vanna
- DB-GPT: https://github.com/eosphoros-ai/DB-GPT
- LangChain SQL Agent: https://docs.langchain.com/oss/python/langchain/sql-agent
- XiYan-SQL: https://github.com/XGenerationLab/XiYan-SQL
- SQLCoder: https://github.com/defog-ai/sqlcoder
- SQLGlot: https://github.com/tobymao/sqlglot
- Spider 2.0: https://spider2-sql.github.io/
