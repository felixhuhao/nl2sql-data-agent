# Validation — i18n EN/ZH Separation

## Validated At

- Commit: `868fc82` plus current working-tree implementation diff.
- Date: 2026-07-03.
- Change: `i18n-en-zh-separation`.

## Summary

Overall: **PASS**.

- Backend tests/lints/type checks: pass.
- Frontend build/type check: pass.
- Smoke eval: pass for available DuckDB surface; ClickHouse was unavailable locally and skipped by the smoke runner.
- Playwright UI matrix: pass.
- Blockers: none.

Note: the requested `docs/design/i18n-en-zh-separation/code-review.md` was not present in this workspace; only `docs/design/retrieval-expansion-closeout/code-review.md` exists. Validation proceeded against the implemented i18n design and tasks.

## Tests / Lints

| command | result | notes |
|---|---:|---|
| `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests -q` | PASS | `521 passed`; one existing Starlette/httpx deprecation warning. |
| `uvx ruff check backend/app backend/tests scripts` | PASS | `All checks passed!` |
| `npx --yes pyright -p pyrightconfig.json --pythonpath backend/.venv/bin/python` | PASS | `0 errors, 0 warnings, 0 informations` |
| `npm --prefix frontend run build` | PASS | `vue-tsc -b && vite build`; existing Vite chunk-size warning only. |
| `PYTHONPATH=. backend/.venv/bin/python scripts/run_smoke_eval.py --report-path docs/design/i18n-en-zh-separation/evidence/smoke_eval.md` | PASS | `52/52` available DuckDB smoke cases passed; ClickHouse connection refused on `localhost:8123`, so ClickHouse cases were skipped. Evidence: [smoke_eval.md](evidence/smoke_eval.md). |

## UI Test Matrix

Local app setup:

- Backend: `AUTH_ENABLED=false LLM_PROVIDER=mock SEMANTIC_GUARD_MODE=off PYTHONPATH=. backend/.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`
- Frontend: `npm --prefix frontend run dev -- --host 127.0.0.1`
- Browser: Playwright MCP at `http://127.0.0.1:5174/`.

| case | input | expected | actual | result |
|---|---|---|---|---:|
| Default locale load | Open `/` with no locale interaction | UI chrome loads in `zh`; datasource/domain content may remain as data. | Header showed `掌柜问数`, `语言`, `数据源`, `问数`, `管理`; datasource displayed `DuckDB (本地)`. Screenshot: [i18n-default-zh.png](evidence/i18n-default-zh.png). Snapshot: [i18n_default_zh_snapshot.md](evidence/i18n_default_zh_snapshot.md). | PASS |
| Switch to English | Select `EN` in language switcher | UI chrome changes to English; Chinese sample/domain content remains untouched. | Header changed to `Data Agent`, `Language`, `Datasource`, `Ask`, `Admin`; sample questions stayed Chinese. Screenshot: [i18n-switched-en.png](evidence/i18n-switched-en.png). | PASS |
| English chat output | With locale `EN`, submit `查询最近30天每日销售额和订单数` | Backend presentation output is English; SQL/schema/data names remain unchanged. | Answer showed `Query returned 30 rows with columns: date_value, sales_amount, order_count.` Workflow/explanation labels were English; SQL and column names unchanged. Screenshot: [i18n-chat-en-result.png](evidence/i18n-chat-en-result.png). | PASS |
| Chinese backend output preservation | Switch back to `ZH`, start new chat, submit `查询最近30天每日销售额和订单数` | Backend summary uses preserved default Chinese shape `查询返回 N 行，字段：...。`. | Answer showed `查询返回 30 行，字段：date_value, sales_amount, order_count。`. Screenshot: [i18n-chat-zh-result.png](evidence/i18n-chat-zh-result.png). | PASS |
| Admin chrome smoke | Open Admin, then switch to `EN` | Admin tabs/buttons/table headers localize; domain metric labels remain metadata content. | Tabs/buttons/table headers changed to `Tables`, `Metrics`, `Add metric`, `Name`, `Label`, `Enabled`, `Edit`; metric labels such as `客单价` stayed as domain data. Screenshot: [i18n-admin-en.png](evidence/i18n-admin-en.png). | PASS |

Console notes: [i18n-console.log](evidence/i18n-console.log) contains Vite debug messages and one unrelated `/favicon.ico` 404. No app runtime errors were observed.

## Manual UI Check

- Eyeball both desktop and narrow viewport language switcher placement and text fit.
- Try exploratory mixed-language questions under both locales and confirm only presentation text changes.
- Review copy tone for the English catalog before public release.

## Blockers

None.
