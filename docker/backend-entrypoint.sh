#!/bin/sh
set -eu

cd /app

python scripts/generate_ecommerce_data.py

if [ "${CLICKHOUSE_ENABLED:-false}" = "true" ]; then
  python scripts/export_ecommerce_csv.py
  python - <<'PY'
import os
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

host = os.environ.get("CLICKHOUSE_HOST", "clickhouse")
port = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
user = os.environ.get("CLICKHOUSE_USER", "default")
password = os.environ.get("CLICKHOUSE_PASSWORD", "")
params = {"user": user, "query": "SELECT 1"}
if password:
    params["password"] = password
url = f"http://{host}:{port}/?{urlencode(params)}"

deadline = time.time() + 120
last_error = None
while time.time() < deadline:
    try:
        with urlopen(url, timeout=3) as response:
            response.read()
        break
    except URLError as exc:
        last_error = exc
        time.sleep(2)
else:
    raise SystemExit(f"ClickHouse did not become ready: {last_error}")
PY
  python scripts/seed_clickhouse.py
  python - <<'PY'
from backend.app.metadata.sync import sync_metadata

print({"duckdb_ecommerce": sync_metadata(datasource_name="duckdb_ecommerce")})
print({"clickhouse_ecommerce": sync_metadata(datasource_name="clickhouse_ecommerce")})
PY
else
  python scripts/sync_metadata.py
fi

exec python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
