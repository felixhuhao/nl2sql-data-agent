# Semantic Grounding Guard — Design

**Date:** 2026-06-12
**Status:** Draft; Phase 2A refutation hardening implemented, Phase 2B promotion gate pending
**Related:** `docs/prompt-reliability-audit.md` (Manual Test Finding: Unsupported Concept Substitution)

---

## Problem

After SQL generation, the model sometimes answers an **unsupported business concept** by silently substituting a nearby *available* concept, or by dropping the concept entirely — in both cases changing the meaning of the result without telling anyone.

Observed cases:

- **Case 25 (substitution)** — `查看删除率趋势` ("deletion rate trend"). Schema has no deleted/deletion concept. Model emitted `countIf(fo.order_status = 'refunded') / count(*) AS deletion_rate`, silently turning *deletion rate* into *refund rate*.
- **Case 37 (omission)** — `删除的订单` ("deleted orders") became generic orders with no deleted filter at all.

Both pass every existing guard:

- [`_detect_blocked_intent`](../../../backend/app/agent/nodes.py) only catches *destructive intent* (DELETE/DROP/external file reads). A `SELECT` computing a refund ratio is not destructive.
- [`guard_sql`](../../../backend/app/sql_guard/guard.py) only checks command safety + column allow-listing. `order_status = 'refunded'` uses a perfectly valid allowed column.

This is a **semantic** failure wearing a **syntactic** disguise. It needs a new check in a dimension neither guard covers.

### Why a SQL-only check is insufficient

The overlay ([`ecommerce.yml`](../../../backend/app/metadata/semantic_overlays/ecommerce.yml)) documents `order_status` sample values as `paid`, `completed`, `refunded` — and **nothing for `deleted`**. So:

1. `删除` (deleted) has **zero evidence** across every channel (column name, description, display name, sample value, metric).
2. `refunded` **is** documented — which is *exactly why* the model grabbed it as a proxy.

A naive "is every column/value in the SQL grounded?" check **would pass** Case 25, because `refunded` is genuinely grounded. The ungrounded thing is not the value — it is the **mapping from the question's concept (`删除率`) to that value**. And Case 37 has *no mapping in the SQL at all*. So the check must be **question-aware**: it has to start from what the question asks and reason about both substituted and omitted concepts, looking at the question, the schema, and the SQL together (and, for any judgment that may justify a block, the *full* schema metadata — not the focused retrieval subset). An allow-list / SQL-only shortcut cannot work.

---

## Design philosophy: notice vs. prove

Two responsibilities, deliberately separated:

- **The LLM verifier *notices* the semantic mismatch.** Identifying that "删除率" was answered with a refund proxy, or silently dropped, is an interpretive judgment. Doing this deterministically would require a hand-written concept parser — a token/pattern list mapping phrases to concepts — which is precisely the brittle, schema-independent keyword hardcoding the audit spent 15 findings removing. **We do not build that.** Interpretation stays with the LLM.
- **The deterministic layer supplies *court-admissible evidence*.** Given a concept the verifier flagged, it checks schema-derived facts: is this concept absent from *every* evidence channel of the active datasource? Is the proxied value provably impossible (`SELECT DISTINCT` returns nothing)? This is not interpretation; it is corroboration. It can only **refute** a mapping (confirm it is unsafe); it can **never assert support**.

A hard block requires both: the LLM says unsupported *and* the deterministic audit confirms the refutation. The LLM provides the suspicion; the deterministic layer provides the proof. This keeps the mechanism non-brittle (no speculative concept parser, no keyword patches) and makes every block explainable.

---

## Scope

### In scope

- Detect when a **business entity, filter, status value, or named metric** the question requires is unsupported by schema evidence — whether the SQL **substitutes** a proxy for it (Case 25) or silently **omits** it (Case 37).
- Respond with a graded action: warn (visible) or, once earned, hard-block.
- Eval-first rollout: warn-only first, enforcement only after evidence justifies it.

### Out of scope (must NOT be flagged)

- **Analytical operations** — rank correlation, YoY/MoM, moving average, TopN, share/ratio of supported measures. These need *compatible measures/dimensions*, not same-named columns. (Case 36, rank correlation over `unit_price`/`quantity`, must stay valid.) This is an instruction to the verifier, not a deterministic rule.
- Any special-case keyword rule for `删除率`, `refunded`, or specific status values. The audit explicitly forbids this; the mechanism must be general and evidence-based.

