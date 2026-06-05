# Multi-Turn Conversational Follow-up — Design

> Status: Design (approved for spec). Target: Phase 8 signature feature, gated.
> Scope decision recap: this is a gated demo feature. If the riskier slices prove
> flaky in testing, ship the robust subset rather than destabilize the demo chain.

## 1. Goal

Let a user refine the previous query in natural language instead of re-asking a full
question, so the chat behaves like a real data conversation:

```text
最近30天每日销售额和订单数
  -> 按地区拆分          (add dimension)
  -> 只看华东            (add value filter)
  -> 换成订单数          (swap metric)
  -> 改成最近90天        (change time range)
```

The follow-up turn carries forward the prior turn's query as **conversation context**.
A single LLM call both decides whether the message is a follow-up and produces the new
SQL. Every turn still flows through retrieval → focused context → SQL Guard → execute,
so safety and observability are unchanged.

## 2. Scope

### In scope (core)
- **Add/change dimension** — 按地区拆分 / 再按渠道分.
- **Add/narrow value filter** — 只看华东 (resolved through existing Value Recall).
- **Swap metric** — 换成订单数 (reuses the metric layer).
- **Change time range** — 改成最近90天.

### Out of scope (non-goals)
- **Transform follow-ups** (同比 / 环比 / 移动平均) — structural rewrites, deferred.
- **Cross-datasource follow-ups** — switching DuckDB↔ClickHouse mid-conversation is
  treated as a fresh turn (dialect/columns differ).
- Persistence across process restart; multi-worker session sharing; auth/user isolation.
  `session_id` is an opaque client-minted token.

## 3. Architecture

Two ownership boundaries:

- **Agent workflow stays a pure function.** `run_query_workflow(...)` and the streaming
  path accept an optional `conversation_context`. Tests and the eval runner drive
  multiple turns by passing context directly — no HTTP required.
- **The API layer owns session storage.** It maps `session_id -> recent turns`, loads the
  prior `ConversationContext` before invoking the workflow, and appends the new one after
  a successful turn.

New parts:
- `ConversationContext` dataclass — snapshot of the prior turn.
- `SessionStore` — in-memory, capped, TTL, keyed by `session_id` (API layer).
- `SQLGenerationResult` — structured generation output `{sql, is_follow_up, change_kind}`.
- Conversation-context block in the SQL-generation prompt + structured filter carry-over.
- Frontend: `session_id` plumbing, `新对话` reset, follow-up/kind badge.

Touched files (existing):
- `backend/app/agent/state.py` — carry `conversation_context`, `is_follow_up`, `change_kind`.
- `backend/app/agent/nodes.py` — `build_context` unions prior assets with current retrieval
  (§5); `generate_sql` consumes the structured result.
- `backend/app/agent/workflow.py` — thread `conversation_context` through both entrypoints;
  insert the `conversation_filter_verify` stage between `sql_guard` and `execute` (§7).
- `backend/app/agent/repair.py` — targeted filter-preservation repair, sharing the global
  max-2 budget (§7).
- `backend/app/agent/prompts/sql_generation.py` — conversation-context section + JSON output
  contract when prior context is present (§6).
- `backend/app/core/llm_provider.py` — `generate_sql` returns `SQLGenerationResult` with the
  parse/fallback rules of §6 (Mock + DeepSeek).
- `backend/app/api/chat.py` — `session_id` handling, session store, new SSE fields.
- `frontend/src/App.vue` — session id, reset button, badge.

New files:
- `backend/app/agent/conversation.py` — `ConversationContext`, `FilterPredicate`,
  `build_conversation_context(state)`, and the sqlglot WHERE-walk helper used by both filter
  capture and verify (§7).
- `backend/app/api/session_store.py` — `SessionStore` (TTL + LRU caps, §4).

## 4. Data model

```text
FilterPredicate:
  column          # e.g. dim_regions.region_name
  op              # '=', 'in', ...
  value           # e.g. 华东

ConversationContext:
  question            # prior NL question
  normalized_sql      # prior Guard-approved SQL — source of truth for the rewrite
  datasource_name
  matched_tables      # from prior explainability
  matched_columns
  metric              # prior metric (if any)
  date_interpretation # prior time window
  result_columns
  active_filters: list[FilterPredicate]   # see §7

SessionState:
  turns: deque[ConversationContext]   # cap = last 5
  last_updated: float                 # for TTL eviction

SessionStore:
  _sessions: OrderedDict[str, SessionState]          # ordered for LRU
  get(session_id) -> ConversationContext | None      # latest turn, None if absent/expired; moves entry to MRU
  append(session_id, ctx) -> None                    # creates/updates entry, enforces caps
  evict_expired() -> None                            # opportunistic on access: drop TTL-expired entries
  TTL_SECONDS = 1800
  MAX_TURNS = 5
  MAX_SESSIONS = 500     # after TTL eviction, if still over the cap, drop least-recently-used
```

