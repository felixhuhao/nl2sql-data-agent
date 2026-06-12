# Semantic Grounding Guard — Design

**Date:** 2026-06-12
**Status:** Draft (pending review)
**Related:** `docs/prompt-reliability-audit.md` (Manual Test Finding: Unsupported Concept Substitution)

---

## Problem

After SQL generation, the model sometimes answers an **unsupported business concept** by silently substituting a nearby *available* concept, changing the meaning of the result without telling anyone.

Observed cases:

- **Case 25** — `查看删除率趋势` ("deletion rate trend"). Schema has no deleted/deletion concept. Model emitted `countIf(fo.order_status = 'refunded') / count(*) AS deletion_rate`, silently turning *deletion rate* into *refund rate*.
- **Case 37** — `删除的订单` ("deleted orders") became generic orders with no deleted filter at all.

Both pass every existing guard:

- [`_detect_blocked_intent`](../../../backend/app/agent/nodes.py) only catches *destructive intent* (DELETE/DROP/external file reads). A `SELECT` computing a refund ratio is not destructive.
- [`guard_sql`](../../../backend/app/sql_guard/guard.py) only checks command safety + column allow-listing. `order_status = 'refunded'` uses a perfectly valid allowed column.

This is a **semantic** failure wearing a **syntactic** disguise. It needs a new check in a dimension neither guard covers.

### Why a SQL-only check is insufficient

The overlay ([`ecommerce.yml`](../../../backend/app/metadata/semantic_overlays/ecommerce.yml)) documents `order_status` sample values as `paid`, `completed`, `refunded` — and **nothing for `deleted`**. So:

1. `删除` (deleted) has **zero evidence** across every channel (column name, description, display name, sample value, metric) → a clean unsupported signal.
2. `refunded` **is** documented — which is *exactly why* the model grabbed it as a proxy.

A naive "is every column/value in the SQL grounded?" check **would pass** Case 25, because `refunded` is genuinely grounded. The ungrounded thing is not the value — it is the **mapping from the question's concept (`删除率`) to that value**. Therefore the check must be **question-aware**, evaluating `{question, schema_context, sql}` together. An allow-list / SQL-only shortcut cannot work.

---

## Scope

### In scope

- Detect when a **business entity, filter, status value, or named metric** the question requires is unsupported by schema evidence — whether the SQL **substitutes** a proxy for it (Case 25) or silently **omits** it (Case 37).
- Respond with a confidence-banded action: block, warn, or pass.
- Eval-first rollout (warn-only first, enable blocking once precision is measured).

### Out of scope (must NOT be flagged)

- **Analytical operations** — rank correlation, YoY/MoM, moving average, TopN, share/ratio of supported measures. These need *compatible measures/dimensions*, not same-named columns. (Case 36, rank correlation over `unit_price`/`quantity`, must stay valid.)
- Any special-case keyword rule for `删除率`, `refunded`, or specific status values. The audit explicitly forbids this; the mechanism must be general and evidence-based.

---

## Architecture

A new node, `semantic_guard_node`, runs **inside the repair loop** ([`iter_sql_repair_events`](../../../backend/app/agent/repair.py)), on **every** SQL candidate — initial and repaired — after it passes syntax/scope (`sql_guard`) and before `execute_node`. It mirrors the existing [`sql_guard_node`](../../../backend/app/agent/nodes.py) pattern: read state, set `stopped_at`/`error` on a hard block, or attach an annotation on a soft warning.

```
                 ┌──────────────── repair loop ────────────────┐
generate_sql ──► │ sql_guard ──► semantic_guard ──► execute      │ ──► finalize
                 │    │ fail          │ block          │ fail     │
                 │    └─► repair ◄────┘ (non-repairable)└─► repair │
                 └──────────────────────────────────────────────┘
                                    │
   semantic_guard ─ Stage 1: deterministic evidence lookup → confidence band
                  └ Stage 2 (medium band only): question-aware LLM verifier
```

**Placement rationale.** The original draft placed this between `generate_sql` and `sql_guard`, i.e. in the once-only pre-repair workflow. That is wrong: [`iter_sql_repair_events`](../../../backend/app/agent/repair.py) re-runs `sql_guard_node` at the top of every loop iteration and loops back after each `repair_sql_node`, so **repaired** SQL would re-hit `sql_guard` but bypass a pre-loop semantic guard entirely. Running inside the loop after `sql_guard` passes guarantees every candidate that could reach execution is grounded. Running *after* `sql_guard` (not before) means we only spend the semantic check on syntactically valid, in-scope SQL.

