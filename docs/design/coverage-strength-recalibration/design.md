# Design — Coverage Match-Strength Signal + Vector-ON Recalibration

## Digest
- **Bottom line:** Approve if you agree the coverage over-firing under vector-active retrieval is fixed by having retrieval emit **one scale-faithful `[0,1]` `coverage_match_strength`** (from both the rule-only and hybrid-merge paths) that `score_coverage` consumes — **leaving the structural blend and recovery control flow untouched** — and then **re-calibrating the threshold with vector ON** before default-on is re-enabled.  ·  **Lane:** full (lightweight)
- **Change:** Replace the scale-guessing `_match_strength` heuristic with an explicit, path-independent strength signal emitted by the retrieval layer, and re-run calibration in the real vector-active config.
- **Decisions:**
  - **(A) Strength source = explicit `coverage_match_strength` from the merge (option b)** — why: `hybrid_merge` and the rule-only path each know their own confidence; each emits one `[0,1]` value meaning the same thing, so a threshold is comparable across rule/vector/hybrid — rejected: (a) raw rule strength pre-normalization (vector-only hits read as 0), (c) scale-detection inside `score_coverage` (heuristic, fragile).
  - **(B) Do NOT change the structural blend or the gate logic** — why: the bug is the *strength input's scale*, not how signals combine; keeping the linear `strength × structural` blend preserves design D1 (a connected-but-wrongly-matched recall must still be catchable) — rejected: "structural=1.0 gates recovery" shortcut (deletes D1's signal).
  - **(C) Re-calibrate with vector ON, on a corpus incl. non-verified-query phrasings** — why: the original `0/61` was measured rule-only, the one config where the bug is invisible; VQ-phrasings mask it — rejected: reuse prior calibration.
  - **(D) Re-enable default-on only if it clears the vector-ON gate** — why: same recovery + zero-regression bar, now measured in the config the app actually runs.
- **Risks / watch:** the emitted strength must be present on **both** retrieval paths or the conflation persists; back-compat — callers/fixtures without the field must not crash; recalibration may shift threshold *and* weights, not just threshold.
- **Open questions:** the normalization curve for `coverage_match_strength` and the recalibrated threshold/weights — pinned during calibration, not guessed here.
- **Drill down:** full design below · pros/cons in `design.html`.

---

## 1. Problem / context

Default-on shipped calibrated at `0/61` regressions — but that sweep ran **rule-only** (`--provider mock`, `VECTOR_ENABLED=disabled`). Under the real app config (vector `auto`), diagnostic evidence shows **56/75 non-closeout smoke cases fall back to full schema**, and **44 cases have `structural_score=1.0` but `band=low`**: correctly-recalled, structurally-complete queries are dumped to full schema, silently discarding the ~65–75% context-reduction win.

