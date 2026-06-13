# Phase 5: SQL 修复与执行反馈闭环 设计文档

> 日期: 2026-05-31
> 状态: 设计修订完成，待实现
> 前置: Phase 4 code complete；自动化测试通过；真实 Qdrant + 默认 MiniLM 手动验收 pending
> 2026-06-13 更新: 当前向量默认模型为 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，Docker 使用 CPU-only PyTorch；不需要 CUDA 或外部模型挂载。
> 范围: SQL 修复闭环、执行错误重试、修复可观测展示、修复评测

---

## Context

Phase 1-4 已经形成完整 NL2SQL 查询链路：意图检查 -> 检索上下文 -> 构建 Schema -> 生成 SQL -> SQL Guard -> 执行 -> 摘要。当前链路是单次执行：SQL Guard 拒绝或 DuckDB 执行失败后直接报错给用户，没有自动修复能力。

Phase 4 的向量召回代码已经完成，但真实 Qdrant + 默认 MiniLM 手动测试因本机资源占用暂缓。Phase 5 不依赖真实向量效果验收；默认要求在 `VECTOR_ENABLED=false` 或索引不可用时仍保持 Phase 3/4 的规则链路可用。

实际场景中 LLM 生成 SQL 经常有可修复的小错误：

- Guard 层面：scope 错误、syntax 错误、function 错误、fanout 风险、cost guard 错误
- 执行层面：DuckDB Parser/Catalog/Binder/InvalidInput 类错误，例如列不存在、函数参数错误、语法细节错误

Phase 5 的目标是在现有链路中插入受控修复闭环，提高查询成功率，同时不降低 SQL Guard 的安全等级。

---

## 1. 关键决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 修复触发点 | SQL Guard 拒绝 + DuckDB 执行失败 | 两个是最常见可修复失败点；intent_guard 拒绝不修复 |
| 修复编排位置 | 新增共享 repair engine | `chat.py` 和 eval runner 必须复用同一套修复逻辑，避免前端能修、eval 测不到 |
| 修复方式 | 将结构化错误信息注入 LLM prompt 重新生成 | 灵活处理表名、列名、语法、fanout 等问题 |
| 最大修复次数 | 2 次 | ROADMAP 要求；防止无限循环和延迟失控 |
| 修复后安全 | 修复后的 SQL 必须重新走完整 SQL Guard | 不跳过 scope/function/fanout/cost 任一阶段 |
| Mock 策略 | MockLLMProvider 本身不修复；测试使用 ScriptedRepairProvider 或 `mock_repair_sqls` | Mock 保持确定性，同时可以覆盖修复闭环 |
| 状态清理 | 每次重新 guard/execute 前清理瞬时失败状态 | 避免第一次失败留下的 `stopped_at/error` 影响后续成功判断 |
| 修复记录 | `AgentState.repair_history` 记录每次尝试 | 前端展示、eval 统计、错误解释都依赖同一份历史 |

### 1.1 不修复的场景

- **intent_guard 拒绝**：用户明确说删除、建表、导入外部文件等，直接拒绝
- **operation_guard 拒绝**：LLM 生成 DELETE/UPDATE/CREATE/DROP/TRUNCATE 等，修复风险大于收益
- **基础设施错误**：数据库连接失败、OOM、网络错误、模型超时等，不尝试 SQL 修复
- **修复 2 次仍失败**：返回最终错误和 repair history

### 1.2 可修复的场景

- **scope_guard 拒绝**：引用了不在白名单的表/列，改用 schema context 中允许的表/列
- **syntax_guard 拒绝**：SQL 语法错误、UNION 等可改写为单 SELECT 的情况
- **function_guard 拒绝**：LLM 幻觉使用 `read_csv/read_parquet` 等外部函数时，改为使用已有表
- **fanout_guard 拒绝**：聚合被 join 膨胀的列，改用正确事实表或明细金额列
- **cost_guard 拒绝**：LIMIT 非整数、负数等可修复成本约束
- **执行失败**：DuckDB Parser/Catalog/Binder/InvalidInput 类错误

