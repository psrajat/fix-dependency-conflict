import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

BASE_URL = "http://localhost:8000"
APP_ROOT = Path("/app")


def wait_for_app(timeout: int = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(f"{BASE_URL}/health", timeout=1)
            if response.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


@pytest.fixture(scope="session", autouse=True)
def ensure_app_running():
    if not wait_for_app(5):
        proc = subprocess.Popen(
            [sys.executable, "/app/app.py"],
            cwd="/app",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not wait_for_app(20):
            proc.terminate()
            pytest.fail("application did not become healthy")
    yield


class TestHealthEndpoint:
    def test_health_status(self):
        response = requests.get(f"{BASE_URL}/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestIngestEndpoint:
    def test_ingest_accepts_valid_event(self):
        response = requests.post(
            f"{BASE_URL}/ingest",
            json={"source": "web", "event": "click", "user_id": "u-1", "note": ""},
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["accepted"] is True
        assert body["telemetry"]["channel"] == "web"
        assert body["telemetry"]["event"] == "click"
        assert body["telemetry"]["schema_version"] == "1.8.2"
        assert body["report"]["group"] == "web"
        assert body["report"]["schema_version"] == "1.8.2"

    def test_ingest_validates_required_fields(self):
        response = requests.post(f"{BASE_URL}/ingest", json={"source": "web"})
        assert response.status_code == 400


class TestReportingFlow:
    def test_summary_aggregates_sources(self):
        requests.post(f"{BASE_URL}/ingest", json={"source": "web", "event": "click", "user_id": "u-2"})
        requests.post(f"{BASE_URL}/ingest", json={"source": "mobile", "event": "open", "user_id": "u-3"})

        response = requests.get(f"{BASE_URL}/reports/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 2
        assert body["sources"]["web"] >= 1
        assert body["sources"]["mobile"] >= 1


class TestDependencyFixes:
    def test_requirements_select_compatible_reporting_branch(self):
        requirements = (APP_ROOT / "requirements.txt").read_text()
        active_requirements = "\n".join(
            line for line in requirements.splitlines() if not line.lstrip().startswith("#")
        )
        assert "schema_lib_v1" in active_requirements
        assert "schema_lib_v2" not in active_requirements
        assert "reporting_sdk_v1" in active_requirements
        assert "reporting_sdk_v2" not in active_requirements
        assert 'summary-sdk @ file:///app/packages/summary_sdk_v2' not in active_requirements

    def test_inactive_v2_branch_metadata_is_unchanged(self):
        reporting_v2 = (APP_ROOT / "packages/reporting_sdk_v2/pyproject.toml").read_text()
        schema_v2 = (APP_ROOT / "packages/schema_lib_v2/pyproject.toml").read_text()
        assert 'version = "2.2.0"' in reporting_v2
        assert 'schema-lib @ file:///app/packages/schema_lib_v2' in reporting_v2
        assert 'schema-lib @ file:///app/packages/schema_lib_v1' not in reporting_v2
        assert 'version = "2.1.0"' in schema_v2

    def test_selected_v1_branch_metadata_is_still_consistent(self):
        reporting_v1 = (APP_ROOT / "packages/reporting_sdk_v1/pyproject.toml").read_text()
        schema_v1 = (APP_ROOT / "packages/schema_lib_v1/pyproject.toml").read_text()
        assert 'version = "1.9.0"' in reporting_v1
        assert 'schema-lib @ file:///app/packages/schema_lib_v1' in reporting_v1
        assert 'version = "1.8.2"' in schema_v1

    def test_telemetry_sdk_was_not_rewritten_around_conflict(self):
        source = (APP_ROOT / "packages/telemetry_sdk/telemetry_sdk/encoder.py").read_text()
        assert "drop_empty=True" in source
        assert "required_fields" not in source

    def test_installed_schema_version_is_legacy_compatible(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import schema_lib; print(schema_lib.normalize_event({'event':'x'}, drop_empty=True)['schema_version'])",
            ],
            cwd="/app",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "1.8.2"

