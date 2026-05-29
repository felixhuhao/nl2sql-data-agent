# Industrial NL2SQL Data Agent Platform

面向 OLAP 数据仓库的工业级 NL2SQL 数据分析智能体平台。

## 项目主题

本项目不是金融分析助手，也不是单一业务 Demo，而是一个面向企业经营分析和 OLAP 数仓场景的通用 NL2SQL 平台。

第一阶段采用：

- DuckDB 作为本地 OLAP 数据库。
- 示例电商数仓数据集作为演示和评测数据。
- FastAPI + Vue 作为前后端架构。
- LangGraph 编排 NL2SQL 查询链路。
- SQLGlot 构建 SQL Guard。

后续阶段扩展：

- ClickHouse 数据源。
- DuckDB / ClickHouse 方言适配。
- OLAP 查询性能治理。
- MCP 工具化。

## 一句话定位

基于 FastAPI、LangGraph、SQLGlot 和 DuckDB/ClickHouse 构建的工业级 NL2SQL Data Agent，支持元数据语义层、指标口径管理、SQL 安全治理、执行反馈修复、自动可视化和评测闭环。

## 第一阶段数据域

使用电商数仓作为示例业务域，覆盖典型 OLAP 分析场景：

- 销售分析：销售额、订单数、客单价、同比、环比。
- 用户分析：新增用户、活跃用户、复购率、留存。
- 商品分析：品类销售额、SKU 销量、毛利率、TopN。
- 渠道分析：渠道销售额、转化率、ROI。
- 区域分析：省份、城市、大区贡献。
- 时间分析：日、周、月、季度趋势。

建议第一版数据模型：

```text
fact_orders
fact_order_items
fact_user_events
dim_users
dim_products
dim_regions
dim_channels
dim_date
```

## 当前 Scope

- DuckDB 本地数据源。
- 电商数仓示例数据生成。
- 元数据同步。
- 轻量语义层。
- NL2SQL Agent 工作流。
- SQL Guard。
- 只读 SQL 执行。
- SSE 查询步骤流。
- 表格和图表展示。
- Eval Runner。

## 暂不纳入 Scope

- 金融/股票分析插件。
- 训练专用 NL2SQL 模型。
- 多租户权限系统。
- 企业 SSO。
- 复杂 BI Dashboard 编辑器。
- 完整 MCP 工具生态。
- Elasticsearch 值召回。

这些能力可以后续扩展，但不进入第一版目标。

## 核心工程卖点

1. 面向 OLAP，而不是普通 CRUD 数据库。
2. 不直接执行 LLM 生成 SQL，所有 SQL 必须经过 SQL Guard。
3. 不把全量 schema 粗暴塞进 prompt，而是通过元数据和语义层构建上下文。
4. 查询链路可观测，前端展示步骤、SQL、结果和图表。
5. 通过 eval runner 量化准确率、安全拦截率、执行成功率和延迟。
6. 第一版轻量可运行，后续可平滑扩展 ClickHouse。

## 文档

- [NL2SQL_RESEARCH.md](docs/NL2SQL_RESEARCH.md)
- [ROADMAP.md](docs/ROADMAP.md)
