#!/usr/bin/env bash
set -euo pipefail

cd /app

python - <<'PY'
from pathlib import Path

requirements = Path("/app/requirements.txt")
text = requirements.read_text()
text = text.replace(
    "schema-lib @ file:///app/packages/schema_lib_v2",
    "schema-lib @ file:///app/packages/schema_lib_v1",
)
text = text.replace(
    "reporting-sdk @ file:///app/packages/reporting_sdk_v2",
    "reporting-sdk @ file:///app/packages/reporting_sdk_v1",
)
requirements.write_text(text)
PY

pip install --no-cache-dir --force-reinstall \
    -r /app/requirements.txt

python /app/app.py &
APP_PID=$!

for _ in $(seq 1 20); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "app pid: $APP_PID"
