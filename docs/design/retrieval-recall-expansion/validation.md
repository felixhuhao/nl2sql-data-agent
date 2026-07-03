# Validation - Retrieval Recall Expansion

## Summary

Overall: **PASS** for the local validation surface of commit `44fa6d6`.

- Backend tests: **PASS** - `504 passed`
- Mock smoke eval: **PASS** - DuckDB tier `51/51` passed; ClickHouse `25` cases skipped because local ClickHouse was unavailable
- Frontend build/type-check: **PASS**
- Backend MCP smoke: **PASS**
- Playwright MCP UI flow: **PASS** - real browser chat flow completed; screenshots, accessibility snapshots, and API network log captured
- HTTP/SSE telemetry: **PASS** - `retrieval_coverage` appeared on `build_context` and `done`
- Blockers: **none**

Limitations:

- DeepSeek eval and vector comparison were not run because they require external API/vector service setup.
- Backend `ruff` is not installed in the project venv and no documented backend lint command exists; Python compile checks were run instead.
- Local ClickHouse was not running, so ClickHouse smoke cases were skipped by the runner.

## Tests / Lints

### Backend Unit/Regression

Command:

```bash
PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests -q
```

Result:

```text
504 passed, 1 warning in 6.06s
```

The warning is from `fastapi.testclient` / Starlette deprecation and is unrelated to this change.

### Smoke Eval

Command:

```bash
PYTHONPATH=. backend/.venv/bin/python scripts/run_smoke_eval.py \
  --provider mock \
  --report-path evals/reports/retrieval_recall_expansion_validation.md
```

Result:

```text
51/51 smoke cases passed.
DuckDB (本地) - 51 cases: 51/51 passed.
skipped 25 cases for provider=mock.
focused context: avg=2901 chars, full=8401 chars, avg_reduction=65.5%, fallback=5/51, repair_cases=4/51, avg_elapsed=41ms
report: evals/reports/retrieval_recall_expansion_validation.md
```

Notes:

- ClickHouse registration failed because `localhost:8123` was not running, so ClickHouse cases were skipped by the runner.
- Semantic verifier unavailable messages are expected under mock/no configured verifier and fail open.

### Frontend Build / Type Check

Command:

```bash
cd frontend
npm run build
```

Result:

```text
vue-tsc -b && vite build
✓ 603 modules transformed.
✓ built in 2.38s
```

Vite emitted the existing large-chunk warning.

### Backend MCP Smoke

Command:

```bash
PYTHONPATH=. backend/.venv/bin/python scripts/run_mcp_smoke.py
```

Result:

```text
MCP smoke passed: db_tools + olap_tools + guarded DELETE rejection
```

ClickHouse connection warnings appeared because local ClickHouse was unavailable; the smoke itself passed.

### Compile Check

Command:

```bash
PYTHONPATH=. backend/.venv/bin/python -m py_compile \
  backend/app/config.py \
  backend/app/agent/state.py \
  backend/app/agent/nodes.py \
  backend/app/api/chat.py \
  backend/app/metadata/service.py \
  backend/app/metadata/retrieval_coverage.py \
  backend/tests/conftest.py \
  backend/tests/test_metadata_retrieval.py \
  backend/tests/test_agent_workflow.py \
  scripts/run_smoke_eval.py
```

Result: **PASS**.

Backend lint note:

```text
backend/.venv/bin/python: No module named ruff
```

No documented backend lint command is present in `README.md`, `docs/DEVELOPMENT.md`, or `backend/pyproject.toml`.

## Playwright MCP Test Matrix

Started backend with retrieval recovery enabled:

```bash
PYTHONPATH=. \
LLM_PROVIDER=mock \
VECTOR_ENABLED=disabled \
AUTH_ENABLED=false \
RETRIEVAL_EXPANSION_ENABLED=true \
RETRIEVAL_FALLBACK_MODE=on \
backend/.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8011
```

Started frontend:

```bash
VITE_PROXY_TARGET=http://127.0.0.1:8011 npm run dev -- --port 5174 --strictPort
```

Playwright MCP launcher used:

```bash
npx -y @playwright/mcp@latest \
  --executable-path /home/hao/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome \
  --headless
```

| case | input | expected | actual | result |
|------|-------|----------|--------|--------|
| Chat happy path with retrieval recovery flags enabled | UI question: `按渠道统计最近30天销售额`; datasource: DuckDB local | App loads, `/api/chat/query` returns 200, result shows SQL, 5 rows, `sales_amount`, and no app/API failures | MCP waited for `查询返回 5 行`; result snapshot contains SQL, row count `5`, table header `sales_amount`; API network log shows `/api/auth/me`, `/api/health`, `/api/datasources`, `/api/chat/query` all `200 OK` | PASS |

Evidence:

- Initial screenshot: [evidence/mcp-chat-initial.png](evidence/mcp-chat-initial.png)
- Filled-question screenshot: [evidence/mcp-chat-filled.png](evidence/mcp-chat-filled.png)
- Result screenshot: [evidence/mcp-chat-result.png](evidence/mcp-chat-result.png)
- Result accessibility snapshot: [evidence/mcp-chat-result.md](evidence/mcp-chat-result.md)
- API network log: [evidence/mcp-network.md](evidence/mcp-network.md)
- Raw MCP run log: [evidence/mcp-run.json](evidence/mcp-run.json)

Console notes:

```text
[ERROR] Failed to load resource: the server responded with a status of 404 (Not Found) @ http://127.0.0.1:5174/favicon.ico:0
```

Only the missing favicon 404 was reported; no API request failed.

## HTTP/SSE Telemetry Evidence

Chat stream request:

```json
{"question":"按渠道统计最近30天销售额","datasource":"duckdb_ecommerce"}
```

Observed SSE evidence:

```json
[
  {"event":"step","step":"retrieve_context","coverage":null,"row_count":null},
  {
    "event":"step",
    "step":"build_context",
    "coverage":{
      "match_strength":1.0,
      "structural_score":1.0,
      "score":1.0,
      "band":"high",
      "expanded":false,
      "fallback_used":false,
      "signals":{
        "stage":"merged",
        "tables":["dim_channels","dim_date","dim_regions","fact_order_items","fact_orders"],
        "metric_intent":true,
        "fact_role_tables":["fact_order_items","fact_orders"],
        "relationship_count":7,
        "join_connected":true,
        "has_fact_role":true
      }
    },
    "row_count":null
  },
  {
    "event":"done",
    "step":null,
    "coverage":{
      "match_strength":1.0,
      "structural_score":1.0,
      "score":1.0,
      "band":"high",
      "expanded":false,
      "fallback_used":false
    },
    "row_count":5
  }
]
```

This exercises the changed HTTP/SSE telemetry path with flags enabled. The query completed and returned `row_count=5`.

## Manual UI Check

- Visually inspect the result screen screenshot for layout regressions around the workflow/status panels and result table.
- Try one deliberately unsupported or unsafe question manually to confirm the UI still presents guard failures clearly.

## Blockers

None.
