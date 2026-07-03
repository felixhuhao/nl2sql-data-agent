# Tasks — Coverage Match-Strength Signal + Vector-ON Recalibration

Implementer checklist (design altitude). Design: [design.md](design.md).

## 1. Emit the strength signal (pillar A)
- [x] `hybrid_merge` emits `coverage_match_strength ∈ [0,1]` from its own `_vector_score`/lexical confidence, normalized so a good recall lands comparably to the rule path.
- [x] Rule-only path in `retrieve_metadata_assets` emits `coverage_match_strength ∈ [0,1]` (lexical top normalized), labelled and owned by retrieval.
- [x] Both paths set the field on the same scale; clamp to `[0,1]`.

## 2. Consume it in the scorer (minimal)
- [x] `score_coverage` uses `retrieval_result["coverage_match_strength"]` when present; else falls back to legacy `_match_strength`.
- [x] No change to `structural_score`, the blend, the band threshold, expansion, fallback, or the empty invariant.

## 3. Vector-ON recalibration (pillar C)
- [x] Add non-verified-query "metric by dimension" cases to the calibration corpus (e.g. `各渠道销售额`, `各品类销售额`, `按性别统计销售额`).
- [ ] Run the calibration sweep with `VECTOR_ENABLED=auto`; record recovery · high-conf regressions · context-size delta per threshold.
- [ ] Re-derive threshold (and weights if needed) on the faithful scale.
- [ ] Gate: recovery target + 0 regression, both in vector-active config. Write chosen values into design.md open questions + `../retrieval-recall-expansion/design.md`.
- [ ] Re-enable default-on **only if** the vector-ON gate passes; else keep opt-in and iterate.

## 4. Regression protection
- [x] Add a test/fixture asserting a structurally-complete, hybrid-recalled query (non-VQ phrasing) scores `band=high` under vector-active retrieval.
- [x] Add a test asserting both retrieval paths populate `coverage_match_strength` on the same scale for an equivalent recall.
- [ ] Capture the "56/75 fallback under vector" observation as a guarded regression check (or documented eval baseline) so it can't silently return.

## 5. Tests
- [x] Unit: `score_coverage` prefers `coverage_match_strength` when present; legacy fallback when absent; out-of-range clamped.
- [x] Unit: vector-only hit yields non-zero strength (the (a)-option failure this avoids).
- [x] Full backend suite green with the current default-off safety setting.
