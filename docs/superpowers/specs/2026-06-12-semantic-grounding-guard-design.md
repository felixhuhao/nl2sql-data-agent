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

- [`_detect_blocked_intent`](../../backend/app/agent/nodes.py) only catches *destructive intent* (DELETE/DROP/external file reads). A `SELECT` computing a refund ratio is not destructive.
- [`guard_sql`](../../backend/app/sql_guard/guard.py) only checks command safety + column allow-listing. `order_status = 'refunded'` uses a perfectly valid allowed column.

This is a **semantic** failure wearing a **syntactic** disguise. It needs a new check in a dimension neither guard covers.

### Why a SQL-only check is insufficient

The overlay ([`ecommerce.yml`](../../backend/app/metadata/semantic_overlays/ecommerce.yml)) documents `order_status` sample values as `paid`, `completed`, `refunded` — and **nothing for `deleted`**. So:

1. `删除` (deleted) has **zero evidence** across every channel (column name, description, display name, sample value, metric) → a clean unsupported signal.
2. `refunded` **is** documented — which is *exactly why* the model grabbed it as a proxy.

A naive "is every column/value in the SQL grounded?" check **would pass** Case 25, because `refunded` is genuinely grounded. The ungrounded thing is not the value — it is the **mapping from the question's concept (`删除率`) to that value**. Therefore the check must be **question-aware**, evaluating `{question, schema_context, sql}` together. An allow-list / SQL-only shortcut cannot work.

---

## Scope

### In scope

- Detect when SQL grounds a **business entity, filter, status value, or named metric** in schema evidence that does not actually correspond to the concept the user asked for.
- Respond with a confidence-banded action: block, warn, or pass.
- Eval-first rollout (warn-only first, enable blocking once precision is measured).

### Out of scope (must NOT be flagged)

- **Analytical operations** — rank correlation, YoY/MoM, moving average, TopN, share/ratio of supported measures. These need *compatible measures/dimensions*, not same-named columns. (Case 36, rank correlation over `unit_price`/`quantity`, must stay valid.)
- Any special-case keyword rule for `删除率`, `refunded`, or specific status values. The audit explicitly forbids this; the mechanism must be general and evidence-based.

---

## Architecture

A new node, `semantic_guard_node`, runs **after `generate_sql` and before `sql_guard`** in the workflow ([`nodes.py`](../../backend/app/agent/nodes.py) / [`workflow.py`](../../backend/app/agent/workflow.py)). It mirrors the existing [`sql_guard_node`](../../backend/app/agent/nodes.py) pattern: read state, set `stopped_at`/`error` on a hard block, or attach an annotation on a soft warning.

```
generate_sql ──► semantic_guard ──► sql_guard ──► execute
                      │
                      ├─ Stage 1: deterministic evidence lookup → confidence band
                      └─ Stage 2 (medium band only): question-aware LLM verifier
```

Placement rationale: before `sql_guard` so a blocked query never reaches command/column validation or execution; after `generate_sql` because we need the candidate SQL to inspect.

### Stage 1 — Deterministic evidence lookup (cheap, always runs)

Input: `{question, schema_context, sql, semantic_overlay}`.

For the concept(s) the question asks about, check presence across every evidence channel already available:

- overlay `table_semantics` (display name, description, domain)
- overlay `column_semantics`, `table_column_semantics`
- overlay `dimension_columns` / `metric_columns`
- overlay `sample_value_fallbacks`
- schema-context labels, descriptions, aliases, Metric Definitions, Verified Queries, SQL Generation Guidance

Output: a **confidence band**, not a boolean.

| Band | Condition | Action |
|------|-----------|--------|
| **grounded** | Concept matches evidence in any channel | Pass untouched |
| **analytical** | Request is an analytical operation over supported measures/dimensions | Pass untouched (skip Stage 2) |
| **high-unsupported** | No match in *any* channel, AND the SQL nonetheless introduces a business filter/metric for it | Block (Stage 2 skipped — already conclusive) |
| **medium** | Weak/adjacent evidence only (e.g., concept absent but SQL grounds itself in a documented adjacent value) | Send to Stage 2 |

Concept extraction from the question is itself fuzzy. Stage 1 does **not** need perfect concept parsing — it needs to decide a band conservatively. When Stage 1 cannot confidently place a query in `grounded` or `high-unsupported`, it falls to `medium` and defers to Stage 2.

### Stage 2 — Question-aware LLM verifier (medium band only)

A **fresh** LLM call (not the generator marking its own homework) that sees only `{question, schema_context, sql}` and is instructed adversarially:

> Identify business entities, filters, status values, and named metrics in the SQL that correspond to a concept in the question. For each, decide whether schema evidence justifies that mapping. Do NOT flag analytical operations (rank correlation, YoY, moving average, TopN, share) — those require compatible measures/dimensions, not same-named columns.

Returns structured output:

```json
{
  "ok": false,
  "issues": [
    {
      "concept": "删除率",
      "sql_mapping": "order_status = 'refunded'",
      "evidence": [],
      "supported": false,
      "explanation": "Schema documents 'refunded' but defines no deleted/deletion concept; the mapping is invented."
    }
  ]
}
```