---

## Architecture

A new node, `semantic_guard_node`, runs **inside the repair loop** ([`iter_sql_repair_events`](../../../backend/app/agent/repair.py)), on **every** SQL candidate — initial and repaired — after it passes syntax/scope (`sql_guard`) and before `execute_node`. It mirrors the existing [`sql_guard_node`](../../../backend/app/agent/nodes.py) pattern: read state, set `stopped_at`/`error` on a hard block, or attach a visible warning on a soft finding.

```
                 ┌──────────────── repair loop ────────────────┐
generate_sql ──► │ sql_guard ──► semantic_guard ──► execute      │ ──► finalize
                 │    │ fail          │ block          │ fail     │
                 │    └─► repair ◄────┘ (non-repairable)└─► repair │
                 └──────────────────────────────────────────────┘
                                    │
   semantic_guard ─ LLM verifier: concept extraction + grounding judgment (primary, interpretive)
                  └ deterministic refutation audit: corroborates a block, never interprets
```

**Placement rationale.** The original draft placed this between `generate_sql` and `sql_guard`, i.e. in the once-only pre-repair workflow. That is wrong: [`iter_sql_repair_events`](../../../backend/app/agent/repair.py) re-runs `sql_guard_node` at the top of every loop iteration and loops back after each `repair_sql_node`, so **repaired** SQL would re-hit `sql_guard` but bypass a pre-loop semantic guard entirely. Running inside the loop after `sql_guard` passes guarantees every candidate that could reach execution is grounded. Running *after* `sql_guard` (not before) means we only spend the semantic check on syntactically valid, in-scope SQL.

**A semantic block is non-repairable.** Unlike `scope_guard`/`syntax_guard` (in `REPAIRABLE_GUARD_STAGES`), a confirmed-unsupported block means the requested concept genuinely has no schema evidence — re-prompting the model would only produce another substitution. So `semantic_guard` adds a stage to `NON_REPAIRABLE_GUARD_STAGES` (alongside `operation_guard`): on a hard block it stops the loop and returns the error, it does not trigger `repair_sql_node`.

**Cost across repairs.** The check can fire up to `MAX_REPAIRS + 1` times (3×) per query. The question's required concepts and their support status do not change across repairs — only whether a given SQL grounds them does. The verifier is therefore split into two responsibilities with distinct interfaces (see Component 1): a question-invariant **extraction + support** stage that is computed once and cached on state, and a per-candidate **grounding check** that re-runs on each SQL. Only the latter repeats across repairs.

---

## Component 1 — Semantic verifier (LLM, primary)

A **fresh** LLM critic (not the generator marking its own homework), split into two responsibilities with distinct interfaces so the question-invariant part can be cached across repair candidates:

**Stage A — extraction + support (question-invariant, cached).** Input: `{question, full datasource metadata}`. **Not** the focused `schema_context` — support judgments that may justify a block must be made against the *complete* metadata (all tables, columns, aliases, metrics, sample values, verified queries), not the ranked top-K retrieval subset. Output: the list of business concepts the question requires, each with a stable `concept_id`, `concept_type`, supported / unsupported status, and evidence (or absence) behind it. Runs once per query; cached on state.

> Start from the **question**. List the business entities, filters, status values, and named metrics the question requires. For each, decide from the full schema metadata whether schema evidence supports it.

**Support semantics policy.** A concept is supported when the full metadata provides evidence for the business meaning through names, labels, descriptions, aliases, Metric Definitions, Verified Queries, SQL Generation Guidance, or sample values. Qualified entity requests preserve the qualifier/status/filter as a required concept: a supported base entity (`orders`) does not make an unsupported qualifier (`deleted`) supported. Value-derived metrics such as rates, shares, counts, and trends do **not** require an exact pre-defined Metric Definition when both the base column and the requested value meaning are documented (e.g. `order_status.refunded` documented as `已退款/退款` can support a refund-rate calculation). A documented value supports only its explicit business meaning and aliases; similarity, causality, or common co-occurrence with another lifecycle/payment/fulfillment outcome is not evidence. For status/value concepts, a related documented value cannot support a requested value that is not itself named, described, or aliased in metadata. Analytical operations (rank correlation, YoY/MoM, moving average, TopN, share/ratio over supported measures) need compatible supported measures/dimensions, not same-named columns.