**A semantic block is non-repairable.** Unlike `scope_guard`/`syntax_guard` (in `REPAIRABLE_GUARD_STAGES`), a high-unsupported block means the requested concept genuinely has no schema evidence — re-prompting the model would only produce another substitution. So `semantic_guard` adds a stage to `NON_REPAIRABLE_GUARD_STAGES` (alongside `operation_guard`): on a hard block it stops the loop and returns the error, it does not trigger `repair_sql_node`.

### Stage 1 — Deterministic evidence lookup (cheap, always runs)

Input: `{question, schema_context, sql, datasource_name}`.

**Evidence must be datasource-scoped.** The current overlay is global: [`_overlay_path`](../../../backend/app/metadata/semantic_overlay.py) resolves a single configured path (`ecommerce.yml`) with no datasource parameter. Using that overlay as evidence for a *different* datasource would supply false evidence (e.g. e-commerce status values for an unrelated schema). So Stage 1 builds evidence in this priority:

1. **Primary — the datasource's own metadata/schema context** (`state.schema_context`): labels, descriptions, aliases, sample values, Metric Definitions, Verified Queries, SQL Generation Guidance. This is always correct for the active datasource.
2. **Secondary — a semantic overlay only when it is explicitly bound to this datasource.** Until overlay→datasource binding exists, the overlay is consulted only for the datasource it actually describes (the demo e-commerce one) and ignored otherwise. Wiring per-datasource overlay binding is tracked as an open question.

Concretely, for the concept(s) the question asks about, check presence across every channel available *for the active datasource*: `table_semantics` (display name, description, domain), `column_semantics` / `table_column_semantics`, `dimension_columns` / `metric_columns`, `sample_value_fallbacks`, and the schema-context channels above.

**The unit of analysis is each required business concept in the question, not each mapping in the SQL.** This is essential: the failure has two shapes, and a SQL-mapping-driven check only sees the first.

- **Substitution** (Case 25): the concept is unsupported and the SQL grounds it in a *proxy* (`删除率` → `order_status = 'refunded'`). Visible in the SQL.
- **Omission** (Case 37): the concept is unsupported and the SQL *silently drops* it (`删除的订单` → unfiltered orders, no deleted predicate at all). **Invisible** in the SQL — there is no mapping to inspect. Only checkable by starting from the question.

So Stage 1 first identifies the question's required business concepts (entities, filters, status values, named metrics), checks each for schema evidence, and only then asks whether the SQL substituted or omitted it. Output: a **confidence band**, not a boolean.

| Band | Condition | Action |
|------|-----------|--------|
| **grounded** | Every required business concept matches evidence in some channel | Pass untouched |
| **analytical** | Request is an analytical operation over supported measures/dimensions | Pass untouched (skip Stage 2) |
| **high-unsupported** | A required business concept has **no match in any channel** — whether the SQL substituted a proxy *or* omitted it entirely | Block (Stage 2 skipped — already conclusive) |
| **medium** | Concept has only weak/adjacent evidence (e.g. absent itself but the SQL grounds in a documented adjacent value), or concept extraction is uncertain | Send to Stage 2 |

Concept extraction from the question is itself fuzzy. Stage 1 does **not** need perfect concept parsing — it needs to decide a band conservatively. When it cannot confidently place a query in `grounded` or `high-unsupported`, it falls to `medium` and defers to Stage 2.

### Stage 2 — Question-aware LLM verifier (medium band only)

A **fresh** LLM call (not the generator marking its own homework) that sees only `{question, schema_context, sql}` and is instructed adversarially:

> Start from the **question**. List the business entities, filters, status values, and named metrics the question requires. For each, decide whether schema evidence supports it. Then check the SQL: if a required concept is unsupported, flag it whether the SQL **substituted** a proxy for it OR **omitted** it entirely (e.g. the question asks for "deleted orders" but the SQL filters nothing). Do NOT flag analytical operations (rank correlation, YoY, moving average, TopN, share) — those require compatible measures/dimensions, not same-named columns.

Returns structured output:

```json
{
  "ok": false,
  "issues": [
    {
      "concept": "删除率",
      "failure_kind": "substituted",
      "sql_mapping": "order_status = 'refunded'",
      "evidence": [],
      "supported": false,
      "explanation": "Schema documents 'refunded' but defines no deleted/deletion concept; the mapping is invented."
    },
    {
      "concept": "删除的订单",
      "failure_kind": "omitted",
      "sql_mapping": null,
      "evidence": [],
      "supported": false,
      "explanation": "Question requires a 'deleted' filter, but the SQL applies no such predicate; the concept is silently dropped."
    }
  ]
}
```

