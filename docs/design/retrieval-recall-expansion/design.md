# Design — Retrieval Recall Expansion + Confidence-Triggered Fallback

## Digest
- **Change:** Add deterministic graph expansion of recalled tables + a hybrid coverage score that triggers full-schema fallback on *low* confidence (not just empty recall).  ·  **Lane:** full (lightweight)  ·  **Status:** In-review
- **Decisions:**
  - **Coverage score = hybrid** (match strength × structural joinability) — why: a query can have strong lexical hits yet no joinable subgraph, or a joinable subgraph with weak scores; either alone misses a real failure — rejected: recall-strength-only (ignores structure), structural-only (misses weak/ambiguous recall).
  - **Expansion = 1-hop, fanout-gated, bidirectional, capped** — why: covers fact↔dim recovery both directions on a star schema while preserving the ~75% focused-context reduction — rejected: transitive (pulls whole star, redundant given fallback), fact-anchored (one-directional, misses dim→fact recovery).
  - **Two-stage recovery** — low confidence → expand → re-score → full-schema fallback only if still low — why: lets cheap deterministic expansion recover before paying full-schema token/hallucination cost — rejected: direct fallback (more full-schema hits, higher cost/hallucination).
  - **Full-schema fallback is itself size-capped** — fall back only when full schema fits a context budget; else keep best expanded focused context — why: honours the *Death of Schema Linking?* caveat (full schema helps only when it fits).
  - **Ships behind feature flags, default off** — why: eval-gated rollout, matches existing `semantic_mode` / `VECTOR_ENABLED` pattern; no behaviour change until calibrated.
  - **Fact-role = FK-topology heuristic** (source of ≥`FACT_MIN_DIM_EDGES` many_to_one edges), not a metadata column or name prefix — why: objectively implementable across datasources without a migration or naming dependency — rejected: new `role` column (migration), name-prefix-only (fragile for new sources).
  - **Empty-recall fallback is an unconditional invariant** — why: preserves today's behaviour exactly; the new flags/budget gate only the new non-empty low-confidence fallback.
- **Risks / watch:** score weights & threshold are eval-calibrated (not derivable a priori); expansion must respect analysis-space scoping or it breaks the shared governance model; must be a no-op (not a crash) on datasources with no relationships; coverage/telemetry always describes one defined retrieval stage (the rendered set), never a mix.
- **Open questions:** exact weights/threshold and the full-schema size budget — deferred to eval calibration (tracked in tasks.md), not blocking the interface design.
- **Drill down:** full design below · pros/cons in `design.html`.

---

## 1. Problem / context

Focused retrieval ([backend/app/metadata/retrieval.py](../../../backend/app/metadata/retrieval.py)) recalls tables/columns/metrics/aliases via rule + optional vector recall, and [build_context_node](../../../backend/app/agent/nodes.py) renders a focused schema context from the hits. Today the **only** safety net is: if retrieval is *empty*, fall back to full schema.

