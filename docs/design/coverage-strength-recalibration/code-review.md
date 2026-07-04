# Code review — Coverage Match-Strength Signal

Change: `2404aba Add coverage match strength signal`
Range reviewed: `669e485..2404aba` (first review, branch point)
Design: [design.md](design.md) (REVIEWER-CLEAR) · [tasks.md](tasks.md)

## Verdict: CLEAN — no blocking findings

The committed diff faithfully realizes design §3 (Signal contract) and §3 (Scorer change, minimal):

- **Both paths emit** a top-level `coverage_match_strength: float ∈ [0,1]` — rule-only at `retrieval.py:158` (max lexical score ÷ 30), hybrid at `hybrid.py:93` (max of rule-floor, per-item `rule_score/30`, per-item `_vector_score`). Field name/placement matches the contractual shape committed in §3.
- **Scaler consumes it minimally** (`retrieval_coverage.py:221`) — prefers the explicit field, clamps to `[0,1]`, falls back to legacy `_match_strength` heuristic when absent (back-compat). The strength×structural blend, band threshold, expansion, and fallback control flow are untouched, preserving design decision B/D1.
- **Default stays off**; recalibration (pillar C, tasks §3) is intentionally deferred and unchecked, consistent with the roadmap `In-progress` state and the commit's "add the signal" scope. No premature default-on.
- **Tests are adequate** — they meaningfully exercise the contract: explicit-preferred over legacy (`test_score_coverage_prefers_explicit_coverage_match_strength`), clamp of out-of-range `2.0`→`1.0`, rule-path emission bounded `∈(0,1]` and `>0.7` on a non-VQ phrasing, hybrid rule-floor preservation when vector is lower, and hybrid reflection of top vector score (0.9 / 0.8). The vector-only-hit non-zero-strength case (the option-(a) failure the design avoids) is implicitly covered by the hybrid vector-score tests.

## Advisory findings (non-blocking — fix or waive at discretion)

- [x] `backend/app/metadata/retrieval.py:525` — the rule-only normalizer hardcodes `/ 30.0`, duplicating the canonical `MAX_LEXICAL_SCORE` (`retrieval_coverage.py:20`) and `MAX_RULE_SCORE` (`hybrid.py:24`), all currently `30.0`. If either constant is retuned during the §3 recalibration, this magic number will silently diverge and re-introduce the exact scale-conflation bug this change fixes. Suggested: reuse the shared constant (import or lift to a common module). **Disposition:** fixed with shared `score_constants.MAX_LEXICAL_SCORE`; `MAX_RULE_SCORE` now aliases it.
- [ ] `backend/app/metadata/hybrid.py:376` / `retrieval.py:516` — two private `_coverage_match_strength` implementations plus two `_clamp01` helpers exist. Minor duplication; consider a shared util, but harmless today.
- [ ] `backend/app/metadata/retrieval_coverage.py:222-226` — the `try/except (TypeError, ValueError)` branch for a non-numeric `coverage_match_strength` has no test. Low value, but the guard is untested.
- [ ] `tasks.md` §4 item 3 ("both retrieval paths populate `coverage_match_strength` on the same scale for an equivalent recall") is marked done, but no single test runs the *same* query through both paths to assert scale-comparability directly — it is argued only indirectly via independent per-path assertions. Acceptable given the per-path scale anchors (`/30` on both), but a direct parity test would lock the contract.

## Out of scope for this review (tracked in tasks.md §3, deferred by design)

- Vector-ON calibration sweep, threshold/weight re-derivation, default-on re-enable gate. These are the pending pillar-C tasks; their absence here is correct, not a defect.