---

## 2. 架构设计

### 2.1 修复闭环流程

```
intent_guard_node
  ↓
retrieve_context_node
  ↓
build_context_node
  ↓
generate_sql_node
  ↓
repair engine:
  ├─ guard attempt
  │    ├─ blocked + repairable -> repair_sql_node -> guard attempt
  │    ├─ blocked + not repairable -> error
  │    └─ passed -> execute attempt
  ├─ execute attempt
  │    ├─ failed + repairable -> repair_sql_node -> guard attempt
  │    ├─ failed + not repairable -> error
  │    └─ passed -> summarize
  └─ max repairs exhausted -> error
```

### 2.2 共享 Repair Engine

新增 `backend/app/agent/repair.py`，集中放三类逻辑：

- repairability 判断：`is_guard_repairable()`, `is_execution_repairable()`
- repair state/event 数据结构：`SQLRepairContext`, `RepairAttempt`, `RepairEvent`
- repair loop：`iter_sql_repair_events(...)`

`chat.py` 只负责把 `RepairEvent` 转成 SSE；`run_smoke_eval.py` 消费同一批 `RepairEvent` 来统计 `repair_count`、最终 SQL、最终错误。

伪代码：

```python
def iter_sql_repair_events(
    state: AgentState,
    *,
    provider: LLMProvider,
    scope_builder: ScopeBuilder,
    executor: SQLExecutor,
    max_repairs: int = 2,
) -> Iterator[RepairEvent]:
    repair_count = 0

    while True:
        reset_failure_state(state)
        sql_guard_node(state, scope_builder=scope_builder)
        yield RepairEvent(step="sql_guard", state=state)

        if state.guard_result and state.guard_result.allowed:
            try:
                execute_node(state, executor=executor)
                yield RepairEvent(step="execute", state=state)
                return
            except Exception as exc:
                if not is_execution_repairable(exc) or repair_count >= max_repairs:
                    yield RepairEvent(step="error", error=exc, state=state)
                    return
                repair_context = SQLRepairContext.from_execution_error(...)
        else:
            if not is_guard_repairable(state.guard_result) or repair_count >= max_repairs:
                yield RepairEvent(step="error", state=state)
                return
            repair_context = SQLRepairContext.from_guard_result(...)

        repair_count += 1
        repair_sql_node(state, provider=provider, repair_context=repair_context, attempt=repair_count)
        yield RepairEvent(step="repair_sql", state=state, attempt=repair_count)
```

### 2.3 状态清理规则

每次 repair 后重新进入 guard/execute 前，必须清理瞬时失败字段：

```python
def reset_failure_state(state: AgentState) -> None:
    state.error = None
    state.stopped_at = None
    state.guard_result = None
    state.query_result = None
    state.summary = None
    state.explainability = None
    state.execution_error = None
```

原因：当前 `sql_guard_node()` 失败时会设置 `stopped_at="sql_guard"`，如果不清理，后续修复 SQL 即使通过 Guard，也可能被旧状态误判成失败。

### 2.4 修复判断逻辑

```python
NON_REPAIRABLE_GUARD_STAGES = {"operation_guard"}
REPAIRABLE_GUARD_STAGES = {
    "scope_guard",
    "syntax_guard",
    "function_guard",
    "fanout_guard",
    "cost_guard",
}

def is_guard_repairable(guard_result: GuardResult | None) -> bool:
    return bool(guard_result and guard_result.stage in REPAIRABLE_GUARD_STAGES)

def is_execution_repairable(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if any(token in name or token in message for token in ("outofmemory", "connection", "timeout")):
        return False
    return any(token in name or token in message for token in ("parser", "catalog", "binder", "invalidinput"))
```

实际实现可以根据 DuckDB 异常类型逐步收紧，不在 Phase 5 首版做过度分类。

---

## 3. Prompt 与数据结构

### 3.1 SQLRepairContext

不要传裸字符串 `repair_context`。使用结构化对象，便于测试和前端/eval 复用。