The gap (per schema-linking research, e.g. [Rethinking Schema Linking](https://arxiv.org/html/2510.14296v2), [Boundary-Aware NL2SQL](https://www.arxiv.org/pdf/2601.10318)): retrieval can be **non-empty but structurally incomplete** — it surfaces some tables but misses a needed dimension or the join path. That context is then *irrevocably lost*; the LLM generates SQL over an incomplete schema and there is no signal that anything was missing. This is the single highest-leverage accuracy failure in the pipeline and it is currently invisible.

## 2. Goals

**In scope:**
- A deterministic, non-LLM **graph expansion** step that grows a structurally-incomplete recall set along `MetaRelationship` edges.
- A **hybrid coverage score** on `retrieval_result` with a `high|low` band.
- **Two-stage recovery** control flow in the retrieve/build-context path: expand on low confidence, re-score, full-schema fallback only if still low.
- **Telemetry**: coverage score/band, expansion-used, fallback-used exposed on `AgentState` / SSE / eval report.
- **Eval cases** for structurally-incomplete recall, to calibrate the threshold.
- **Feature flags**, default off.

**Out of scope (non-goals):**
- Any LLM-based schema linking/expansion (that is a separate later roadmap item; this step is deterministic only).
- Changing rule/vector recall scoring itself.
- Multi-hop/transitive expansion.
- Changing SQL Guard, execution, or the semantic guard.

## 3. Proposed approach

Insert expansion + coverage between recall and context rendering. Pseudocode (control-flow contract, not final code):

```
# Stage 1 — raw recall (retrieve_context_node)
raw = retriever(question, datasource)                   # unchanged rule+vector recall

# Stage 2 — merge carried conversation assets (build_context_node, as today)
result = merge_prior_assets_into_retrieval(raw, conversation_context)

# INVARIANT (unchanged from today): empty recall always falls back to full schema,
# independent of RETRIEVAL_FALLBACK_MODE and the size budget.
if is_empty(result):
    state.retrieval_coverage = coverage_empty(fallback_used=True)
    return full_schema_context(datasource)

coverage = score_coverage(result, datasource)           # hybrid, on the merged set

# Stage 3 — deterministic expansion (recovery on low confidence)
if coverage.band == "low" and EXPANSION_ENABLED:
    result   = expand_via_graph(result, datasource)     # 1-hop, fanout-gated, capped, space-scoped
    coverage = score_coverage(result, datasource)       # re-score on the expanded set

# Stage 4 — NEW low-confidence fallback (gated + size-budgeted)
if coverage.band == "low" and RETRIEVAL_FALLBACK_MODE == "on" and full_schema_fits_budget(datasource):
    coverage.fallback_used = True
    state.retrieval_coverage = coverage
    return full_schema_context(datasource)

# Stage 5 — render focused context from the SAME set coverage was computed on
state.retrieval_coverage = coverage                     # telemetry describes the rendered set
return build_focused_context_from_retrieval(result, datasource)
```

In the **high-confidence common case nothing changes** — no expansion, no extra cost, same focused context and size. Expansion and the Stage-4 fallback engage only when recall is structurally incomplete *and* non-empty.

### Canonical retrieval stages (committed — resolves the "which set" ambiguity)

One `retrieval_result` object flows through named stages; each stage owns one transform, and `RetrievalCoverage` always describes the **latest** stage. The stored `state.retrieval_coverage` describes the set that actually produced `schema_context`:

1. **raw** — `retriever(question)` output (`retrieve_context_node`).
2. **merged** — raw + carried conversation assets via `merge_prior_assets_into_retrieval` (`build_context_node`, as today). Coverage and expansion operate on/after this stage, so carried context counts toward coverage and is never expanded away.
3. **expanded** — merged + graph additions; exists only when low-confidence recovery fires.
4. **rendered** — the set passed to `build_focused_context_from_retrieval`; identical to the expanded (or merged) set.

Full-schema fallback (the empty invariant, or Stage 4) bypasses focused rendering; in that case coverage records `fallback_used=True` and describes the pre-fallback set that triggered it. So telemetry and the fallback decision always refer to a **defined, single** set — never a mix of stages.

### Data shapes (committed)

- `RetrievalCoverage` carried on `retrieval_result` and mirrored on `AgentState`:
  - `match_strength: float` (0–1) — normalized from existing top-k recall scores.
  - `structural_score: float` in `{0.0, 0.5, 1.0}` — 1.0 if the set forms a join-connected component that includes a **fact-role** table when aggregation/metric intent is present; 0.5 if connected but missing the needed role, or intent is non-aggregating; 0.0 if dangling (no join path among recalled tables).
  - `score: float` = `w_strength * match_strength + w_struct * structural_score`.
  - `band: "high" | "low"` — `low` if `score < threshold`.
  - `expanded: bool`, `fallback_used: bool`, and the `signals` used (for eval/debug — includes the derived table roles and which fact-role signal fired).
- Config flags (mirror `semantic_mode` / `VECTOR_ENABLED` conventions): `RETRIEVAL_EXPANSION_ENABLED`, `RETRIEVAL_FALLBACK_MODE` (`off|on`), `RETRIEVAL_COVERAGE_THRESHOLD`, weight constants, `RETRIEVAL_EXPANSION_MAX_TABLES`, `FACT_MIN_DIM_EDGES`, full-schema size budget. Weights/threshold/budget/`FACT_MIN_DIM_EDGES` are **constants sourced from config**, values set by calibration.

### Fact-role detection (committed heuristic — resolves the undefined fact signal)

The design does **not** add a metadata column and does **not** depend on table naming. A table is treated as **fact-role** if it is the *source* side of at least `FACT_MIN_DIM_EDGES` (config, default 2) `many_to_one` `MetaRelationship` edges to distinct target tables — i.e. a topology hub of foreign keys. Corroborating signals (`fact_`/`dim_` name prefix; whether a recalled `MetaMetric` expression references the table) are **recorded in `coverage.signals` but not required**, so scoring is inspectable and degrades gracefully.

**Aggregation/metric intent** is present when the recall set includes a `MetaMetric` hit *or* the question carries an OLAP intent (`state.olap_intents`) — both already computed upstream. This relies only on `MetaRelationship` topology + existing recall/intent outputs, so the scoring contract is objectively implementable across datasources (including ones without the `fact_`/`dim_` convention).

### Expansion contract (committed)

- Traverse `MetaRelationship` **1 hop** from each recalled table, **both directions** (fact→dim and dim→fact).
- **Skip** edges with `fanout_risk == "high"`; `medium` allowed but counts against the cap.
- Add only tables within the **active analysis space** allowed set (governance scoping).
- Hard **cap** of `RETRIEVAL_EXPANSION_MAX_TABLES` added tables; deterministic ordering by `(confidence desc, target_table, source_table, source_column)` so results are fully stable and tie-breaks are unambiguous.
- Added tables carry the same focused-context shape as recalled tables (columns, sample values) via the existing context builder.

### Failure modes (committed)

- **No relationships** (e.g. a new datasource without inferred/overlay relationships): `expand_via_graph` is a **no-op**, coverage falls back to `match_strength` only, and full-schema fallback can still fire. Must not raise.
- **Full schema too large**: `full_schema_fits_budget` false → keep best expanded focused context (never emit an over-budget context). Log the decision.
- **Empty recall**: **invariant** — always falls back to full schema (Stage 2 check), independent of `RETRIEVAL_FALLBACK_MODE` and the size budget. This exactly matches today's behaviour; the new gates/budget govern **only** the Stage-4 non-empty low-confidence fallback. (Resolves the earlier no-regression conflict.)
- **Flags off**: entire path is inert; `build_context_node` behaves exactly as today.

## 4. Key decisions, alternatives & rationale (full lane)

### D1 — Coverage signal: **Hybrid** (chosen)
| Option | Pros | Cons |
|---|---|---|
| **Hybrid (chosen)** | Catches both failure shapes: strong hits with no join path, and a joinable set with weak scores. Directly models the "lost context" risk. | Two things to calibrate (weights + threshold). |
| Recall strength only | Simplest; reuses existing scores. | Blind to structure — passes a strong-but-disconnected recall straight to the LLM (the exact failure we target). |
| Structural only | Directly encodes joinability. | Misses weak/ambiguous recall where tables connect but the *right* ones weren't found. |

### D2 — Expansion depth: **1-hop, fanout-gated, bidirectional** (chosen)
| Option | Pros | Cons |
|---|---|---|
| **1-hop bidirectional (chosen)** | Recovers fact↔dim both directions; preserves ~75% context reduction; composes with two-stage fallback for deep cases. | Won't self-recover a 2-hop gap — but fallback covers that. |
| Fact-anchored (fact→dim) | Tightest; star-shaped. | One-directional; misses the common dim-recalled-but-not-fact recovery. |
| Transitive (multi-hop) | Highest recall. | Pulls the whole star; erodes context reduction, raises hallucination; **redundant** given full-schema fallback. |

### D3 — Fallback flow: **Two-stage recovery** (chosen)
Low confidence → expand → re-score → full schema only if still low. Rejected **direct fallback** (skip expansion): simpler control flow but more full-schema hits → higher token cost and hallucination, wasting the cheap deterministic recovery.

### D4 — Full-schema fallback is size-capped (chosen)
Fall back to full schema only when it fits a context budget; otherwise keep the best expanded focused context. Rationale: [Death of Schema Linking?](https://arxiv.org/pdf/2408.07702) — full schema rivals schema-linking *only when it fits*; an over-budget dump degrades accuracy.

### D5 — Ship behind flags, default off (chosen)
Eval-gate rollout; no behaviour change until weights/threshold are calibrated. Matches existing `semantic_mode`/`VECTOR_ENABLED` conventions.

## 5. Altitude — deferred to implementation (Codex)

- Exact function/dataclass names and whether `RetrievalCoverage` is a dataclass vs typed dict.
- SSE payload field naming and where in the event schema coverage lands.
- Line-level traversal code and the normalization formula for `match_strength`.
- **Calibrated values**: `w_strength`, `w_struct`, `RETRIEVAL_COVERAGE_THRESHOLD`, `RETRIEVAL_EXPANSION_MAX_TABLES`, full-schema size budget — set from eval, not in this design.

## 6. Open questions

- Final weights/threshold and the full-schema size budget → **eval calibration** (tasks.md). Not blocking: the interface commits to their existence and type; only the numbers are open.
- Whether `medium` fanout edges should count fractionally vs fully against the cap → default full; revisit only if calibration shows over-pruning.

## 7. Status

`Approved` — maker: Claude. Reviewer: Codex, APPROVED round 2 (zero BLOCKING). Revised against all three round-1 `BLOCKING` findings (empty-recall invariant, canonical retrieval stages, fact-role heuristic).
Approval: felixhuhao — `approved`.
