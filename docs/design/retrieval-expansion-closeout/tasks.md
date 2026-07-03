# Tasks — Retrieval Expansion Closeout

Implementer checklist (design altitude). Design: [design.md](design.md). Sibling: [../retrieval-recall-expansion/](../retrieval-recall-expansion/).

## 1. Incomplete-recall eval cases (pillar A)
- [x] Add `expected.coverage` fields to the smoke case schema (pre-band, post-band, `expanded`, `fallback_used`).
- [ ] Author `missing_dimension` cases (fact recalled, needed dim absent) → assert expansion recovers. **Blocked by current scorer behavior: fact-only metric intent scores structurally high, so this archetype does not produce a valid pre-low case without changing the approved algorithm.**
- [x] Author `missing_join_path` cases (two tables, no connecting edge) → assert dangling / fallback.
- [x] Author `dangling_no_fact` cases (non-fact only, no metric intent) → assert fallback.
- [x] Runner asserts the **path taken** (pre/post band, expanded, fallback_used), not just the result.
- [x] Validity check: each case scores `band=low` on the merged (pre-expansion) stage.
- [x] Document fixture boundary: retrieval fixtures stub recalled assets only; coverage/expansion still depend on the live seeded `MetaRelationship` graph, and coverage assertions fail loudly if that graph drifts.

## 2. Calibration (pillar B)
- [ ] Fix the weight prior (`w_struct > w_strength`); hold `MAX_TABLES` / full-schema budget at conservative constants.
- [x] Sweep the single threshold; for each: record recovery rate (incomplete-recall) · regression count (high-confidence, must be 0) · context-size delta.
- [ ] Bind zero-regression to the full existing corpus (51 DuckDB + CH high-confidence cases).
- [ ] Pick the threshold meeting recovery target with 0 regression; write chosen values back into `../retrieval-recall-expansion/design.md` open questions.
- [ ] Enable flags (`RETRIEVAL_EXPANSION_ENABLED`, `RETRIEVAL_FALLBACK_MODE`) for canary; confirm via emitted telemetry before broad on.

## 3. Datasource-partitioned harness (pillar C)
- [x] Add plural `datasources: [...]` to the case schema; **legacy scalar `datasource: X` normalizes to `[X]`** (zero migration for existing DuckDB + 25 CH cases).
- [x] Validation: both `datasource` and `datasources` on one case → error; neither → `[DEFAULT_DATASOURCE]` (`duckdb_ecommerce`); values are instance names.
- [x] Runner: `_case_datasource` → `_case_datasources` (list); iterate cases × listed-available datasources; surface SKIP explicitly (never counted as pass).
- [ ] Build the small targeted CH set (CH OLAP metadata through coverage + the three new paths on CH-synced metadata), additive to existing CH cases. **Pending live/synced ClickHouse.**
- [x] Add 2-3 parity-anchor cases `datasources: [duckdb_ecommerce, clickhouse_ecommerce]`; assert band matches across datasources.
- [x] Add the CH-up closeout gate (`--require-clickhouse`-style): fail if a CH-listed case is SKIPPED due to CH down.
- [x] Update `docs/EVALUATION_DESIGN.md` to document the scalar→list compatibility, the CH set, parity anchors, and the closeout gate.

## 4. Self-explanatory closeout (no design decision)
- [ ] e2e validation cases that fire `expanded=true` and `fallback_used=true` end-to-end (mirror the existing Playwright/SSE evidence format in `../retrieval-recall-expansion/validation.md`).
- [x] Add flags-off vs flags-on focused-context-size delta row to the eval report.
- [x] Run the CH-up closeout smoke with live ClickHouse; capture evidence.
- [ ] Status hygiene: move `retrieval-recall-expansion` to Done once flags on + closeout green; update `SPEC.md`.

## 5. Tests
- [x] Runner unit: datasource selection, SKIP surfacing, closeout-gate hard-fail on CH-down.
- [x] Runner unit: parity-anchor divergence fails.
- [x] Case-contract unit: an invalid (scores `high`) incomplete-recall case is rejected/flagged.