**Root cause:** `_match_strength` ([retrieval_coverage.py:221](../../../backend/app/metadata/retrieval_coverage.py#L221)) conflates two score scales — lexical (0–30, `top>1.0` → ÷30) and hybrid/vector (already ~0–1, `top<=1.0` → used as-is). A strong hybrid recall of `~0.33` is treated as *weak*, so `0.5·0.33 + 0.5·1.0 = 0.665 < 0.70` → `low` → expand → (no strength gain) → **full-schema fallback**. The threshold `0.70` was tuned on the lexical scale, so it ships mistuned for the scale the app actually produces.

## 2. Goals

**In scope:**
- An explicit `coverage_match_strength ∈ [0,1]` emitted by the retrieval layer on **both** the rule-only and `hybrid_merge` paths, meaning the same thing on each.
- `score_coverage` consumes it (legacy `_match_strength` retained only as an absent-field fallback).
- Re-calibration of the coverage threshold (and weights if needed) with **vector ON**, on a corpus that includes non-verified-query phrasings.
- Diagnostic regression fixtures so this specific failure can't silently return.

**Out of scope (non-goals):**
- Changing `structural_score`, the strength×structural blend, expansion, the fallback control flow, or the empty-recall invariant.
- Changing rule/vector recall themselves (we only *emit* a strength signal from what the merge already computes).
- Any LLM involvement.

## 3. Proposed approach

### Signal contract (committed)

- `retrieve_metadata_assets` returns `coverage_match_strength: float ∈ [0,1]` at the top level of the result, on **both** paths:
  - **hybrid path** — `hybrid_merge` derives it from the confidence it already has (`_vector_score` cosine + lexical), normalized to `[0,1]` so "a good recall" lands near the same value as on the rule path.
  - **rule-only path** — normalize the lexical top score to `[0,1]` (today's ÷`MAX_LEXICAL_SCORE`), but now **owned by retrieval and labelled**, not re-derived by the scorer.
- The value is **path-independent**: the number reflects match quality, not which channel produced it. This is the property the current code lacks and the threshold depends on.

### Scorer change (committed, minimal)

- `score_coverage` uses `retrieval_result["coverage_match_strength"]` as `match_strength` when present; falls back to the existing `_match_strength(retrieval_result)` heuristic when absent (back-compat for fixtures/callers).
- **Everything else in `score_coverage` is unchanged** — the `strength·w + structural·w` blend, the band threshold comparison, `structural_score`, fact-role detection, expansion, fallback. Only the *source* of `match_strength` changes.

### Recalibration protocol (committed)

- Run the calibration sweep with **`VECTOR_ENABLED=auto` (or enabled)**, against a corpus that includes **non-verified-query phrasings** (e.g. `各渠道销售额`, `各品类销售额`, `按性别统计销售额` — the diagnostic cases), not only VQ-aligned questions.
- Re-derive the threshold, and re-tune weights if the faithful strength scale changes their balance.
- **Gate:** recovery target **and** zero high-confidence regression, both measured in the **vector-active** config. Re-enable default-on only if it clears there; otherwise ship the corrected scorer opt-in and iterate.
- Write the chosen threshold/weights back into this design's open questions and `retrieval-recall-expansion/design.md`.

### Failure modes (committed)

- `coverage_match_strength` absent (older caller/fixture) → scorer falls back to legacy heuristic; no crash.
- Value out of range → clamp to `[0,1]`.
- Signal emitted on only one path → **treated as a bug** (defeats the purpose); tests assert both paths set it on the same scale.
- Empty-recall invariant and all other control flow: unchanged.

## 4. Alternatives & rationale (full lane)

### (A) Strength source
| Option | Pros | Cons |
|---|---|---|
| **(b) Explicit signal from merge (chosen)** | One `[0,1]` value across rule/vector/hybrid; handles vector-only hits; threshold becomes meaningful. | Merge must define a normalized confidence (calibrated). |
| (a) Raw rule strength pre-normalization | Small change; restores lexical scale. | Vector-only hits have no rule score → read as 0 strength, under-counting semantic recall. |
| (c) Scale-detection in `score_coverage` | Least invasive; no merge change. | Heuristic scale-guessing; fragile across score distributions — the current bug in a new form. |

### (B) Combine logic — keep the blend (chosen)
Rejected the "`structural=1.0` gates recovery" shortcut: it looks clean but contradicts **D1** (a joinable subgraph with genuinely weak match must stay catchable). The fix is a *faithful* strength input, not deleting strength's influence.

### (C) Recalibration config — vector ON (chosen)
Rejected reusing the prior rule-only calibration — it's the exact config that hid the bug.

## 5. Altitude — deferred to implementation

- The normalization curve for `coverage_match_strength`, and how `hybrid_merge` derives its internal confidence (tuned during calibration).
- The recalibrated threshold/weight numbers.

(The field **name and placement are contractual** — top-level `coverage_match_strength: float ∈ [0,1]` on the retrieval result, committed in §3 — not deferred. Only the value's computation is deferred.)

## 6. Open questions

- Final threshold + weights on the faithful scale → **resolved by validation**: keep threshold `0.7`, strength weight `0.5`, structural weight `0.5`. Vector-active calibration at `0.7` passed recovery `1/1`, fallback paths `1/1`, and high-confidence regressions `0/66`; `0.8` produced `2/66` regressions.
- Whether the rule-only and hybrid normalizations need slightly different curves to be truly comparable, or one shared curve suffices → **one shared scale is sufficient for the current corpus**. The added non-VQ metric-by-dimension cases stay high-confidence with focused context.

## 7. Approval

Maker: Claude · reviewer: Codex (REVIEWER-CLEAR, zero BLOCKING). Re-enabling default-on is gated on §3 vector-ON calibration; the default is reverted to off in the meantime (operator). Live status is tracked in `roadmap.md`.
Approval: felixhuhao — `approved`. Approves the **approach**; default-on re-enable remains gated on the (C)/(D) vector-ON calibration outcome.
