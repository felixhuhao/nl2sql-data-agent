# Code Review — Retrieval Expansion Closeout

- **Skill:** `diff-review` (contract §7). Reviewer: GLM (OpenCode) — independent of the implementer (Codex).
- **Scope:** uncommitted diff (`git diff`) for slug `retrieval-expansion-closeout`, reviewed against `design.md` + `tasks.md`.
- **Method:** direct §7 review (did not wrap superpowers' `code-review` engine — recommended, not required). Tests **read** for adequacy, not run (that is `validate`'s job).
- **Verdict (round 2 re-review): CLEAN — no blocking issues.** All round-1 blocking/minor findings verified resolved in code (not just checked off). Proceed to `validate` / `ship`. Open items below are operator/scope decisions or a low-exposure Minor note — none block.

Files touched: `scripts/run_smoke_eval.py`, `evals/smoke_cases.yaml`, `backend/tests/test_smoke_eval_runner.py`, `docs/EVALUATION_DESIGN.md`, `docs/design/roadmap.md` (+ unrelated doc edits — see Important #3).

---

## Blocking

- [x] **`scripts/run_smoke_eval.py` `_retrieval_calibration_row` + `_run_case` (pre_coverage) — calibration zero-regression check is vacuous; holdout is threshold-relative.** `_retrieval_calibration_row` defines the high-confidence holdout as cases where `retrieval_pre_coverage["band"] == "high"`, and `pre_coverage` is computed inside `_run_case` under `_temporary_retrieval_recovery(threshold)` — i.e. at the **swept** threshold (`score_coverage` line 89: `band = "low" if score < threshold else "high"`). Two compounding defects: (1) the holdout membership drifts with the knob being tuned, so it is not the **fixed large-N corpus** design §B anti-overfit point 3 commits to ("Bind zero-regression to the large corpus. The 51 … high-confidence smoke cases are the 'must stay high' holdout"); (2) expansion and fallback are both gated on `coverage.band == "low"` (`backend/app/agent/nodes.py:261,274`), so a `pre_band=="high"` case can **never** expand or fall back → its `post_band` is always `high` and `flags_on_context_delta` is always `0` → `high_conf_regressions` is **always 0 by construction**. The headline safety guarantee ("0 regressions on 51+ cases") is therefore meaningless, and a boundary case that is fine at the production threshold but over-expanded at a higher swept threshold is *excluded* from the holdout (it reclassifies to low) and thus never counted. — **Fixed:** `_run_case` now records `retrieval_reference_coverage` at the production threshold before the sweep, `_retrieval_calibration_row` uses that fixed reference holdout, and the calibration report prints the reference threshold.

- [x] **`backend/tests/test_smoke_eval_runner.py` `test_retrieval_calibration_row_tracks_recovery_and_high_conf_regression` — test encodes an impossible scenario, giving false confidence.** The `regressed` fixture has `pre_band="high"` **and** `flags_on_context_delta=5`, and the test asserts it is counted as a regression. As shown above, a `pre_band=="high"` case can never produce a non-zero delta in real execution (expansion is gated on low band), so this input cannot occur in production. The test "passes" but validates regression detection against a state the harness can never produce — it masks the blocking defect above rather than guarding against it. — **Fixed:** the unit now uses a boundary regression that is high at `retrieval_reference_coverage`, low at the swept threshold, and has a non-zero context delta.

## Important

- [x] **`evals/smoke_cases.yaml` closeout fixtures are not self-contained — `score_coverage` reads the *live* relationship graph, not the fixture.** `_case_retrieval_result` injects only `tables/columns/metrics/verified_queries`; the structural half of the coverage score (`_structural_score` → `_is_join_connected`, `_fact_role_tables`) and `expand_via_graph` both consult `_relationships(datasource_name)` / `_allowed_tables` from SQLite (`retrieval_coverage.py:48-49,314`). So `missing_join_path` validity (`pre_band=low`) requires `dim_channels`↔`fact_order_items` to have **no direct edge** in seed metadata, and its **recovery** (`expanded=true`, `post_band=high`) requires a live 1-hop bridging edge (`fact_order_items`↔`fact_orders`↔`dim_channels`) that the fixture does not declare. This couples "deterministic fixtures" (design §A) to mutable metadata state; a seed-graph change flips the case from passing to failing for the wrong reason. Failures are loud (the `pre_band` assertion catches drift), so not silent, but the cases do not "isolate one archetype" on their own. — **Fixed by documentation:** `tasks.md` and `docs/EVALUATION_DESIGN.md` now state that fixtures stub recalled assets only and still depend on seeded `MetaRelationship` graph shape.

- [ ] **Diff bundles unrelated doc additions outside this change's scope.** `docs/AGENT_WORKFLOW.md` (manual-vs-agentic audit + agentic roadmap), `docs/METADATA_SEMANTIC_LAYER.md` (overlay vs auto-discovery), and the "方法学定位：与市场标准对照" section of `docs/EVALUATION_DESIGN.md` are not in `tasks.md` and are unrelated to retrieval closeout. Contract §6 makes each change a self-contained package; mixing them in muddies this review and the eventual PR digest. — **Required change:** split these into their own commits/changes (or a separate doc change) so the closeout PR diff maps 1:1 to `tasks.md`.

- [ ] **`tasks.md` §1 — `missing_dimension` archetype unvalidated (recovery evidence rests on 2 of 3 archetypes).** Disclosed/blocked (fact-only metric intent scores structurally high), so not hidden — but design §A's decision commits to three archetypes and the digest's calibration claim leans on "incomplete-recall recovery." With only `missing_join_path` + `dangling_no_fact` authored, the recovery signal is thinner than designed. — **Required change:** no code change required if the operator accepts this; flag explicitly in the ship digest (§8) that recovery was validated on `missing_join_path` only, and track `missing_dimension` as a follow-up (scorer change) so the calibration is not over-claimed.

## Minor

- [x] **`scripts/run_smoke_eval.py` `_is_unavailable_clickhouse_skip` — brittle substring match.** Gates the closeout on `"datasource unavailable:" in skipped and "clickhouse" in skipped.casefold()`. Works today only because the instance is named `clickhouse_ecommerce`; a rename silently disables the gate. — **Fixed:** the helper parses the unavailable datasource from the skip string and compares it exactly to `ClickHouseConnector.name`; the unit covers a false-positive datasource containing `clickhouse`.

- [x] **`scripts/run_smoke_eval.py` `_run_case` — OLAP intent detection runs up to 3×.** Once inline for `pre_coverage`, once inside `build_context_node` (state.olap_intents is empty because `olap_intent_detect_node` runs *after*), and again in `olap_intent_detect_node`. Functionally correct (all three see the same question) but redundant. — **Fixed:** `_run_case` computes intents once for coverage/context and `_set_olap_hint` reuses them when building the SQL-stage hint.

- [x] **`scripts/run_smoke_eval.py` `_validate_parity_anchor_results` — a failed run (coverage `None`) is dropped from the band comparison.** `unique_bands` skips `None`, so if one datasource of a parity anchor errors before coverage is computed, a real divergence can hide behind the error. — **Fixed:** parity anchors now fail the group if any listed datasource has no coverage band; unit covered.

---

## Disposition

Blocking implementation findings are fixed and validated. Re-run `diff-review` for the next loop. No design-level re-sign-off was triggered (§4); the blocker was an implementation deviation from §B, not a design change.

---

## Round 2 re-review (GLM) — CLEAN

Re-reviewed the updated uncommitted diff against `design.md` + §7. Blocking findings verified **in code**, not just by the checklist:

- **Blocking #1 (vacuous regression) — resolved.** `_run_retrieval_calibration` captures `reference_threshold = get_settings().retrieval_coverage_threshold` once, before the sweep; `_run_case` records `retrieval_reference_coverage` at that fixed threshold under a nested `_temporary_retrieval_threshold` (entry/exit values restore correctly under the outer swept-threshold context); `_retrieval_calibration_row` selects the holdout via `_reference_coverage(...)["band"] == "high"`. The holdout is now a fixed set, and a boundary case (high at reference, low at swept threshold → expanded → Δcontext) is countable as a regression. Implementer's live evidence (high_conf_regressions=1/41 at threshold 0.8) confirms the check is no longer vacuous.
- **Blocking #2 (impossible test) — resolved.** `test_retrieval_calibration_row_…` now uses `boundary_regression` (reference=high, pre=low at swept 0.8, expanded, Δ=5) alongside a clean `high_conf` — a realistic boundary regression; assertion holds for the right reasons.
- **Minor (CH substring) — resolved.** `_is_unavailable_clickhouse_skip` parses the datasource via `UNAVAILABLE_DATASOURCE_RE` and compares `== ClickHouseConnector.name` (`"clickhouse_ecommerce"`, verified `clickhouse.py:16`); test covers a `not_clickhouse_ecommerce` false positive.
- **Minor (OLAP dedup) — resolved, no behavior lost.** `_set_olap_hint` is equivalent to `olap_intent_detect_node` (`nodes.py:310-321`): same metrics load, same `build_olap_hint`, same `completed_steps`; it only skips recomputing when `state.olap_intents` is already populated.
- **Minor (parity dropped `None`) — resolved.** `_validate_parity_anchor_results` now fails the group when any listed datasource has no band; covered by `test_validate_parity_anchor_results_fails_missing_band`.
- **Important (fixtures read live graph) — resolved by documentation** in `tasks.md` / `EVALUATION_DESIGN.md`. Acceptable: drift surfaces as a loud `pre_band` assertion failure, not a silent pass.

Carried forward (non-blocking, operator/scope decisions — **not** self-fixed because they are not safe local code edits):
- **Important — unrelated doc edits still bundled** (`AGENT_WORKFLOW.md`, `METADATA_SEMANTIC_LAYER.md`, EVAL market-comparison). Split at `ship` time so the closeout PR diff maps 1:1 to `tasks.md`.
- **Important — `missing_dimension` archetype still unvalidated** (scorer limitation, disclosed). Surface in the ship digest (§8) that recovery was validated on `missing_join_path` only.

One new low-exposure note (not blocking):
- **Minor — `_validate_parity_anchor_results` treats all-`None` bands as failure.** In flags-off mode `build_context_node` never sets `state.retrieval_coverage`, so a parity anchor that runs on **two available** datasources with ClickHouse up would have `band=None` for both and be marked failed (false failure, safe direction). Undocumented combo (closeout runs flags-on, where coverage is computed and parity works); the unit only covers the mixed high/`None` case. Suggested, if desired: distinguish "no coverage computed for the whole group" (flags-off → skip) from "partial coverage" (real signal → fail). Left to the implementer; not a blocker.

**Verdict: CLEAN.** Hand to `validate` (run tests/UI) then `ship` (PR with the §8 walkthrough).
