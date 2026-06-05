# Interview Pitch

> 目标：让面试官在 3 分钟内理解这个项目不是 Text-to-SQL Demo，而是一个有安全边界、语义层和评测闭环的工业级 NL2SQL Data Agent。

## 30 秒版本

我做的是一个面向 OLAP 数仓的 NL2SQL Data Agent。它不是直接把用户问题丢给大模型生成 SQL，而是先用 metadata semantic layer 检索可信表、字段、指标和 verified queries，构建 focused schema context，再生成 SQL。所有 SQL 在执行前必须经过基于 SQLGlot AST 的 SQL Guard，做只读限制、表字段白名单、危险函数拦截、fanout 风险检查和自动 LIMIT。执行失败或 Guard 拒绝时，系统会进入受控 SQL repair loop，修复后的 SQL 仍然重新过 Guard。最后用 eval runner 同时验证 mock 回归、真实 DeepSeek 表现、retrieval 命中、图表推荐和安全拦截。

## 3 分钟讲法

### 1. 问题定义

企业里的 NL2SQL 难点不只是“模型会不会写 SQL”。真正的问题有三个：

- 模型不知道哪些表和指标可信。
- 模型可能生成危险 SQL，系统不敢直接执行。
- 出错后很难定位是检索错、SQL 错、执行错，还是评测器误判。

所以我把项目设计成一条工业化闭环：

```text
Question
  -> metadata retrieval
  -> focused schema context
  -> SQL generation
  -> SQL Guard
  -> read-only execution
  -> repair / explain / chart
  -> eval report
```

### 2. 架构主线

系统分成四个核心层：

- **Semantic Layer**：SQLite 落库表、字段、join relationship、metric、alias、analysis space、verified query。查询时不临场扫库，只消费已同步 metadata。
- **Agent Workflow**：节点化处理 datasource selection、intent guard、retrieval、context build、OLAP intent、SQL generation、SQL Guard、execute、repair、summary、chart。
- **SQL Guard**：所有 SQL 执行前必须通过确定性检查。Guard 基于 SQLGlot AST，不依赖 prompt；MCP 工具也复用同一条 Guard 路径。
- **Evaluation**：mock smoke 保证回归稳定，DeepSeek real eval 验证真实模型；报告按错误类型拆解，支持 result equivalence，避免把语义等价 SQL 误判失败。

### 3. 最值得展开的技术点

**SQL Guard 是承重墙。**
模型输出不会直接执行。`guard_sql` 会解析 SQL，拒绝非 SELECT、DDL/DML、外部文件函数、越权表字段、ClickHouse 危险命令，并自动补 LIMIT。`execute_guarded_sql` 对 `allowed=False` 直接抛错，所以没有绕过路径。

**Semantic Layer 提升准确率和可控性。**
系统把 schema introspection 和业务语义分开：物理 schema 来自 DuckDB/ClickHouse introspection，业务语义来自 metric/alias/verified query/analysis space。检索后只把相关资产写入 prompt，mock smoke 里 focused context 相比 full schema 大约减少 75%。

**评测闭环让系统可迭代。**
Eval 不只看 pass/fail，还记录 retrieval hit、focused context、Guard stage、repair count、chart type、plan hints、error category。真实 DeepSeek eval 用参考 SQL 的执行结果做等价判断，避免列别名和 SQL 形状差异造成假失败。

### 4. 当前可展示结果

- Mock smoke：50/50 通过。
- DeepSeek real eval：18/18 通过（DuckDB real cases）。
- 后端测试：328 passed。
- 支持 DuckDB + ClickHouse connector、方言感知 SQL Guard、ClickHouse EXPLAIN 性能提示。
- MCP 工具复用后端 Guard 和只读执行器，外部 Agent 也不能绕过安全边界。

## 面试官可能追问

### 为什么不直接把全量 schema 塞进 prompt？

全量 schema 简单但不可控：prompt 长、无可信资产边界、模型容易误用表字段。这个项目把 schema 和语义资产落库，通过 retrieval 构建 focused context，并由 Analysis Space 限定可问资产。这样 prompt 更小，Guard scope 也能和语义空间保持一致。

### SQL Guard 和 prompt 约束有什么区别？

Prompt 是软约束，只能影响模型倾向；SQL Guard 是执行前的硬边界。即使模型生成 `DELETE`、`DROP`、`read_csv` 或越权字段，Guard 也会在执行前拒绝。修复后的 SQL 也必须重新过 Guard。

### 为什么需要 SQL repair？

真实模型常见错误是字段名、方言函数、join 路径或 fanout 聚合问题。如果一错就失败，体验很差。Repair loop 只修可修复错误，最多两次，并保留完整历史；破坏性操作不可修复，直接拒绝。

### 真实模型评测为什么要做 result equivalence？

同一个问题可以有多种等价 SQL，例如列别名不同、join 写法不同、是否多带非核心 ID 列。只做 SQL 字符串或严格列名比较会误杀。现在 real eval 会执行参考 SQL 和模型 SQL，比较结果集，mock eval 仍保持严格，兼顾真实准确率和回归稳定性。

### MCP 工具为什么是亮点？

Phase 7 不是重写一套工具，而是把已有 schema、query、explain、metric search 以 MCP 暴露给外部 Agent。关键是 MCP 的 `query_readonly` 仍然走同一个 `guard_sql + execute_guarded_sql`，所以工具生态扩展没有产生第二条不安全执行路径。

## 简历 Bullet 候选

- 设计并实现工业级 NL2SQL Data Agent，基于 FastAPI、Vue、SQLGlot、DuckDB/ClickHouse 构建从自然语言问数到 SQL 生成、Guard 校验、只读执行、图表推荐和结果解释的完整闭环。
- 构建 DB-backed metadata semantic layer，支持 schema introspection、metric/alias/verified query、analysis space、规则+向量混合召回，将 prompt schema context 压缩约 75%，提升 NL2SQL 可控性。
- 实现确定性 SQL Guard，基于 SQLGlot AST 做 SELECT-only、表字段白名单、危险函数/命令拦截、fanout 风险检查、自动 LIMIT 和多数据源方言适配，所有 HTTP/MCP 执行路径统一复用。
- 实现受控 SQL repair loop，对 Guard 拒绝和执行错误进行最多两轮修复，修复后重新经过完整 Guard；区分可修复错误和不可修复危险操作，保证安全边界不降级。
- 建立 smoke eval 和 real LLM eval 体系，覆盖 50 条 DuckDB mock case、25 条 ClickHouse/OLAP case、DeepSeek real eval、错误归因、retrieval 命中率、图表推荐和 result equivalence 校验。
- 将核心能力封装为 MCP 只读工具，提供 schema 查询、guarded SQL、EXPLAIN 和指标检索，外部 Agent 调用仍继承后端 SQL Guard 和只读连接约束。

## 推荐现场 Demo

1. 问：“查询最近30天每日销售额和订单数”，展示步骤流、SQL、表格、折线图和解释信息。
2. 问：“最近30天销量最高的10个商品”，展示 TopN bar chart、命中表字段和 join path。
3. 问：“删除2024年的订单数据”，展示 intent/SQL Guard 拦截。
4. 切换 ClickHouse 数据源，问一个最近30天渠道销售额，展示方言 SQL、耗时和性能提示。
5. 运行 `python scripts/run_smoke_eval.py` 或展示 `deepseek_latest.md`，说明评测闭环。
