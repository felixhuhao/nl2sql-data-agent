# Validation — Coverage Match-Strength Signal + Vector-ON Recalibration

## Validated at

- Commit under test: `09f9c99`
- Date: 2026-07-04

## Summary

Overall: **PASS**.

All objective tests, lints, ClickHouse-required smoke, vector-active calibration, and default-on smoke passed. The vector-on gate clears at threshold `0.7`, and retrieval recovery is now enabled by default (`RETRIEVAL_EXPANSION_ENABLED=true`, `RETRIEVAL_FALLBACK_MODE=on`).

## Coverage

| Surface | Result | Evidence |
|---|---:|---|
| Rule-only strength emission | covered | backend unit tests; full backend suite passed |
| Hybrid/vector strength emission | covered | backend unit tests; vector-active calibration loaded local embedding weights |
| Coverage scorer fallback/clamp behavior | covered | backend unit tests; full backend suite passed |
| DuckDB smoke surface | covered | `55/55` DuckDB cases passed |
| ClickHouse smoke surface | covered | `26/26` ClickHouse cases passed with `--require-clickhouse` |
| Vector-active recalibration gate | covered | recovery `1/1`, fallback paths `1/1`, high-conf regressions `0/66` at threshold `0.7` |
| Default-on re-enable | covered | code defaults, `.env.example`, local `.env`, config test, and default-on smoke all use recovery on |
| Browser UI / Playwright | not applicable | no UI or HTTP behavior changed |

## Tests / Lints

| Suite | Command | Result | Evidence |
|---|---|---:|---|
| backend full suite | `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests -q` | PASS | `529 passed, 1 warning in 30.78s` |
| lint | `uvx ruff check backend/app scripts backend/tests` | PASS | `All checks passed!` |
| type check | `npx --yes pyright -p pyrightconfig.json --pythonpath backend/.venv/bin/python` | PASS | `0 errors, 0 warnings, 0 informations` |
| ClickHouse-required smoke | `PYTHONPATH=. backend/.venv/bin/python scripts/run_smoke_eval.py --provider mock --require-clickhouse --report-path evals/reports/coverage_strength_recalibration_validation_smoke.md` | PASS | `81/81 smoke cases passed`; DuckDB `55/55`, ClickHouse `26/26`; focused context reduction `65.6%`; fallback `8/81` |
| vector-active calibration | `PYTHONPATH=. backend/.venv/bin/python scripts/run_smoke_eval.py --provider mock --retrieval-calibration --require-clickhouse --report-path evals/reports/coverage_strength_recalibration_validation_calibration.md` | PASS | no skipped cases; report table below |
| default-on smoke | `PYTHONPATH=. backend/.venv/bin/python scripts/run_smoke_eval.py --provider mock --require-clickhouse --report-path evals/reports/coverage_strength_recalibration_default_on_smoke.md` | PASS | `83/83 smoke cases passed`; DuckDB `57/57`, ClickHouse `26/26`; focused context reduction `59.2%`; fallback `15/83`; no skipped recovery cases |

Calibration summary:

| Threshold | Passed | Recovery | Fallback Paths | High-conf regressions | Fallback cases | Avg context delta | Regression cases |
|---:|---:|---:|---:|---:|---:|---:|---|
| 0.3 | 83/83 | 0/1 | 0/1 | 0/66 | 7 | +16 | - |
| 0.4 | 83/83 | 0/1 | 1/1 | 0/66 | 8 | +109 | - |
| 0.5 | 83/83 | 0/1 | 1/1 | 0/66 | 10 | +183 | - |
| 0.6 | 83/83 | 1/1 | 1/1 | 0/66 | 11 | +297 | - |
| 0.7 | 83/83 | 1/1 | 1/1 | 0/66 | 13 | +479 | - |
| 0.8 | 83/83 | 1/1 | 1/1 | 2/66 | 16 | +739 | `recent_30d_channel_user_count`, `phase2_product_name_alias` |

Non-VQ diagnostic cases in the ClickHouse-required smoke:

| Case | Result | Coverage |
|---|---:|---|
| `coverage_recalibration_channel_sales_non_vq` | PASS | high / `0.94`, focused context retained |
| `coverage_recalibration_category_sales_non_vq` | PASS | high / `0.81`, focused context retained |
| `coverage_recalibration_gender_sales_non_vq` | PASS | high / `0.81`, focused context retained |

Environment notes:

- Docker reported `backend`, `frontend`, `clickhouse`, and `qdrant` containers up; ClickHouse `/ping` and Qdrant `/readyz` responded from host.
- A direct host curl to `127.0.0.1:8000/api/health` returned connection refused, while Docker reported the backend container healthy and backend logs showed repeated internal `/api/health` `200 OK`. This validation did not depend on the HTTP server because the changed surface is the retrieval/scoring path exercised by tests and smoke runner.
- Semantic verifier was not configured under `provider=mock`, so smoke/calibration emitted expected fail-open warnings.

## UI Test Matrix

No browser UI surface changed. Playwright MCP is not applicable for this validation.

## Manual UI Check

None for this change.

## Blockers

None.
