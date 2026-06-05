# Agent Workflow Design

## 目标

Agent Workflow 负责把“用户问题”变成“安全执行后的分析结果”。设计重点不是让模型自由行动，而是把 NL2SQL 拆成可观测、可测试、可修复的节点。

核心文件：

- `backend/app/agent/state.py`
- `backend/app/agent/nodes.py`
- `backend/app/agent/repair.py`
- `backend/app/agent/workflow.py`
- `backend/app/api/chat.py`

## 状态模型

`AgentState` 是工作流唯一状态容器，记录 question、datasource、retrieval_result、schema_context、olap_hint、sql、guard_result、query_result、explainability、repair_history、plan_hints、summary、chart_recommendation、completed_steps 和 error。状态扁平、字段显式，方便单测和 SSE 序列化。

## Workflow 拆分

```text
iter_pre_repair_workflow
  datasource_selected
  intent_guard
  retrieve_context
  build_context
  olap_detected
  generate_sql

iter_sql_repair_events
  sql_guard
  execute
  repair_sql (optional, max 2)

finalize_workflow
  explain_performance
  summarize
  recommend_chart
```

流式 API 和同步测试入口共用这三段逻辑。

## Pre-repair 节点

### datasource_selected

从 `DataSourceManager` 获取当前 datasource：name、dialect、display_name。如果数据源不可用，在第一步停止，避免后续节点拿错误方言生成 SQL。

### intent_guard

在 SQL 生成前做轻量中文意图拦截：删除、清空、修改、插入、建表、删表、外部文件读取。这是用户意图层拦截；SQL Guard 仍然是执行前硬边界。

### retrieve_context

调用 `retrieve_metadata_assets(question, datasource_name)`，召回 tables、columns、metrics、aliases、verified queries 和 value hits。

### build_context

基于 retrieval result 构建 focused schema context。如果没有 retrieval result，可 fallback 到 question-based context builder。

### olap_detected

用 deterministic regex 检测 OLAP intent，例如 topn、distribution/share、yoy/mom、moving average。检测结果生成 `olap_hint`，进入 SQL generation 和 repair prompt。

### generate_sql

构造 `SQLGenerationRequest`：

```text
question
schema_context
datasource_name
datasource_dialect
olap_intents
olap_hint
repair context (optional)
```

Provider 可以是 Mock 或 DeepSeek。生成后做 SQL postprocess，例如去 markdown fence、处理常见字段别名。

## Guard + Execute + Repair

`iter_sql_repair_events` 是受控循环：

```text
while true:
  sql_guard
  if rejected:
    if repairable and repair_count < 2:
      repair_sql
      continue
    error
  execute
  if execution failed:
    if repairable and repair_count < 2:
      repair_sql
      continue
    error
  success
```

可修复 Guard stage：

- `syntax_guard`
- `scope_guard`
- `function_guard`
- `fanout_guard`
- `cost_guard`

不可修复：

- `operation_guard`

执行错误只修 parser/catalog/binder 类错误；连接、timeout、内存等基础设施错误不让模型修。

## Finalize 阶段

执行成功后统一进入 `finalize_workflow`：

- `explain_performance_node`：ClickHouse EXPLAIN / runtime stats / plan hints。
- `summarize_node`：生成中文结果摘要。
- `recommend_chart`：根据 columns、rows、OLAP intent 推荐 line/bar/pie/dual_axis/table。

这个函数同时被 `run_query_workflow` 和 SSE API 调用，避免同步测试和线上流式行为分叉。

## SSE 事件

前端通过 SSE 展示步骤流：

```text
datasource_selected
intent_guard
retrieve_context
build_context
olap_detected
generate_sql
sql_guard
repair_sql (optional)
execute
explain_plan
summarize
recommend_chart
done
```

每个事件带结构化 payload，前端可以展示 SQL、Guard 结果、repair history、query result、chart recommendation 和 explainability。

## 依赖注入

关键节点都支持替换依赖：

- provider
- retriever
- schema_context_builder
- scope_builder
- executor

这让单元测试可以绕开真实 LLM 和真实数据库，精确验证节点行为。

## 与 MCP 的关系

MCP 不复用整个 Agent workflow，而是复用底层能力：

- schema/list 工具复用 metadata service。
- query_readonly 复用 `guard_sql + execute_guarded_sql`。
- explain_query 复用 connector explain 和 performance hints。

这样 MCP 是外部工具入口，不改变主 Agent 链路。

## 面试讲法

> 我没有做一个黑盒 Agent，而是把问数链路拆成确定性节点。每一步都能在 SSE 中展示，也能在测试里替换依赖。模型只负责生成候选 SQL；检索、Guard、执行、修复、解释和图表推荐都有明确模块边界。