`failure_kind` is `substituted` (a proxy appears in the SQL) or `omitted` (`sql_mapping: null` — the concept was dropped). Both are unsupported-concept failures.

Why a separate call and not structured self-report from the generator (rejected Option 2): the model that substitutes `refunded` for `删除` is the same model that, asked to self-report in the same forward pass, will rationalize `supported: true`. A fresh critic has no investment in defending the SQL. It also avoids re-touching the SQL/JSON output contract stabilized in audit Findings 3/11/13.

#### Verifier failure handling (fail open, never a new reliability cliff)

The second LLM call must not become a new failure mode that the audit's reliability work would regret. It runs under a bounded timeout and a single attempt (no internal retry loop). On **timeout, provider error, empty response, or unparseable output**, Stage 2 is treated as **inconclusive → fail open**: the candidate passes to `execute`, and a `verifier_unavailable` note is attached to the visible warnings (see Banded response) so the degraded check is observable, not silent.

Crucially, **failing open here does not weaken the hard block.** The `high-unsupported` block is decided entirely by deterministic Stage 1 and never depends on the verifier — so `enforce` mode still blocks unsupported concepts even when the LLM verifier is down. Only the medium-band warning degrades. Behavior by mode:

| `semantic_guard_mode` | Stage 1 high-unsupported | Stage 2 medium-band result | Stage 2 unavailable |
|---|---|---|---|
| `off` | pass | pass | pass |
| `warn` | warn (visible) | warn if `ok:false` | pass + `verifier_unavailable` note |
| `enforce` | **block** | warn if `ok:false` (medium stays warn-only) | pass + `verifier_unavailable` note |

---

## Banded response

| Band / verifier result | Behavior | Rollout phase |
|---|---|---|
| grounded / analytical | Pass | always |
| medium, verifier `ok: true` | Pass | always |
| medium, verifier `ok: false` | **Warn** — visible `grounding_warnings` in the response payload + UI banner; query still runs | phase 1 → later: clarify |
| medium, verifier unavailable | Pass + `verifier_unavailable` note in `grounding_warnings` | always |
| high-unsupported | **Block** (non-repairable) — `stopped_at="semantic_guard"`, clear `error` message | phase 2 |

### Block message (high-unsupported)

```
当前 schema 中没有"删除/删除率"对应的字段、状态值或指标，无法安全生成 SQL。
```

The message names the unsupported concept and states no safe SQL exists — it does not invent a proxy.

### Warn annotation (medium, unsupported) — must be *visible*

A warning that only lives in [`explainability`](../../../backend/app/agent/explainability.py) is too easy to miss — the analyst would see numbers that silently mean something other than what they asked, which is the exact failure we are trying to prevent. So the warning is a **first-class, visible part of the final response payload**, not buried in explainability:

- A dedicated `grounding_warnings` field on `AgentState`, populated by `semantic_guard_node` (each entry: concept, `failure_kind`, the proxy/omission, and a plain-language caveat).
- This field is included in the **API response payload** (chat result / SSE final event) alongside the result, not only inside `explainability`.
- The **UI surfaces it prominently** next to the result table (e.g. a warning banner: "结果可能不准确：'删除率' 在当前数据中没有定义，已用近似口径/未过滤"), so it cannot be overlooked.

Explainability may *additionally* carry the detailed evidence trail, but visibility does not depend on the user opening explainability.

### Fail-closed safety check

`sample_value_fallbacks` is, by name, a *fallback* list — not guaranteed exhaustive. Absence of a value there is a **signal, not proof** of non-existence. Before a **high-unsupported block** fires on a missing *status/enum value*, run a cheap `SELECT DISTINCT <column> LIMIT N` against the real column to confirm the value genuinely does not exist. This prevents blocking a value that exists in data but was never enumerated in the overlay. (Concept-level absence — no column/metric/description at all, as with `删除率` — does not need this check; there is no column to probe.)

### Datasets without an overlay

Overlays are per-dataset (`ecommerce.yml` is the demo). For datasets lacking one, Stage 1 degrades gracefully to schema-context metadata only; more queries fall to the `medium` band and lean on Stage 2. No dataset is left unprotected; coverage is just weaker without curated evidence.

---

## Data flow & state

New `AgentState` fields (additive to [`state.py`](../../../backend/app/agent/state.py)):

