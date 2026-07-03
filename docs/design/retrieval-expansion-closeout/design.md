# Design — Retrieval Expansion Closeout (eval cases · calibration · datasource partitioning)

## Digest
- **Bottom line:** Approve if you agree that flipping the expansion flags on should be gated by **recovery-first / zero-regression** calibration against purpose-built incomplete-recall cases, and that a **small additive CH set + parity anchors + a hard CH-up closeout gate** proves datasource-agnosticism without running every case on ClickHouse.
- **Change:** Close out `retrieval-recall-expansion` by making its deferred eval work real — build the incomplete-recall cases that trigger the new paths, calibrate threshold/weights defensibly, and restructure the eval harness so ClickHouse validation is cheap but honest.  ·  **Lane:** full (lightweight)  ·  **Status:** Validated
- **Decisions:**
  - **(A) Incomplete-recall case contract** — each case must score `band=low` *before* expansion and isolate one archetype (missing-dimension / missing-join-path / dangling-no-fact) — why: calibration and e2e validation both stand on these; an invalid case that scores `high` proves nothing — rejected: ad-hoc cases (untriggerable, non-distinct).
  - **(B) Calibration = recovery-first under a hard zero-regression constraint** — maximize incomplete-recall recovery subject to *no* high-confidence context-size/band regression — why: matches the feature's purpose and is the simplest target to defend — rejected: balanced score (needs an arbitrary weighting), precision-first (under-recovers).
  - **(B-anti-overfit) Single-threshold tuning against a fixed weight prior + existing corpus as large-N regression holdout + dark→canary telemetry** — why: only ~a dozen recovery cases, so fixing weights before threshold tuning and binding zero-regression to the 51+ existing cases removes the overfit surface — rejected: holdout split (meaningless at N≈12), both (false rigor).
  - **(C) Datasource-partitioned harness: additive targeted CH set + parity anchors + runner-enforced CH-up closeout gate** — why: keeps live CH smoke cheap while still proving datasource-agnosticism and turning the silent-skip gotcha into a hard failure; **legacy scalar `datasource:` cases preserved (normalized to singleton), zero migration** — rejected: clean partition (loses cross-dialect parity), status-quo (skips masked as green).
- **Risks / watch:** an incomplete-recall case that expansion *should* recover vs one that must fall back must be labelled and asserted distinctly; parity-anchor divergence is a real bug signal, not flakiness; the calibrated numbers remain provisional until canary telemetry confirms.
- **Calibrated defaults:** threshold `0.7`; weights `w_strength=0.5`, `w_struct=0.5`; `MAX_TABLES=3`; full-schema budget `120000`. Evidence: 0/61 high-confidence regressions; recovery evidence covers `missing_join_path`; fallback evidence covers `dangling_no_fact`; `missing_dimension` remains a scorer follow-up.
- **Drill down:** full design below · pros/cons in `design.html`.

---

## 1. Problem / context

`retrieval-recall-expansion` initially shipped **dark and uncalibrated**: flags defaulted off; `RETRIEVAL_COVERAGE_THRESHOLD=0.7`/weights/`MAX_TABLES` were guessed defaults; and validation ([validation.md](../retrieval-recall-expansion/validation.md)) exercised only a `band=high` question — so **expansion and fallback had zero end-to-end evidence** and there was nothing to calibrate against. Separately, ClickHouse validation was skipped (CH down), which the project's ClickHouse-skip gotcha warns can mask failures behind green DuckDB smoke.

This design closes those gaps. It carries **three design-worthy decisions (A/B/C)**; the remaining closeout items are self-explanatory execution and live in `tasks.md`, not here.

## 2. Goals

**In scope (design decisions):**
- (A) A validity contract for incomplete-recall eval cases.
- (B) A calibration methodology (objective + anti-overfitting) that yields defensible threshold/weights and flips the flags on.
- (C) A datasource-partitioned eval harness: case tagging, per-datasource runner selection, a small targeted CH set, parity anchors, and a runner-enforced CH-up closeout gate.

**Out of scope — self-explanatory execution (tracked in `tasks.md`, no design decision):**
- e2e expansion/fallback validation cases (mirror existing Playwright/SSE evidence format; inputs come from pillar A).
- flags-off vs flags-on focused-context-size delta in the eval report.
- status hygiene (roadmap → In-progress/Done) and `SPEC.md` update on Done.

**Non-goals:** changing the coverage/expansion algorithm itself (approved and implemented); any LLM-based retrieval; new datasource connectors.

## 3. Key decisions

### (A) Incomplete-recall case contract — *light*

Each incomplete-recall case declares an **archetype** and must satisfy a **validity assertion**: on the pre-expansion (merged) stage it scores `band=low`. Archetypes, each isolating one failure:

| archetype | recall shape | expected path |
|---|---|---|
| `missing_dimension` | fact recalled; a needed dim absent from recall | expansion **recovers** (`expanded=true`, post-band `high`) |
| `missing_join_path` | two tables recalled with no connecting `MetaRelationship` edge | dangling → recovers only if a 1-hop edge exists, else falls back |
| `dangling_no_fact` | non-fact tables only, no metric intent | stays `low` → **fallback** (`fallback_used=true`) |

Commit: each case carries `expected.coverage` (pre-band, post-band, `expanded`, `fallback_used`) so the runner asserts the *path taken*, not just the answer. This is what makes A the shared foundation for B (calibration signal) and the e2e validation cases.

### (B) Calibration methodology — *core*

**Objective:** maximize recovery over the `missing_dimension`/`missing_join_path` cases, **subject to a hard constraint**: zero regression on high-confidence cases (band stays `high`, focused-context size unchanged).

