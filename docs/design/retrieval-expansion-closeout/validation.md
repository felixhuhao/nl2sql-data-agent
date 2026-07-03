# Validation — Retrieval Expansion Closeout

## Validated at

- Commit: `44fa6d6`
- Workspace: includes uncommitted `retrieval-expansion-closeout` implementation diff.
- Date: 2026-07-03

## Summary

Overall: **PASS**.

Runner tests, backend blast-radius tests, lint, type-check, default smoke, calibration, and CH-up closeout smoke all pass. ClickHouse was started locally through Docker Compose, seeded from `data/clickhouse_csv`, and metadata was synced before the CH-up run.

## Tests / Lints

| suite | command | result | evidence |
|---|---|---:|---|
| focused runner unit | `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_smoke_eval_runner.py -q` | PASS | `21 passed in 0.32s` |
| backend blast radius | `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests -q` | PASS | `514 passed, 1 warning in 6.54s` |
| syntax | `PYTHONPATH=. backend/.venv/bin/python -m py_compile scripts/run_smoke_eval.py scripts/run_semantic_guard_eval.py` | PASS | no output |
| lint | `uvx ruff check backend/app scripts backend/tests` | PASS | `All checks passed!` |
| type check | `npx --yes pyright -p pyrightconfig.json --pythonpath backend/.venv/bin/python` | PASS | `0 errors, 0 warnings, 0 informations` |
| ClickHouse seed | `docker compose up -d clickhouse`; `scripts/seed_clickhouse.py`; `scripts/sync_metadata.py` | PASS | registered `clickhouse_ecommerce`; seeded 7 tables; synced `{tables: 7, columns: 44, relationships: 7}` |
| default mock smoke | `PYTHONPATH=. backend/.venv/bin/python scripts/run_smoke_eval.py --provider mock --report-path evals/reports/retrieval_expansion_closeout_validation_smoke.md` | PASS | `78/78 smoke cases passed`; DuckDB `52/52`; ClickHouse `26/26` |
| calibration sweep | `PYTHONPATH=. backend/.venv/bin/python scripts/run_smoke_eval.py --provider mock --retrieval-calibration --retrieval-thresholds 0.5,0.7,0.8 --report-path evals/reports/retrieval_expansion_closeout_validation_calibration.md` | PASS | report: `evals/reports/retrieval_expansion_closeout_validation_calibration.md` |
| CH-up closeout gate | `PYTHONPATH=. backend/.venv/bin/python scripts/run_smoke_eval.py --provider mock --require-clickhouse --report-path evals/reports/retrieval_expansion_closeout_validation_ch_up.md` | PASS | `78/78 smoke cases passed`; no CH skip |

Calibration summary:

| threshold | recovery | fallback paths | high-conf regressions | note |
|---:|---:|---:|---:|---|
| 0.5 | 0/1 | 1/1 | 0/61 | under-recovers |
| 0.7 | 1/1 | 1/1 | 0/61 | current best candidate in this local run |
| 0.8 | 1/1 | 1/1 | 2/61 | proves fixed holdout is non-vacuous |

## UI / E2E Matrix

No browser UI surface changed. This change is in the smoke-eval CLI, retrieval coverage calibration, datasource partitioning, and reports, so Playwright MCP is **not applicable**.

| case | input | expected | actual | result |
|---|---|---|---|---|
| default mock smoke | `--provider mock` with DuckDB + ClickHouse available | DuckDB and ClickHouse cases pass; closeout recovery cases requiring calibration mode skip | `78/78` passed, 2 recovery-gated cases skipped | PASS |
| calibration happy path | `--retrieval-calibration --retrieval-thresholds 0.5,0.7,0.8` | recovery/fallback rows emitted; fixed reference holdout shown; high-confidence regression count can be non-zero at aggressive threshold | reference threshold `0.7`; threshold `0.8` reports `2/61` high-conf regressions | PASS |
| CH-up gate | `--require-clickhouse` with seeded local ClickHouse | zero CH unavailable skips; DuckDB + CH corpus passes | `78/78` passed; ClickHouse `26/26` | PASS |
| parity all-missing coverage | runner unit fixture with both datasource bands `None` | flags-off/all-missing coverage skips parity comparison | covered by `test_validate_parity_anchor_results_skips_when_all_bands_missing` | PASS |
| parity partial missing coverage | runner unit fixture with one band and one `None` | group fails | covered by `test_validate_parity_anchor_results_fails_missing_band` | PASS |

## Manual UI Check

None. There is no visual or interactive UI delta.

## Known Follow-up

- `missing_dimension` remains unvalidated. Recovery evidence currently rests on `missing_join_path`; fallback evidence rests on `dangling_no_fact`. Ship digest must state this honestly and track the scorer change needed to make fact-only missing-dimension cases score pre-expansion `low`.

## Blockers

None.
