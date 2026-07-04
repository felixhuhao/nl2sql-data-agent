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

`AgentState` 是工作流唯一状态容器，记录 question、datasource、`locale`、retrieval_result、`retrieval_coverage`、schema_context、olap_hint、sql、guard_result、query_result、explainability、repair_history、plan_hints、summary、chart_recommendation、completed_steps 和 error。状态扁平、字段显式，方便单测和 SSE 序列化。`locale`（默认 `DEFAULT_LOCALE="zh"`）在 API 边界解析（`Accept-Language` + 请求 `locale` 覆盖），只影响输出呈现，不影响 schema context、prompt 或生成的 SQL。

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

在 conversation-merged 召回集上计算 `RetrievalCoverage`（hybrid：match strength × structural joinability），再构建 focused schema context。两段式恢复（`RETRIEVAL_EXPANSION_ENABLED` / `RETRIEVAL_FALLBACK_MODE`，**默认 on**，经 vector-active 校准）：coverage 为 `low` 时先做确定性 graph expansion（`MetaRelationship` 1-hop 双向、fanout-gated、capped、受 analysis space 约束）并 re-score；仍 `low` 且 full schema 在 budget 内则回退 full schema，否则用扩展后的 focused context。空召回始终回退 full schema（历史不变量）。coverage 写入 `state.retrieval_coverage`（见 `docs/METADATA_SEMANTIC_LAYER.md`）。

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
- `summarize_node`：按 `state.locale` 经 i18n resolver `t(key, locale, …)` 生成结果摘要（默认 `zh`，输出与历史一致；`en` 返回英文）。
- `recommend_chart`：根据 columns、rows、OLAP intent 推荐 line/bar/pie/dual_axis/table。

这个函数同时被 `run_query_workflow` 和 SSE API 调用，避免同步测试和线上流式行为分叉。

## SSE 事件

前端通过 SSE 展示步骤流：

```text
datasource_selected
intent_guard
retrieve_context
build_context (carries retrieval_coverage: score/band/expanded/fallback_used)
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

## 手工 vs Agentic 步骤审计

LLM 只触及 3 个环节，其余 11 步是确定性代码。这是刻意收窄的 LLM 面，也是可测试/可审计/安全性的来源。

| 步骤 | 分类 | 决策方式 |
|---|---|---|
| datasource_selected | 🔧 Manual | connector registry 查找 |
| intent_guard | 🔧 Manual | 正则 + token 模式（中英文写操作、外部文件读取） |
| retrieve_context | 🔧 Manual + 🧮 embedding | 规则召回（确定性）+ 可选向量召回（embedding 模型，非 LLM） |
| build_context | 🔧 Manual | focused context 模板渲染 |
| olap_detected | 🔧 Manual | 正则意图检测 |
| **generate_sql** | 🤖 **Agentic (LLM)** | `provider.generate_sql` |
| sql_guard | 🔧 Manual | sqlglot AST scope/syntax/function/fanout/cost 检查 |
| conversation_filter_verify | 🔧 Manual | carried filter 集合 diff |
| **repair_sql** | 🤖 **Agentic (LLM)** | 带 repair context 的 `provider.generate_sql` |
| **semantic_guard** | 🤖 **LLM + 🔧 确定性审计** | LLM 提取概念并判断 grounding，但确定性 refutation auditor 用 `DISTINCT` probe 佐证后才能在 enforce 下拦截 |
| execute | 🔧 Manual | 执行受 Guard 的 SQL |
| explain_performance | 🔧 Manual | EXPLAIN 解析 + sqlglot |
| summarize | 🔧 Manual | **f-string 模板**（非 LLM）：`查询返回 N 行，字段：…` |
| recommend_chart | 🔧 Manual | 规则推荐器 |

要点：LLM 实质只做「text→SQL」（生成 + 其修复）；`summarize` 是模板而非 LLM 生成；`retrieve_context` 的向量步是 embedding 相似度而非 LLM 判断；`semantic_guard` 的 LLM 判断被真实数据 gate，不能凭一己之见拦截。

## 何处应引入 Agentic 推理（roadmap）

市场研究（schema-linking、CHASE-SQL、PV-SQL、歧义 intent、表格叙述保真度）指向的结论：安全/执行路径应保持确定性，accuracy 推理才是 LLM 值得扩张的地方。以下措辞为**权威版本**，已修正早期过度声称。

按优先级：

1. **Schema expansion + verifier，recall-first，带 full-schema fallback**（不是 re-ranker）。
   - re-ranker 只能对已召回的东西重排，无法找回漏掉的表。正确做法是**扩召回**：先做确定性图扩展（沿 `MetaRelationship` 的 FK/关系边从已召回表扩展，利用 confidence/fanout），再用 LLM 补语义缺口，schema 体量可容纳时回退到 full-schema。
   - 现有 full-schema fallback 仅在召回**为空**时触发；应改为在召回**低置信**时也触发（这才是真实风险场景）。
   - 依据：schema-linking 的「上下文一旦漏召回即不可逆丢失」风险，以及《Death of Schema Linking?》中「强模型下 full-schema 可与 schema linking 持平」的 caveat。

2. **generate_sql 升级为 multi-candidate + verification-aware selection**（不是 prompt-only selector）。
   - 证据支持的是「候选多样性 + 验证感知选择」，不是「N 个候选 + LLM 投票」。CHASE-SQL 的 73%（BIRD）来自多路生成 + value retrieval + query fixer + 微调 pairwise selector。
   - 本项目**天然适配**：guard 与 executor 是确定性的且已在链路中，可近乎免费地作为选择信号（guard 通过、执行干净、probe 一致）。当前 repair 循环本质是退化的单候选版本，推广到 N 候选用执行/guard 信号选择，是很小的架构改动。

3. **歧义检测 + 解释枚举，仅在必要时澄清**（gated clarification）。
   - 依据《Reasoning About Intent for Ambiguous Requests》：模型在 text-to-SQL 上常漏掉有效解读，但**直接枚举解读可优于开放式澄清管道**。故：检测歧义 → 枚举可能解读（限定在 active analysis space 内）→ 仅当置信度低或治理要求时才向用户澄清。
   - 该步较轻（一次分类 pass），可与 1/2 并行原型，不必严格排第三。

4. **LLM 结果叙述，后置且受确定性保真检查约束**（guarded narration，非 free UX）。
   - 依据《Can LLMs Narrate Tabular Data?》：LLM 摘要可能丢失/扭曲表格事实。故保留现有确定性模板作为**保真基准**，LLM 只在已计算好的聚合/行数之上叙述，带模板 fallback。

不做 agentic（研究亦支持保持确定性）：`intent_guard`、`sql_guard`、`execute`——安全与执行边界的确定性本身就是保证；PV-SQL 亦是「DB probing + 规则校验」而非信任 LLM judge。`semantic_guard` 已是正确的混合形态，其增强方向是把单次 DISTINCT 检查扩成 **probe → 派生约束 → 复验**的迭代 loop（可建立在 `promoted_patterns` 上），而非「等同 PV-SQL」。

## 技术说明

> 我没有做一个黑盒 Agent，而是把问数链路拆成确定性节点。每一步都能在 SSE 中展示，也能在测试里替换依赖。模型只负责生成候选 SQL；检索、Guard、执行、修复、解释和图表推荐都有明确模块边界。