**Anti-overfitting** (only ~a dozen recovery cases → 4 knobs would overfit):
1. **Fix the weights**, don't co-tune. Calibration fixed `w_strength=0.5` and `w_struct=0.5`; keep `MAX_TABLES` and full-schema budget at conservative constants.
2. **Tune a single free parameter** — the threshold.
3. **Bind zero-regression to the large corpus.** The 51 DuckDB + CH high-confidence smoke cases are the "must stay `high`" holdout (large N, defensible). Place the threshold to satisfy that hard, then recover as much as it allows.
4. **Dark → canary → tune.** The original rollout path was dark first, then canary. After calibration, the flags are enabled by default; continue using emitted `band`/`expanded`/`fallback_used` telemetry to tune only if high-confidence traffic remains undisturbed.

**Reporting contract:** calibration output states, per candidate threshold: recovery rate on incomplete-recall cases · regression count on high-confidence cases (must be 0) · context-size delta flags-off vs on. Flags flip on only when recovery meets target **and** regression is 0. Chosen numbers are written back into `retrieval-recall-expansion/design.md` open questions.

### (C) Datasource-partitioned harness — *structural*

**Compatibility contract (resolves the legacy-shape conflict).** The existing corpus already partitions by a **scalar** `datasource:` naming a **datasource instance** (`duckdb_ecommerce`, `clickhouse_ecommerce`); the runner reads `case.get("datasource") or DEFAULT_DATASOURCE` ([run_smoke_eval.py:331](../../../scripts/run_smoke_eval.py#L331)) and skips cases whose instance is unavailable. This design **extends, not replaces** that shape:
- Legacy scalar `datasource: X` stays valid and normalizes to `datasources: [X]` (singleton). **Zero migration** — all existing DuckDB and the 25 ClickHouse cases are preserved as-is.
- New plural `datasources: [X, Y]` expresses multi-datasource (parity) cases.
- **Both keys on one case → validation error** (fail fast; no silent precedence).
- **Neither → `[DEFAULT_DATASOURCE]`** (`duckdb_ecommerce`), matching today's default.
- Values are **instance names**, not dialect short names.
- Runner change: `_case_datasource` (returns one) → `_case_datasources` (returns list); iterate cases × listed-available datasources.

**Set composition:**
- **DuckDB set:** the behavioral/regression bulk (default, fast).
- **CH set (additive, targeted, small):** the existing ClickHouse cases plus a few new ones covering what only CH can prove — CH OLAP metadata (engine/partition/sorting/low_cardinality) through coverage, and the three new coverage paths on CH-synced metadata. ~4-5 new cases, not a mirror.
- **Parity anchors:** 2-3 cases tagged `datasources: [duckdb_ecommerce, clickhouse_ecommerce]`; the runner asserts the **coverage band matches across datasources** — a divergence is a test failure, the cross-dialect signal a clean partition would lose.
- **CH-up closeout gate:** a runner mode (e.g. `--require-clickhouse`) that **fails** if any CH-listed case is SKIPPED because CH is down. This converts the ClickHouse-skip gotcha (green DuckDB smoke masking CH failures when CH cases are skipped) from a silent green into a hard failure; the closeout run must pass this mode before the change reaches Done.

**Failure modes (committed):** CH down on a normal run → CH cases SKIP, marked in report, not passing; CH down on a closeout run → hard fail; both `datasource` and `datasources` on one case → validation error; parity-anchor band divergence → test failure; a `missing_dimension` case that does *not* recover post-expansion → test failure (catches an expansion regression).

## 4. Alternatives & rationale (full lane)

### (B) Calibration objective
| Option | Pros | Cons |
|---|---|---|
| **Recovery-first / zero-regression (chosen)** | Matches feature intent; single defensible constraint; honest "zero regression on 51+ cases". | Recovery is capped by the constraint (may under-recover initially — mitigated by canary tuning). |
| Balanced score | One number to optimize. | Needs an arbitrary recovery↔size weighting to justify. |
| Precision-first | Fewest spurious fallbacks; cheap runtime. | Under-recovers — undercuts the whole point of the feature. |

### (B) Anti-overfitting
| Option | Pros | Cons |
|---|---|---|
| **Conservative threshold + telemetry, single-knob (chosen)** | Removes overfit surface; leverages existing corpus + already-emitted telemetry; fits the staged flag rollout. | No precise offline threshold before canary — acceptable given N. |
| Holdout split | Offline rigor. | Meaningless at N≈12; noise. |
| Both | — | Inherits holdout weakness for extra work. |

### (C) CH set structure
| Option | Pros | Cons |
|---|---|---|
| **Additive targeted + parity anchors (chosen)** | Cheap CH smoke; proves agnosticism on the paths that matter; keeps cross-dialect parity signal; closeout gate kills the skip gotcha. | Slightly more cases than a pure split. |
| Clean partition | Cheapest live smoke. | Loses cross-dialect parity detection. |

## 5. Altitude — deferred to implementation

- Exact YAML key spelling and runner flag names.
- The specific weight-prior ratio and threshold *value* (pinned by the calibration run, not guessed here).
- Report/table formatting; how SKIP is rendered.
- Parity-anchor exact count (2 vs 3) — pick during implementation.

## 6. Open questions

- `missing_dimension` recovery remains a scorer follow-up because fact-only metric intent currently scores structurally high before expansion.
- Whether the CH-up closeout gate later becomes a CI profile instead of a runner flag.

## 7. Status

`Done` — maker: Claude. Reviewer: Codex, REVIEWER-CLEAR round 2 (zero BLOCKING). Operator approved; implementation completed, validation passed, and calibrated defaults are enabled.
Approval: felixhuhao — `approved`.