`session_id` is client-supplied, so total session count is bounded independently of
per-session turns: every access first evicts TTL-expired entries, then enforces
`MAX_SESSIONS` by dropping LRU entries. This prevents `_sessions` growth under abuse or load.

Reset is **client-driven**: `新对话` mints a fresh `session_id`; the stale entry ages out
via TTL. No reset endpoint.

## 5. Data flow across turns

**Turn 1** (no `session_id` or unknown/expired): unchanged flow. After success, the API
builds a `ConversationContext` from the final `AgentState` and `append`s it.

**Turn N+1** (prior context found):

```text
datasource_selected         # datasource changed vs prior ctx -> drop context, treat as fresh
intent_guard
retrieve_context            # on the CURRENT question — resolves new assets (地区->dim_regions, 华东->Value Recall)
build_context               # prior ctx present -> focused context over UNION(current retrieval,
                            #   prior matched_tables/matched_columns/join_paths) + conversation-context block
olap_detected
generate_sql                # returns {is_follow_up, change_kind, sql}
sql_guard                   # unchanged; conversation block also included in repair prompt
conversation_filter_verify  # post-guard, pre-execute: see §7; targeted repair -> sql_guard again if a filter dropped
execute -> repair*          # guard/execution repair as today
finalize                    # parse is_follow_up/change_kind, emit SSE, append new ConversationContext
```

**Prior-assets union (build_context).** Elliptical follow-ups (换成订单数, 改成最近90天)
retrieve almost nothing from the current question alone, yet the prompt constrains the model
to the focused schema context. So when prior context is present, `build_context` builds the
focused context over the **union** of the current retrieval result and the prior turn's
`matched_tables` / `matched_columns` / `join_paths`. Without this, the schema context would
omit the very tables/columns the follow-up must reuse.

When the model returns `is_follow_up = false`, it ignored the prior context and produced a
standalone query — the turn is effectively fresh, and the new `ConversationContext` replaces
the conversational thread going forward. No pre-generation branch exists; classification is a
field of the generation output.

## 6. Context carry-over into generation

`SQLGenerationRequest` gains optional fields: `prior_sql`, `prior_summary`
(tables/metric/time), `carried_filters`. The prompt gains a 对话上下文 section instructing:

- First decide whether the new question refines the prior query.
- If **not** a follow-up: ignore the prior query and answer standalone.
- If a follow-up: output a **full standalone SQL** that preserves the prior dimensions,
  filters, metric, and time window **unless the new question changes them**.
- Return structured output: `is_follow_up`, `change_kind` ∈
  {dimension, filter, metric, time, none}, and `sql`.

### Output contract

`generate_sql` returns `SQLGenerationResult{sql, is_follow_up, change_kind}`. The wire format
depends on whether prior context is present, to avoid regressing the existing SQL-only path:

- **No prior context (fresh turn):** prompt omits the 对话上下文 section and asks for
  **SQL only**, exactly as today. The result is `SQLGenerationResult(sql=<text>,
  is_follow_up=False, change_kind="none")`. The mock/real eval paths that currently pass are
  unchanged.
- **Prior context present:** prompt asks for a **single JSON object and nothing else**:

  ```json
  { "sql": "SELECT ...", "is_follow_up": true, "change_kind": "metric" }
  ```

  `change_kind ∈ {dimension, filter, metric, time, none}`.

### Parsing & fallback

1. Strip code fences (reuse the existing `sql_postprocess` fence stripping).
2. Try `json.loads`. On success, read `sql` (required), `is_follow_up` (default `false`),
   `change_kind` (default `none`).
3. If JSON parsing fails but the payload still looks like a SQL statement (starts with a
   SELECT/CTE), salvage it as `sql` with `is_follow_up=false, change_kind=none` (**fresh**).
4. If no SQL statement can be extracted at all, raise the existing `generate_sql` error — do
   **not** pass an empty/garbage string to SQL Guard.

This makes "default to fresh" precise: metadata-only parse failures degrade to fresh; a
genuinely missing SQL is a generation error, not a Guard input.

## 7. Filter-persistence backstop

`active_filters` accumulates **only the predicates introduced by filter follow-ups**, not
parsed from arbitrary SQL.

### Capture rule (Guard-normalized SQL is the source of truth)

"Via Value Recall" alone is ambiguous — a question can produce several value hits, and the
recalled column may not be the one the SQL actually filtered on. So a predicate is captured
into `active_filters` only when **both** hold:

1. it corresponds to a value hit for the current question, **and**
2. it actually appears as a `column op value` predicate in the **Guard-normalized SQL's
   WHERE** (verified by walking the WHERE clause with sqlglot, already a dependency).

This guarantees `active_filters` reflects what executed, not what was merely recalled.