**Stage B — grounding check (per candidate).** Input: `{unsupported required concepts from Stage A, deterministic SQL facts, candidate sql}`. For each *unsupported* concept, decide how the SQL handled it. Runs on every candidate (initial and repaired). Findings reference Stage-A `concept_id`s rather than re-extracting or renaming concepts, so concept identity is preserved by contract rather than prompt prose.

> For each unsupported concept, flag whether the SQL **substituted** a proxy for it OR **omitted** it entirely (e.g. the question asks for "deleted orders" but the SQL filters nothing). Do NOT flag analytical operations (rank correlation, YoY, moving average, TopN, share) — those require compatible measures/dimensions, not same-named columns.

**Deterministic SQL facts.** Before Stage B, the guard extracts general SQL facts that are not business interpretation, such as `forced_empty_result` for `WHERE FALSE`, `1=0`, `LIMIT 0`, or equivalent forced-empty predicates. These facts are supplied to Stage B and may produce an omission warning for already-extracted unsupported concepts; they never extract a concept from the question.

The split can collapse to a single combined call on the first candidate and reuse the cached Stage-A result for repaired candidates; what matters is that the cached interface (question→concepts) and the per-candidate interface (concepts+sql→findings) are distinct.

The verifier must catch **both** failure shapes — this is essential, because a SQL-mapping-driven check only sees the first:

- **Substitution** (Case 25): unsupported concept grounded in a *proxy* (`删除率` → `order_status = 'refunded'`). Visible in the SQL.
- **Omission** (Case 37): unsupported concept *silently dropped* (`删除的订单` → unfiltered orders). **Invisible** in the SQL — only catchable by starting from the question.

Returns structured output:

```json
{
  "ok": false,
  "issues": [
    {
      "concept_id": "c1",
      "concept": "删除率",
      "concept_type": "metric",
      "failure_kind": "substituted",
      "sql_mapping": "order_status = 'refunded'",
      "supported": false,
      "explanation": "Schema documents 'refunded' but defines no deleted/deletion concept; the mapping is invented."
    },
    {
      "concept_id": "c2",
      "concept": "删除的订单",
      "concept_type": "filter",
      "failure_kind": "omitted",
      "sql_mapping": null,
      "supported": false,
      "explanation": "Question requires a 'deleted' filter, but the SQL applies no such predicate; the concept is silently dropped."
    }
  ]
}
```

`failure_kind` is `substituted` (a proxy appears in the SQL) or `omitted` (`sql_mapping: null` — the concept was dropped). Both are unsupported-concept failures.

Why a separate call and not structured self-report from the generator (rejected Option 2): the model that substitutes `refunded` for `删除` is the same model that, asked to self-report in the same forward pass, will rationalize `supported: true`. A fresh critic has no investment in defending the SQL. It also avoids re-touching the SQL/JSON output contract stabilized in audit Findings 3/11/13.

### Verifier outage handling (fail open, never a new reliability cliff)

The call runs under a bounded timeout and a single attempt (no internal retry loop). On **timeout, provider error, empty response, or unparseable output**, the verifier result is **inconclusive**. There is no deterministic interpreter to fall back on (by design — see philosophy), so a verifier outage means *the semantic check did not run*:

- **Phase 1 (warn-only):** skip the warning, log `verifier_unavailable`. No user-facing impact beyond a missing advisory.
- **Phase 2 (enforce):** a hard block *requires* an LLM flag, so no flag means **no hard block** — the guard silently fails open. Because that is the highest-risk degraded state (enforcement is expected but not happening), its observability is **mandatory, not optional**, on three surfaces:
  1. a `verifier_unavailable` advisory in `grounding_warnings` on the response (so the user sees the answer was *not* semantically checked);
  2. a metric + structured log entry per occurrence (so the rate is alertable);
  3. exposure on the service health/status endpoint (so a sustained verifier outage degrades reported health rather than passing as green).

The deliberate consequence: enforcement never blocks on the deterministic layer alone — determinism corroborates, it never originates a block — so the fail-open window must be impossible to miss operationally.

---

## Component 2 — Deterministic refutation audit (corroboration only)