Why a separate call and not structured self-report from the generator (rejected Option 2): the model that substitutes `refunded` for `删除` is the same model that, asked to self-report in the same forward pass, will rationalize `supported: true`. A fresh critic has no investment in defending the SQL. It also avoids re-touching the SQL/JSON output contract stabilized in audit Findings 3/11/13.

---

## Banded response

| Band / verifier result | Behavior | Rollout phase |
|---|---|---|
| grounded / analytical | Pass | always |
| medium, verifier `ok: true` | Pass | always |
| medium, verifier `ok: false` | **Warn** — attach to `explainability`; query still runs | phase 1 → later: clarify |
| high-unsupported | **Block** — `stopped_at="semantic_guard"`, clear `error` message | phase 2 |

### Block message (high-unsupported)

```
当前 schema 中没有"删除/删除率"对应的字段、状态值或指标，无法安全生成 SQL。
```

The message names the unsupported concept and states no safe SQL exists — it does not invent a proxy.

### Warn annotation (medium, unsupported)

Surfaced through the existing [`explainability`](../../backend/app/agent/explainability.py) channel (`AgentState.explainability` dict), e.g. a `grounding_warnings` key listing each unsupported mapping. The analyst sees the result *and* the caveat.

### Fail-closed safety check

`sample_value_fallbacks` is, by name, a *fallback* list — not guaranteed exhaustive. Absence of a value there is a **signal, not proof** of non-existence. Before a **high-unsupported block** fires on a missing *status/enum value*, run a cheap `SELECT DISTINCT <column> LIMIT N` against the real column to confirm the value genuinely does not exist. This prevents blocking a value that exists in data but was never enumerated in the overlay. (Concept-level absence — no column/metric/description at all, as with `删除率` — does not need this check; there is no column to probe.)

### Datasets without an overlay

Overlays are per-dataset (`ecommerce.yml` is the demo). For datasets lacking one, Stage 1 degrades gracefully to schema-context metadata only; more queries fall to the `medium` band and lean on Stage 2. No dataset is left unprotected; coverage is just weaker without curated evidence.

---

## Data flow & state

New `AgentState` fields (additive to [`state.py`](../../backend/app/agent/state.py)):

- existing `error` / `stopped_at` reused for hard blocks (consistent with `sql_guard`).
- warnings ride on existing `explainability` dict (new key, no new top-level field needed) — or a dedicated `grounding_result` field if we want it typed. **Decision deferred to plan.**

No change to the SQL/JSON output contract. No change to `generate_sql` prompts (the guard is a separate node; a *light* prompt rule — "do not invent proxy metrics/filters from adjacent values" — may be added as a cheap first line of defense, but the guard does not depend on it).

---

## Rollout (eval-first)

1. **Phase 1 — warn-only.** Ship Stage 1 + Stage 2 in warn-only mode: every band that would block instead annotates `explainability`. Nothing is blocked. Add eval cases (unsupported concept, adjacent-value substitution, plus *negative* cases like rank correlation and legitimate refund-rate questions) and measure verifier precision/recall.
2. **Phase 2 — enable blocking.** Once precision on the high-unsupported band is acceptable, flip that band to fail-closed (with the distinct-values safety check). Medium band stays warn-only.
3. **Phase 3 (later, optional) — clarify.** Replace medium-band warnings with an interactive disambiguation turn ("删除率 在当前 schema 中没有直接对应。你是指 退款率 / 取消率 / 都不是?"). Higher build cost (mid-workflow suspend/resume); deferred until value is proven.

A setting (e.g. `semantic_guard_mode = off | warn | enforce`) gates the phases so rollout is reversible.

---

## Testing

- **Unit (Stage 1):** band assignment for grounded / analytical / high-unsupported / medium inputs, using fixture overlays. Assert `删除率` → high-unsupported, rank correlation → analytical, `销售额` → grounded.
- **Unit (Stage 2):** verifier prompt/parse with a mock provider; assert `ok:false` shape for substitution, `ok:true` for grounded.
- **Node integration:** `semantic_guard_node` sets `stopped_at`/`error` on block; annotates `explainability` on warn; passes through on grounded.
- **Negative regression:** Case 36 rank correlation and a legitimate `退款率` question must NOT be flagged.
- **Workflow:** blocked query never reaches `sql_guard`/`execute`.
- **Eval cases:** add unsupported-concept and adjacent-substitution cases to the manual/eval suite per audit "Future Hardening" item 4.

---

## Rejected alternatives

- **Option 1 — prompt-only rule.** "Do not create proxy metrics from adjacent status values." Cheap, near-free to add, but weak as the *sole* mechanism — no enforcement, no measurement. Kept only as an optional light first-line rule, not the guard.
- **Option 2 — generator self-reports `concept_mappings` in the same call.** Rejected: self-report bias (the generator defends its own substitution) and it re-touches the just-stabilized output contract.
- **Single-band boolean instead of confidence bands.** Rejected: forces one failure behavior for an unproven verifier. Banding lets high-confidence cases fail closed while uncertain cases warn, and supports eval-first rollout.

---

## Open questions for the plan

1. Typed `grounding_result` field on `AgentState` vs. a key inside `explainability`.
2. Exact concept-extraction approach in Stage 1 (lexical/alias overlap vs. embedding similarity vs. letting Stage 2 do all concept parsing for the medium band).
3. Where `semantic_guard_mode` lives in `Settings` and its default for tests vs. production.
