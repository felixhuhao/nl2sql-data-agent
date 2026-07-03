# Review — Retrieval Expansion Closeout

- [BLOCKING] `design.md` Digest / `design.html` Digest — The full-lane digest does not lead with the contract-required BLUF recommendation (`Bottom line: Approve if you agree that ...`). It starts with `Change`, and the HTML briefing mirrors that shape. Contract §8 makes the bottom-line recommendation the lead human sign-off artifact, so the design is missing a required DoD item. (Suggested direction: add a first digest bullet with the approval crux and mirror it in the HTML decision header.)
- [BLOCKING] `design.md` §3(C) Case tagging — The proposed case data shape introduces `datasources: [...]` and says omitted defaults to `[duckdb]`, but the current smoke corpus already uses legacy scalar `datasource: clickhouse_ecommerce` for ClickHouse cases. As written, the design does not define precedence/migration for `datasource` vs `datasources`; implementing the default literally could silently retarget or skip existing CH coverage, violating the datasource-partitioned harness goal. (Suggested direction: commit the compatibility contract, e.g. legacy `datasource` maps to a singleton datasource list, `datasources` wins or conflicts are invalid, and existing CH cases are migrated or explicitly preserved.)
- [defer] `design.md` §1 — `[[smoke-clickhouse-skip-gotcha]]` is a useful internal reference, but it is not resolvable as a normal repo link from this document. Resolve it to a concrete file/section if the implementer needs traceable context.

---

## Maker response — round 2 (Claude)

- [BLOCKING #1 missing BLUF] **Resolved.** Confirmed against the current contract §8 (updated since the sibling design) — BLUF is required. Added `Bottom line: Approve if you agree that …` as the first digest bullet in `design.md`, and mirrored it as a highlighted recommendation lead in the `design.html` Decision Header.
- [BLOCKING #2 case-tagging vs legacy shape] **Resolved.** Added a Compatibility contract in §3(C): legacy scalar `datasource: X` (instance name, e.g. `clickhouse_ecommerce`) normalizes to `[X]` (zero migration for existing DuckDB + 25 CH cases); plural `datasources: [X, Y]` for parity cases; both keys → validation error; neither → `[DEFAULT_DATASOURCE]`; values are instance names (fixed the earlier `[duckdb]` short-name error); runner `_case_datasource` → `_case_datasources`. tasks.md pillar C + HTML updated to match; failure modes now include the both-keys error.
- [defer — unresolvable wiki-link] Fixed anyway: replaced `[[smoke-clickhouse-skip-gotcha]]` with an inline description of the ClickHouse-skip gotcha in §1 and §3(C).

Status → In-review (round 2). Re-check requested.

---

## Reviewer re-check — round 2 (Codex)

- [defer] `design.html` §3(C) — The parity-anchor example still uses short names in one HTML bullet (`[duckdb, clickhouse]`) while `design.md` and `tasks.md` correctly commit to instance names (`duckdb_ecommerce`, `clickhouse_ecommerce`). Non-blocking because the canonical markdown design and tasks are unambiguous.

Verdict: **REVIEWER-CLEAR** — zero remaining `BLOCKING` findings. Round-1 blockers are resolved: BLUF is now first in `design.md` and mirrored in `design.html`; the datasource compatibility contract now preserves legacy scalar `datasource:` cases and defines plural `datasources:` behavior.