Runs over the concepts the verifier flagged. Its sole job is to answer, from schema-derived facts: **can this flagged mapping be proven unsafe?** It does not parse the question, does not decide what the question "means," and **cannot assert that any concept *is* supported** — only the verifier and the schema evidence together can refute.

**Implementation status (Phase 2A landed).** The runtime audit now builds datasource-scoped `SchemaEvidence` from the active datasource's table, column, sample value, alias, metric, and verified-query metadata. Evidence matching is entry-scoped, not one concatenated text blob, so short ASCII tokens such as `id` cannot match inside unrelated values such as `paid`, and concepts cannot be assembled across unrelated metadata fields. The audit confirms only absent requested concepts; any metadata evidence makes it abstain.

For each flagged concept, the audit confirms a refutation when the **requested** concept has **no match in any evidence channel of the active datasource**:

- `table_semantics` (display name, description, domain)
- `column_semantics` / `table_column_semantics`
- `dimension_columns` / `metric_columns`
- `sample_value_fallbacks`
- channels: labels, descriptions, aliases, Metric Definitions, Verified Queries, SQL Generation Guidance

The unsafe thing is the **absence of evidence for the requested concept**, not anything about the proxy. In Case 25, the refutation is confirmed because nothing in the schema links `删除率`/deletion to any column, value, or metric — *not* because `refunded` is impossible (`refunded` is valid and documented; that is irrelevant to whether `删除率` is supported). The audit never tests the proxy value's validity.

**Evidence must be full and datasource-scoped — not the focused context.** Two traps:

1. **Focused vs. full.** `state.schema_context` is the ranked, limited retrieval subset ([`build_focused_context_from_retrieval`](../../../backend/app/agent/nodes.py), top-K via `_rank(..., limit)`). "Absent from the focused context" is far too weak to justify a block — the concept may simply have fallen below the retrieval cutoff. The audit (and Stage A) therefore query the **full datasource metadata** ([`build_schema_context`](../../../backend/app/metadata/service.py) / the underlying stores), enumerating *all* tables, columns, aliases, metrics, sample values, and verified queries. A useful side effect: auditing against full metadata is exactly what catches a verifier false-positive caused by the focused context — if the LLM flagged a concept only because it was retrieval-pruned, the full-metadata audit finds the evidence and abstains, downgrading the block to (at most) a warning.
2. **Datasource scope.** The overlay is global: [`_overlay_path`](../../../backend/app/metadata/semantic_overlay.py) resolves a single configured path (`ecommerce.yml`) with no datasource parameter. Using it as evidence for a *different* datasource would supply false evidence, so the overlay is consulted only when explicitly bound to the active datasource (open question); the datasource's own full metadata is always the primary source.

**`SELECT DISTINCT` confirmation — for the *requested* value, never the proxy.** This applies only to the case where the *question itself names a value* (e.g. "status = cancelled") that is absent from `sample_value_fallbacks`. Because that fallback list is, by name, not guaranteed exhaustive, absence there is a signal, not proof. Phase 2A implements a guarded, bounded `SELECT DISTINCT <column> FROM <table> LIMIT 1000` probe when the verifier supplies a metadata-validated `target_table`, `target_column`, and `requested_value`; the exact `concept_type` label is advisory. If those target fields are missing, the audit may recover a target by parsing the verifier's own `sql_mapping` with the active datasource dialect, but only when the mapped literal appears in the flagged requested concept text, so proxy values such as `refunded` for `删除率` are never probed. If the requested value is genuinely absent, the refutation is confirmed; if it exists, the audit abstains. If the target is ambiguous, hallucinated, or the probe fails, the audit also abstains. The probe is never run against a substituted proxy value. Concept-level absence — no column/metric/description at all, as with `删除率` — needs no probe; there is no column to query.

**The audit can only refute.** A "no evidence found" result confirms a refutation. A "found something" result does **not** clear the verifier's finding — it only means the deterministic layer abstains, leaving the finding as a warning. Determinism is never the reason a query passes.

---

## Decision logic

| `semantic_guard_mode` | verifier `ok:true` | verifier `ok:false`, refutation **confirmed** | verifier `ok:false`, refutation **not confirmed** | verifier unavailable |
|---|---|---|---|---|
| `off` | pass | pass | pass | pass |
| `warn` | pass | **warn** (visible) | **warn** (visible) | skip + log `verifier_unavailable` |
| `enforce` | pass | **block** (non-repairable) | **warn** (visible) | no block; **mandatory** `verifier_unavailable` advisory + metric/log + health degrade |