```python
@dataclass(frozen=True)
class SQLRepairContext:
    attempt: int
    original_sql: str
    error_stage: str       # "sql_guard" | "execute"
    error_kind: str        # guard stage or exception class name
    error_reason: str
    normalized_sql: str | None = None
```

### 3.2 SQLGenerationRequest 扩展

```python
@dataclass(frozen=True)
class SQLGenerationRequest:
    question: str
    schema_context: str
    repair: SQLRepairContext | None = None
```

`build_sql_generation_messages()` 根据 `request.repair` 决定普通生成模式还是修复模式。

### 3.3 修复 Prompt 设计

修复模式使用多轮对话：

```python
[
    {"role": "system", "content": _system_prompt()},
    {
        "role": "user",
        "content": f"Schema context:\n{request.schema_context}\n\nQuestion:\n{request.question}",
    },
    {"role": "assistant", "content": request.repair.original_sql},
    {
        "role": "user",
        "content": (
            "The previous SQL attempt failed.\n"
            f"Attempt: {request.repair.attempt}\n"
            f"Error stage: {request.repair.error_stage}\n"
            f"Error kind: {request.repair.error_kind}\n"
            f"Error reason: {request.repair.error_reason}\n\n"
            "Fix the SQL using only the provided schema context.\n"
            "Return corrected SQL only."
        ),
    },
]
```

fanout 修复时额外加入固定提示：

```text
If the error is fanout_guard, do not aggregate fact_orders.payment_amount after joining fact_order_items. For product/category sales, use fact_order_items.item_amount.
```

### 3.4 AgentState 扩展

```python
@dataclass
class AgentState:
    # ... 现有字段不变 ...
    execution_error: str | None = None
    repair_history: list[dict] = field(default_factory=list)
```

`repair_history` 条目为 JSON-safe dict：

```python
{
    "attempt": 1,
    "original_sql": "...",
    "repaired_sql": "...",
    "error_stage": "sql_guard" | "execute",
    "error_kind": "scope_guard" | "CatalogException",
    "error_reason": "...",
    "normalized_sql": "...",
    "succeeded": true | false | None,
    "final_stage": "sql_guard" | "execute" | None,
}
```

`succeeded` 可在下一轮 guard/execute 后回填；首版如果回填复杂，也可以只记录每次 repair 输入输出，eval 用最终状态判断。

---

## 4. Iteration 拆分

### I5.1 修复基础设施（数据结构 + Prompt + 节点）

目标：建立修复所需的最小基础设施，不接入主链路。

修改文件：

- `backend/app/agent/state.py` — 新增 `execution_error`, `repair_history`
- `backend/app/core/llm_provider.py` — 新增 `SQLRepairContext`, `SQLGenerationRequest.repair`
- `backend/app/agent/prompts/sql_generation.py` — `build_sql_generation_messages()` 支持修复模式
- `backend/app/agent/nodes.py` — 新增 `repair_sql_node()`
- `backend/app/agent/repair.py` (新建) — repairability 判断和状态清理函数

实现要点：

- `repair_sql_node()` 接收 `SQLRepairContext`，调用 `provider.generate_sql()`
- `repair_sql_node()` 追加 `repair_history`
- `MockLLMProvider` 默认忽略 repair，保持现有行为
- DeepSeekProvider 无需改接口，通过 prompt builder 自动支持修复

验收：

- `is_guard_repairable()` 覆盖所有 Guard stage
- `is_execution_repairable()` 覆盖 parser/catalog/binder/timeout/connection
- repair prompt 包含原始 SQL、错误阶段、错误原因
- `repair_sql_node()` 能记录 repair_history

---

### I5.2 共享 Repair Engine（Guard + Execute 闭环）

目标：实现可被 chat 和 eval 复用的修复循环。

修改文件：

- `backend/app/agent/repair.py` — 新增 `iter_sql_repair_events()`
- `backend/app/agent/nodes.py` — 必要时调整 `execute_node()` 的异常记录，不吞异常
- `backend/tests/test_repair.py` — 覆盖修复循环

实现要点：