### Verify stage (post-guard, pre-execute)

Filter persistence is enforced by a distinct stage, **not** the existing guard/execution
repair loop (which only triggers on Guard rejection or execution errors). The flow is:

```text
generate_sql -> sql_guard (allowed) -> conversation_filter_verify -> execute
```

`conversation_filter_verify` runs after Guard approves and **before** execution:

- For each carried predicate in `active_filters`, check presence in the normalized WHERE
  (same sqlglot walk as capture).
- If a predicate is missing **and** `change_kind != filter` (the user did not change filters):
  fire **one** targeted repair ("preserve filter X"), which re-enters `sql_guard` and then
  `conversation_filter_verify` again.
- These filter repairs **share the global max-2 repair budget** with guard/execution repairs,
  so triggers cannot amplify. If still unsatisfied after the budget is spent, **stop before
  execution** and emit an error event with `step = conversation_filter_verify`, the missing
  predicate, and the attempted SQL. This avoids silently answering a broader query than the
  user asked for.

This targets the specific drift case (a filter added in turn N silently dropped in turn N+1)
without an IR or full AST extraction, and never executes a follow-up whose carried filter was
silently lost.

## 8. API changes

- `POST /api/chat/query`: accept optional `session_id`. If absent, the backend mints one and
  emits it in a first SSE `session` event.
- SSE: the `generate_sql` step payload adds `is_follow_up` and `change_kind`; the `done`
  payload echoes `session_id`.
- No new endpoints.

## 9. Frontend (App.vue)

- Hold `session_id` (uuid). `新对话` clears the current question/result workspace and mints a
  new id.
- UI scope stays as the existing single-result workbench, not a full transcript. Add a compact
  badge on the current result when `is_follow_up` is true, using `change_kind`:
  追问 · 维度下钻 / 值过滤 / 指标切换 / 时间范围.
- No router or new view.

## 10. Error handling

- Generation output parsing (per §6): metadata missing/unparseable but SQL salvageable →
  **default to fresh**; no SQL extractable at all → `generate_sql` error (not a Guard input).
- Unknown/expired `session_id` (restart, TTL) → treated as turn 1, graceful.
- Cap/TTL eviction mid-conversation → follow-up degrades to fresh, no crash.
- Datasource changed vs prior context → context dropped, treated as fresh (§5).
- Missing carried filter after the targeted repair budget is spent →
  `conversation_filter_verify` error before execution; the user can rephrase or start a new
  conversation.
- Carried context **never** bypasses SQL Guard; repairs also receive the conversation block so
  they preserve filters.

## 11. Evaluation & tests

### Mock multi-turn eval
- New `conversation: [turn1, turn2, ...]` case type in `evals/smoke_cases.yaml`.
- Mock provider returns a scripted `SQLGenerationResult` per turn
  (`{is_follow_up, change_kind, sql}`).
- Headline case = the demo chain: 销售额 → 按地区拆分 → 只看华东 → 换成订单数 → 改成最近90天.
- **Filter-persistence assertion**: after 只看华东 then 换成订单数, assert the region filter is
  still present in the final normalized SQL / result.
- Runner threads `ConversationContext` between turns (mirrors the API's session handling).

### Unit tests
- `SessionStore`: per-session turn cap, TTL expiry, **`MAX_SESSIONS` LRU eviction**,
  reset-by-new-id, missing key.
- `build_conversation_context(state)`: correct snapshot fields; **filter capture only when the
  value hit appears in the Guard-normalized WHERE** (capture rule, §7) — incl. a negative case
  where a recalled value is *not* in the WHERE and must not be captured.
- `build_context` **prior-assets union**: an elliptical follow-up (改成最近90天) whose current
  retrieval is near-empty still yields a focused context containing the prior tables/columns.
- Prompt builder: 对话上下文 section present only with prior context; SQL-only vs JSON contract;
  carried filters rendered.
- `generate_sql` structured result: SQL-only fresh path unchanged; JSON parse path; fence
  stripping; salvage-to-fresh on metadata-only failure; **`generate_sql` error when no SQL
  extractable**.
- `conversation_filter_verify`: missing carried filter triggers one targeted repair and
  re-guard; budget shared with guard/exec repair (no amplification); never executes the
  unverified SQL.
- Workflow integration: 2–3 turn run asserts dimension add, filter persist, metric swap, time
  change; and a non-follow-up mid-session question returns a standalone query (fresh).

## 12. Rollout / gating

- Build the shared foundation first (session store, context object, carry-over, structured
  generation result), then per-type behavior, then the filter backstop, then frontend, then
  eval.
- This is a **gated** feature: if the filter-persistence backstop or mid-session
  classification proves flaky, ship the robust subset (dimension + metric + time) and the
  demo chain without the fragile slice rather than delaying or destabilizing Phase 8.
