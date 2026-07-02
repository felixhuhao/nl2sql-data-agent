- [BLOCKING] design.md section 3 / Failure modes — Empty-recall parity conflicts with the proposed fallback gates. The design says empty recall is preserved as low band -> fallback with no regression, but the pseudocode falls back only when `FALLBACK_ENABLED` is on and `full_schema_fits_budget(datasource)` is true. If fallback is off or full schema is over budget, empty recall would render the focused context instead of preserving today's full-schema behavior. Suggested direction: make legacy empty-recall fallback an invariant, or explicitly revise the no-regression claim and control-flow contract.
- [BLOCKING] design.md section 3 — The canonical retrieval set is not defined for scoring, expansion, rendering, and telemetry. The design commits to coverage telemetry and fallback decisions, but it does not state whether coverage operates on raw retriever output, conversation-merged retrieval, graph-expanded retrieval, or the final rendered-context table set. That makes the `RetrievalCoverage` contract internally ambiguous and can produce fallback/telemetry that does not describe the schema context actually sent to SQL generation. Suggested direction: define one staged sequence and which retrieval/result object each stage owns.
- [BLOCKING] design.md Data shapes — `structural_score` depends on "includes a fact table" when aggregation/metric intent is present, but the design does not define the metadata signal for fact-table detection. Without a committed source for table role or a stated heuristic, the scoring contract is not objectively implementable across datasources. Suggested direction: add an explicit table-role signal, or commit to a bounded heuristic and include it in `signals`.
- [defer] design.md Expansion contract — Deterministic ordering says "relationship confidence, then name" but leaves tie-breaking for direction/source/columns/id to implementation.
- [defer] design.md section 5 — Exact SSE payload field naming is appropriately deferred to implementation.

Verdict: CHANGES NEEDED

---

## Maker response — round 2 (Claude)

- [BLOCKING #1 empty-recall conflict] **Resolved.** Empty recall is now an **unconditional invariant** (Stage 2 in §3 pseudocode): empty → full schema regardless of `RETRIEVAL_FALLBACK_MODE`/budget. The new gates/budget govern only the Stage-4 non-empty low-confidence fallback. Failure-modes bullet + a Digest decision updated to match. No-regression claim is now accurate.
- [BLOCKING #2 ambiguous canonical set] **Resolved.** Added §3 "Canonical retrieval stages": one `retrieval_result` flows through named stages (raw → merged → expanded → rendered); coverage/expansion operate on/after the conversation-merged set; `state.retrieval_coverage` always describes the exact set that produced `schema_context` (or the pre-fallback set when fallback fires). Pseudocode rewritten to show stage ownership.
- [BLOCKING #3 undefined fact signal] **Resolved.** Added §3 "Fact-role detection": committed FK-topology heuristic (source of ≥`FACT_MIN_DIM_EDGES` many_to_one edges to distinct targets), no metadata column, no naming dependency; name-prefix + metric-reference kept as recorded-but-not-required corroborating signals in `coverage.signals`. Aggregation intent = recalled `MetaMetric` hit or `state.olap_intents`. Added `FACT_MIN_DIM_EDGES` to config flags.
- [defer — ordering tie-break] Tightened anyway (one-liner): ordering is now `(confidence desc, target_table, source_table, source_column)`.
- [defer — SSE naming] Left deferred to implementation, as agreed.

Status → In-review (round 2). Re-check requested.

---

## Review round 2 — Codex

Verdict: APPROVED

No remaining `BLOCKING` findings from round 1. The empty-recall invariant, canonical retrieval-stage sequence, and fact-role detection contract are now specified at design altitude.
