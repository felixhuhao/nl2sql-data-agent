# Tasks — Retrieval Recall Expansion + Confidence-Triggered Fallback

Implementer checklist (design altitude — tasks, not line-level code). Design: [design.md](design.md).

## 1. Coverage scoring
- [x] Define `RetrievalCoverage` shape (`match_strength`, `structural_score`, `score`, `band`, `expanded`, `fallback_used`, `signals`).
- [x] `score_coverage(retrieval_result, datasource)` — hybrid: normalize existing recall scores → `match_strength`; compute `structural_score` from `MetaRelationship` join-connectivity + fact-table presence.
- [x] Add weight/threshold constants to config (`RETRIEVAL_COVERAGE_THRESHOLD`, weights); read via `get_settings()`.

## 2. Graph expansion
- [x] `expand_via_graph(retrieval_result, datasource)` — 1-hop, bidirectional traversal over `MetaRelationship`.
- [x] Skip `fanout_risk == "high"` edges; count `medium` against the cap.
- [x] Scope additions to the active analysis space allowed tables.
- [x] Enforce `RETRIEVAL_EXPANSION_MAX_TABLES` cap with deterministic ordering (confidence, then name).
- [x] No-op safely when the datasource has no relationships (no raise).

## 3. Control flow wiring
- [x] Integrate two-stage recovery in `retrieve_context_node` / `build_context_node` per the design pseudocode.
- [x] `full_schema_fits_budget(datasource)` gate; keep expanded focused context when full schema is over budget.
- [x] Preserve empty-recall → fallback parity (no regression).
- [x] Gate the whole path behind `RETRIEVAL_EXPANSION_ENABLED` / `RETRIEVAL_FALLBACK_MODE`; defaults off pending vector/hybrid coverage recalibration with explicit opt-in support.

## 4. Telemetry
- [x] Carry `retrieval_coverage` on `AgentState`.
- [x] Emit coverage score/band, `expanded`, `fallback_used` in the SSE payload.
- [x] Add the same fields to the eval report (alongside existing `fallback_used`).

## 5. Eval + calibration
- [x] Add structurally-incomplete-recall cases to `evals/smoke_cases.yaml` for missing join path and dangling recall.
- [x] `missing_dimension` cases — not needed; archetype determined non-reproducible (retrieval recalls dims via non-alias channels). Closed.
- [x] Run eval to **calibrate** weights, threshold, `MAX_TABLES`, and the full-schema size budget.
- [x] Verify high-confidence cases are unchanged (0/61 high-confidence regressions at threshold `0.7`).
- [x] Enable flags by default — shipped default-on after vector-active recalibration (`coverage_match_strength`, 0/66 high-conf regressions at threshold `0.7`); see `../coverage-strength-recalibration/`.

## 6. Tests
- [x] Unit: `score_coverage` bands on strong-disconnected, weak-connected, empty, and healthy recalls.
- [x] Unit: `expand_via_graph` fanout gating, cap, analysis-space scoping, no-relationships no-op.
- [x] Unit: two-stage control flow (expand-recovers vs falls-back-to-full vs stays-focused).
- [x] Regression: flags-off path identical to current behaviour.