**Hard block condition (the only path to a block):**

```
semantic_guard_mode == "enforce"
AND verifier.ok == false
AND deterministic_refutation.confirmed == true
```

**Medium / uncertain stays warn-only forever** unless a deterministic refutation pattern has been proven for it. A verifier finding the audit cannot corroborate never escalates to a block on its own, in any mode. Enforcement is earned per pattern, from eval evidence — never granted by default.

### Block message (confirmed unsupported)

```
当前 schema 中没有"删除/删除率"对应的字段、状态值或指标，无法安全生成 SQL。
```

Names the unsupported concept and states no safe SQL exists — it does not invent a proxy.

### Warnings must be *visible*

A warning that only lives in [`explainability`](../../../backend/app/agent/explainability.py) is too easy to miss — the analyst would see numbers that silently mean something other than what they asked, the exact failure we are preventing. So warnings are a **first-class, visible part of the response payload**:

- A dedicated, typed `grounding_warnings` field on `AgentState`, populated by `semantic_guard_node` (each entry: concept, `failure_kind`, the proxy/omission, refutation status, and a plain-language caveat).
- Included in the **API response payload** (chat result / SSE final event) alongside the result, not only inside `explainability`.
- The **UI surfaces it prominently** next to the result table (e.g. a banner: "结果可能不准确：'删除率' 在当前数据中没有定义，已用近似口径/未过滤"), so it cannot be overlooked.

Explainability may *additionally* carry the detailed evidence trail, but visibility does not depend on the user opening it.

### Datasets without an overlay

Overlays are per-dataset (`ecommerce.yml` is the demo). For datasets lacking one, the refutation audit draws on schema-context metadata only. Fewer refutations will be confirmable, so more findings stay warnings rather than blocks — a safe degradation, not a gap: no dataset is left unprotected, coverage is just weaker without curated evidence.

---

## Data flow & state

New `AgentState` fields (additive to [`state.py`](../../../backend/app/agent/state.py)):

- existing `error` / `stopped_at` reused for hard blocks (consistent with `sql_guard`); `semantic_guard` is added to `NON_REPAIRABLE_GUARD_STAGES` in [`repair.py`](../../../backend/app/agent/repair.py).
- a dedicated, typed `grounding_warnings: list[...]` field (not just an `explainability` key) carried into the response payload and UI. Each entry: concept, `failure_kind` (`substituted` / `omitted` / `verifier_unavailable`), the proxy or omission, refutation status, and a plain-language caveat.
- a cached `required_concepts` (or equivalent) so the verifier's extraction is computed once and reused across repair iterations.
- a cached `schema_evidence` object so the deterministic refutation audit builds full datasource evidence once per query/datasource, not once per repair candidate.

The API response model (chat result / SSE final event) is extended to include `grounding_warnings`. No change to the SQL/JSON *generation* output contract. No change to `generate_sql` prompts (the guard is a separate node; a *light* prompt rule — "do not invent proxy metrics/filters from adjacent values" — may be added as a cheap first line of defense, but the guard does not depend on it).

---

## Rollout (eval-first)

1. **Phase 1 — verifier-first, warn-only.** The LLM verifier does *all* extraction and grounding judgment. The deterministic refutation audit runs **observation-only**: it computes whether it *would* confirm each finding and logs the result alongside the verifier output, but it never bands or blocks. Nothing is blocked; verifier `ok:false` produces a visible warning. No hand-written concept rules exist. The logged `{question, sql, verifier finding, would-confirm}` tuples become an **eval corpus** measuring verifier precision/recall and how often the deterministic audit agrees.
2. **Phase 2 — earned enforcement.** From the eval corpus, promote **only** the deterministic refutation patterns the data proves safe (high agreement, no false confirmations). A hard block then requires the double gate: verifier `ok:false` **and** a proven deterministic refutation confirmed. Findings without a proven refutation pattern stay warn-only.
3. **Phase 3 (later, optional) — clarify.** Replace warnings with an interactive disambiguation turn ("删除率 在当前 schema 中没有直接对应。你是指 退款率 / 取消率 / 都不是?"). Higher build cost (mid-workflow suspend/resume); deferred until value is proven.