- repair engine 负责循环：guard -> execute -> repair -> guard
- 每次重新 guard 前调用 `reset_failure_state()`
- 不修复 `operation_guard`
- 捕获执行异常并判断是否可修复
- 修复次数超过 2 后返回 error event

验收：

- scope_guard 拒绝后能修复并执行成功
- fanout_guard 拒绝后能修复并执行成功
- 执行 Catalog/Binder 错误后能修复并执行成功
- 连续失败 2 次后停止
- operation_guard 直接失败，不触发 repair

---

### I5.3 Chat SSE 集成

目标：前端查询链路使用共享 repair engine，并通过 SSE 输出修复步骤。

修改文件：

- `backend/app/api/chat.py` — `iter_chat_events()` 接入 `iter_sql_repair_events()`

新增 SSE step：

```json
{
  "step": "repair_sql",
  "status": "completed",
  "attempt": 1,
  "original_sql": "...",
  "repaired_sql": "...",
  "error_stage": "sql_guard",
  "error_kind": "scope_guard",
  "error_reason": "Column fact_orders.product_id is not allowed."
}
```

错误事件新增：

```json
{
  "step": "sql_guard",
  "reason": "...",
  "error_kind": "blocked",
  "repair_history": [...]
}
```

验收：

- Guard 拒绝后 SSE 包含 `repair_sql`
- 执行失败后 SSE 包含 `repair_sql`
- 修复成功后继续 `execute/summarize/recommend_chart/done`
- 修复失败后 error payload 包含 `repair_history`

---

### I5.4 前端修复展示

目标：前端展示修复过程和最终失败历史。

修改文件：

- `frontend/src/App.vue`

实现要点：

- `workflowSteps` 新增 `{ id: "repair_sql", label: "SQL 修复" }`
- SSE handler 解析 `repair_sql`
- 解释信息区域展示 repair history
- 错误卡片展示“已尝试修复 N 次”和每次错误原因
- 不做 SQL diff，只展示原始 SQL 和修复后 SQL

验收：

- 修复成功时能看到修复步骤和 SQL 变化
- 修复失败时能看到每次尝试和最终错误
- 现有无修复查询 UI 不回归

---

### I5.5 Eval 扩展与 closeout

目标：用同一套 repair engine 覆盖修复场景，并输出 repair_count。

修改文件：

- `evals/smoke_cases.yaml` — 新增 Phase 5 修复 cases
- `scripts/run_smoke_eval.py` — 接入共享 repair engine，记录 `repair_count`

Mock 测试策略：

- 新增测试专用 `ScriptedRepairProvider`
- YAML 支持：

```yaml
mock_sql: "SELECT ..."
mock_repair_sqls:
  - "SELECT ..."
  - "SELECT ..."
expected:
  repair_count: 1
```

新增 eval cases：

- `phase5_guard_scope_repair`: 初始 SQL 引用不允许列，修复成允许列
- `phase5_fanout_repair`: 初始 SQL 触发 fanout，修复成 `fact_order_items.item_amount`
- `phase5_execution_repair`: 初始 SQL 通过 Guard 但 DuckDB 执行报 Catalog/Binder 错，修复成功
- `phase5_max_repair_exhausted`: 连续 2 次修复仍失败，返回最终错误
- `phase5_operation_not_repairable`: DELETE/UPDATE 类 Guard 拒绝不触发 repair

验收：

- 扩展后 42 条 smoke case 全部通过
- Phase 5 scripted repair cases 通过
- 报告包含 `repair_count`
- 修复失败 case 的错误归因明确

---

## 5. 文件变更总览

新建文件：

- `backend/app/agent/repair.py` — 修复判断、状态清理、共享 repair engine

修改文件：

- `backend/app/agent/state.py`
- `backend/app/core/llm_provider.py`
- `backend/app/agent/prompts/sql_generation.py`
- `backend/app/agent/nodes.py`
- `backend/app/api/chat.py`
- `frontend/src/App.vue`
- `evals/smoke_cases.yaml`
- `scripts/run_smoke_eval.py`

测试文件：

