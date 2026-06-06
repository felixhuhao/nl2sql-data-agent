# Development Guide

This guide keeps local setup and operational notes out of the main README. The README is optimized for project review and demo; this file is for day-to-day development.

## Local Python Environment

Create one virtual environment outside the repository:

```bash
python3 -m venv ~/.venvs/nl2sql-pro
source ~/.venvs/nl2sql-pro/bin/activate
python -m pip install --upgrade pip
python -m pip install -e "backend[test]"
```

For optional capabilities:

```bash
python -m pip install -e "backend[mcp]"
python -m pip install -e "backend[vector]"
```

Vector dependencies can be heavy. Install CPU PyTorch first when using sentence-transformers locally:

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
```

## Environment Variables

Copy the example file when needed:

```bash
cp backend/.env.example backend/.env
```

Common local settings:

```env
LLM_PROVIDER=mock
DUCKDB_PATH=/home/hao/.local/share/nl2sql_pro/ecommerce.duckdb
SQLITE_PATH=/home/hao/.local/share/nl2sql_pro/metadata.sqlite
DATASET_CURRENT_DATE=2025-12-31
```

DeepSeek:

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=...
```

ClickHouse:

```env
CLICKHOUSE_ENABLED=true
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=clickhouse
CLICKHOUSE_DATABASE=ecommerce
CLICKHOUSE_READONLY=true
```

Qdrant and vector retrieval:

```env
VECTOR_ENABLED=true
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION_PREFIX=nl2sql
EMBEDDING_MODEL=/path/to/BAAI/bge-m3
```

Do not commit `backend/.env`.

## Data and Metadata

Generate DuckDB demo data:

```bash
python scripts/generate_ecommerce_data.py
```

Sync DuckDB metadata:

```bash
python scripts/sync_metadata.py
```

Sync ClickHouse metadata:

```bash
PYTHONPATH=. python - <<'PY'
from backend.app.metadata.sync import sync_metadata
print(sync_metadata(datasource_name="clickhouse_ecommerce"))
PY
```

Seed ClickHouse from DuckDB CSV exports:

```bash
python scripts/seed_clickhouse.py
```

Rebuild vector index:

```bash
python scripts/rebuild_vector_index.py
```

## Running Locally

Backend:

```bash
PYTHONPATH=. python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5174/
```

If you run the backend on a non-default port, adjust the frontend proxy target as needed.

## Docker

Full demo stack:

```bash
docker compose up --build
```

Reset volumes and generated data:

```bash
docker compose down -v
docker compose up --build
```

Start Qdrant only:

```bash
docker compose --profile vector up -d qdrant
```

Useful sanity checks:

```bash
docker compose config --quiet
docker compose build backend frontend
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8123/ping
```

## Tests and Evals

Backend tests:

```bash
PYTHONPATH=. pytest backend/tests -q
```

Frontend build:

```bash
cd frontend
npm run build
```

Mock smoke eval:

```bash
PYTHONPATH=. python scripts/run_smoke_eval.py
```

DeepSeek real eval:

```bash
PYTHONPATH=. python scripts/run_smoke_eval.py --provider deepseek --report-path evals/reports/deepseek_latest.md
```

Vector comparison:

```bash
PYTHONPATH=. python scripts/run_smoke_eval.py --provider mock --vector-compare --report-path evals/reports/phase4_compare.md
```

## MCP Tools

Install MCP dependencies:

```bash
python -m pip install -e "backend[mcp]"
```

Smoke test:

```bash
python scripts/run_mcp_smoke.py
```

Available stdio servers:

```text
mcp_servers.db_tools
  list_tables
  get_table_schema
  query_readonly

mcp_servers.olap_tools
  explain_query
  metric_catalog_search
```

Example MCP client config:

```json
{
  "mcpServers": {
    "nl2sql-db-tools": {
      "command": "/home/hao/.venvs/nl2sql-pro/bin/python",
      "args": ["-m", "mcp_servers.db_tools"],
      "cwd": "/home/hao/workspace/nl2sql_pro",
      "env": {
        "PYTHONPATH": "/home/hao/workspace/nl2sql_pro"
      }
    },
    "nl2sql-olap-tools": {
      "command": "/home/hao/.venvs/nl2sql-pro/bin/python",
      "args": ["-m", "mcp_servers.olap_tools"],
      "cwd": "/home/hao/workspace/nl2sql_pro",
      "env": {
        "PYTHONPATH": "/home/hao/workspace/nl2sql_pro"
      }
    }
  }
}
```

Adjust `command`, `cwd`, and `PYTHONPATH` for your machine.