A setting `semantic_guard_mode = off | warn | enforce` gates the phases so rollout is reversible. The verifier uses a separate `semantic_guard_timeout` budget; the initial default is 30 seconds because Stage A reads full datasource metadata and is cached across repaired candidates.

### Phase 2 Promotion Criteria

Promotion is decided per named `promotion_pattern`, never from an aggregate verifier score. The first candidate pattern is `concept_absent_full_metadata`: the LLM flags an unsupported concept and the deterministic refutation audit confirms the concept is absent across full datasource metadata. A pattern can enter the `enforce` double gate only when a fresh pattern-scoped eval run satisfies all of these:

- At least 20 total eval cases tagged with the pattern.
- At least 10 unsupported workflow cases with expected warnings, covering both `substituted` and `omitted` findings.
- At least 5 positive-schema verifier-only cases where a schema truly supports adjacent concepts such as returned/cancelled/deleted/shipped, and every one passes.
- At least 3 negative-schema verifier-only cases where related-but-different metadata is present and every one is correctly unsupported.
- `false_confirmed_warning_cases == 0`: no expected-no-warning case may produce a confirmed refutation.
- Verifier-unavailable cases are inconclusive, not semantic failures. They must be rerun as targeted cases before promotion evidence is counted; availability is reported separately from semantic correctness and never becomes a deterministic block.
- The implementation still requires the double gate: LLM verifier finding **and** promoted deterministic refutation. Unpromoted patterns remain warn-only, even in `enforce` mode.

---

## Testing

- **Unit (verifier):** prompt/parse with a mock provider. Assert `ok:false` for both `failure_kind: substituted` (Case 25) and `failure_kind: omitted` (Case 37, no filter in SQL), `ok:true` for a grounded question. Assert outage handling (`verifier_unavailable`) on timeout/error/unparseable output.
- **Unit (refutation audit):** given a flagged concept, confirms refutation when absent across all channels; **abstains** (does not confirm) when any evidence exists; never emits a "supported" verdict. Datasource scoping: an overlay describing datasource A must NOT supply evidence when the active datasource is B. `SELECT DISTINCT` path: confirms when the value is absent in data, abstains when present.
- **Decision logic:** the matrix above — block only when `enforce ∧ ok:false ∧ refutation confirmed`; warn when refutation not confirmed in any mode; verifier-unavailable never blocks.
- **Repair-loop integration:** a **repaired** candidate is still semantically guarded (the original-draft bypass must not regress); a confirmed-unsupported block is non-repairable (does not trigger `repair_sql_node`); concept extraction is cached, not recomputed per iteration.
- **Negative regression:** Case 36 rank correlation and a legitimate `退款率` question must NOT be flagged.
- **Workflow:** blocked query never reaches `execute`; `grounding_warnings` appears in the response payload.
- **Eval cases:** add unsupported-concept (substituted + omitted) and adjacent-substitution cases to the manual/eval suite per audit "Future Hardening" item 4.

---

## Rejected alternatives

- **Option 1 — prompt-only rule.** "Do not create proxy metrics from adjacent status values." Cheap, near-free to add, but weak as the *sole* mechanism — no enforcement, no measurement. Kept only as an optional light first-line rule, not the guard.
- **Option 2 — generator self-reports `concept_mappings` in the same call.** Rejected: self-report bias (the generator defends its own substitution) and it re-touches the just-stabilized output contract.
- **Deterministic-first concept extraction / confidence banding.** Rejected: extracting "what the question requires" with token/pattern lists is the keyword-rule trap the audit removed. Interpretation stays with the LLM; determinism is scoped to corroborating refutation.
- **Single-band boolean / block on verifier alone.** Rejected: blocking on an unproven verifier with no corroboration maximizes false-positive blocks. The double gate makes blocks explainable and earned.

---

## Open questions for the plan

1. **Overlay→datasource binding.** How an overlay is explicitly bound to a datasource (manifest field, per-datasource overlay path, or registry entry), so the audit consults an overlay only when it describes the active datasource. Until this lands, overlay evidence is used only for the datasource it actually describes.
2. Whether enforce-mode production should use the same provider/model as generation or a cheaper/faster verifier model.