- `backend/tests/test_repair.py`
- `backend/tests/test_sql_generation.py`
- `backend/tests/test_chat_api.py`
- 必要时补 `backend/tests/test_agent_workflow.py`

---

## 6. 验证计划

### 6.1 单元测试

- `test_repair.py`: repairability、状态清理、max repair、operation_guard 不修复
- `test_sql_generation.py`: 修复 prompt 格式、多轮消息顺序、fanout 提示
- `test_chat_api.py`: SSE 事件序列、repair_sql payload、失败 payload

### 6.2 集成测试

| 场景 | 验证 |
|------|------|
| scope_guard 拒绝 -> 修复成功 | repair_count=1；最终执行成功 |
| fanout_guard 拒绝 -> 修复成功 | 修复后 SQL 不再触发 fanout |
| 执行失败 -> 修复成功 | 捕获执行异常并重新生成 SQL |
| 修复 2 次仍失败 | 返回最终错误 + repair_history |
| intent_guard 拒绝 | 不触发修复 |
| operation_guard 拒绝 | 不触发修复 |

### 6.3 验收标准

1. 不回归：扩展后 42 条 smoke case 全部通过
2. 可修复：scope/fanout/执行错误能被自动修复
3. 安全：修复后 SQL 必须经过完整 Guard 检查
4. 限制：最多修复 2 次，不无限循环
5. 可观测：前端能看到修复步骤和历史
6. 可评测：eval runner 使用同一套 repair engine，报告 `repair_count`
7. Mock 兼容：Mock 默认行为不变，修复测试使用 scripted provider

---

## 7. 不做清单

1. 不做基于用户反馈的修复学习
2. 不做 SQL 语义等价性验证
3. 不修改 Phase 1-4 已有 Guard 规则
4. 不做 intent_guard / operation_guard 修复
5. 不做连接错误、OOM、模型超时等基础设施错误修复
6. 不做 token-level 流式修复；只在每次修复完成后发 SSE step
7. 不做 SQL diff 展示；只展示原始 SQL 和修复后 SQL
8. 不做修复缓存/记忆
9. 不依赖真实 Qdrant+默认 MiniLM 手动验收完成

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 修复后 SQL 引入安全问题 | 修复后必须重新经过完整 SQL Guard |
| 前端链路和 eval 链路行为不一致 | 抽共享 repair engine，`chat.py` 和 `run_smoke_eval.py` 共用 |
| 旧 `stopped_at/error` 状态污染后续尝试 | 每次重新 guard 前清理瞬时失败状态 |
| LLM 修复时忽略错误信息 | 使用结构化 `SQLRepairContext` 和多轮对话 prompt |
| 修复循环增加延迟 | 最多 2 次；报告 repair_count 和 elapsed |
| Mock 无法覆盖修复 | 使用 ScriptedRepairProvider / `mock_repair_sqls` |
| fanout 修复困难 | prompt 中加入固定 fanout 说明，eval 单独覆盖 |

---

## 9. 实现顺序与时间估计

```text
I5.1 修复基础设施（数据结构 + Prompt + 节点）  1.0 天
I5.2 共享 Repair Engine                         1.0 天
I5.3 Chat SSE 集成                              0.5-1 天
I5.4 前端修复展示                               0.5 天
I5.5 Eval 扩展与 closeout                       0.5-1 天
```

总计: 3.5-4.5 天

关键路径: I5.1 -> I5.2 -> I5.3 -> I5.4/I5.5。

---

## 10. Critical Files

- `backend/app/agent/repair.py` — 修复判断、状态清理、共享 repair engine
- `backend/app/api/chat.py` — SSE 映射层，不直接拥有修复主逻辑
- `scripts/run_smoke_eval.py` — eval 接入共享 repair engine
- `backend/app/agent/nodes.py` — `repair_sql_node()` 和现有 guard/execute 节点
- `backend/app/agent/prompts/sql_generation.py` — 修复 prompt
- `backend/app/core/llm_provider.py` — `SQLRepairContext` / `SQLGenerationRequest`