- existing `error` / `stopped_at` reused for hard blocks (consistent with `sql_guard`); `semantic_guard` is added to `NON_REPAIRABLE_GUARD_STAGES` in [`repair.py`](../../../backend/app/agent/repair.py).
- a dedicated, typed `grounding_warnings: list[...]` field (not just an `explainability` key) so warnings are carried explicitly into the response payload and UI. Each entry: concept, `failure_kind` (`substituted` / `omitted` / `verifier_unavailable`), the proxy or omission, and a plain-language caveat.

The API response model (chat result / SSE final event) is extended to include `grounding_warnings`. No change to the SQL/JSON *generation* output contract. No change to `generate_sql` prompts (the guard is a separate node; a *light* prompt rule — "do not invent proxy metrics/filters from adjacent values" — may be added as a cheap first line of defense, but the guard does not depend on it).

---

## Rollout (eval-first)

1. **Phase 1 — warn-only.** Ship Stage 1 + Stage 2 in warn-only mode: every band that would block instead emits a visible `grounding_warnings` entry. Nothing is blocked. Add eval cases (unsupported concept *substituted* and *omitted*, adjacent-value substitution, plus *negative* cases like rank correlation and legitimate refund-rate questions) and measure verifier precision/recall.
2. **Phase 2 — enable blocking.** Once precision on the high-unsupported band is acceptable, flip that band to fail-closed (with the distinct-values safety check). Medium band stays warn-only.
3. **Phase 3 (later, optional) — clarify.** Replace medium-band warnings with an interactive disambiguation turn ("删除率 在当前 schema 中没有直接对应。你是指 退款率 / 取消率 / 都不是?"). Higher build cost (mid-workflow suspend/resume); deferred until value is proven.

A setting (e.g. `semantic_guard_mode = off | warn | enforce`) gates the phases so rollout is reversible.

---

## Testing

- **Unit (Stage 1):** band assignment for grounded / analytical / high-unsupported / medium inputs, using fixture overlays. Assert `删除率` (substituted) → high-unsupported, `删除的订单` (omitted, no filter in SQL) → high-unsupported, rank correlation → analytical, `销售额` → grounded.
- **Unit (Stage 1) datasource scoping:** an overlay describing datasource A must NOT supply evidence when the active datasource is B.
- **Unit (Stage 2):** verifier prompt/parse with a mock provider; assert `ok:false` for both `failure_kind: substituted` and `failure_kind: omitted`, `ok:true` for grounded. Assert fail-open (`verifier_unavailable`) on timeout/error/unparseable output.
- **Node integration:** `semantic_guard_node` sets `stopped_at`/`error` on block; emits visible `grounding_warnings` on warn; passes through on grounded.
- **Repair-loop integration:** a **repaired** candidate is still semantically guarded (the original-draft bypass must not regress); a high-unsupported block is non-repairable (does not trigger `repair_sql_node`).
- **Negative regression:** Case 36 rank correlation and a legitimate `退款率` question must NOT be flagged.
- **Workflow:** blocked query never reaches `execute`; `grounding_warnings` appears in the response payload.
- **Eval cases:** add unsupported-concept (substituted + omitted) and adjacent-substitution cases to the manual/eval suite per audit "Future Hardening" item 4.

---

## Rejected alternatives

- **Option 1 — prompt-only rule.** "Do not create proxy metrics from adjacent status values." Cheap, near-free to add, but weak as the *sole* mechanism — no enforcement, no measurement. Kept only as an optional light first-line rule, not the guard.
- **Option 2 — generator self-reports `concept_mappings` in the same call.** Rejected: self-report bias (the generator defends its own substitution) and it re-touches the just-stabilized output contract.
- **Single-band boolean instead of confidence bands.** Rejected: forces one failure behavior for an unproven verifier. Banding lets high-confidence cases fail closed while uncertain cases warn, and supports eval-first rollout.

---

## Open questions for the plan

1. **Overlay→datasource binding.** How an overlay is explicitly bound to a datasource (manifest field, per-datasource overlay path, or registry entry), so Stage 1 can consult an overlay only when it describes the active datasource. Until this lands, overlay evidence is used only for the datasource it actually describes.
2. **Concept-extraction approach in Stage 1** — lexical/alias overlap vs. embedding similarity vs. letting Stage 2 do all concept parsing for the medium band. Affects how reliably *omitted* concepts (Case 37) are caught deterministically vs. via the verifier.
3. Where `semantic_guard_mode` (`off` / `warn` / `enforce`) lives in `Settings`, and its default for tests vs. production.
4. Verifier timeout budget and which provider it uses (same as generation vs. a cheaper/faster model).
