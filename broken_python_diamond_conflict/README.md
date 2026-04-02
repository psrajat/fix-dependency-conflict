# Broken Python Diamond Conflict

## Task Description

This is a Harbor benchmark task that challenges an agent to diagnose and fix a
broken Python event-ingestion service caused by a **diamond dependency
conflict**: two packages that are both required at runtime depend on
incompatible versions of a shared third package.

The agent is given a Docker container with a running Flask service. The service
appears healthy at startup (health checks pass), but a key request path fails
at runtime. The agent must identify the root cause in the dependency tree, make
the minimal correct change to `requirements.txt`, and reinstall so the fix
survives a cold start.

The task tests whether a model can:
1. Diagnose a version-compatibility failure without obvious stack traces at startup
2. Understand which dependency version satisfies all callers
3. Make a declarative fix (changing the pinned requirement) rather than mutating SDK source
4. Reinstall packages so the running environment matches the new requirements

---

## The Application

[environment/app.py](environment/app.py) is a small Flask service with three endpoints:

### `GET /health`

Health probe. Returns `{"status": "ok"}` with HTTP 200. Always passes even in
the broken state.

### `POST /ingest`

Accepts a JSON event payload (`source`, `event`, optional `user_id`). Validates
that both `source` and `event` are present, then:

1. Calls `telemetry_sdk.encode_event(payload)` to normalize and tag the event
2. Calls `reporting_sdk.build_report_record(payload)` to build a structured report record
3. Appends both outputs to an in-memory list
4. Returns HTTP 202 with the raw telemetry and report payloads

Returns HTTP 400 if `source` or `event` is missing.

**In the broken state:** this endpoint returns HTTP 500 because `telemetry_sdk`
calls `schema_lib.normalize_event(..., drop_empty=True)` and the installed
`schema_lib` is v2, which does not accept `drop_empty`.

### `GET /reports/summary`

Returns aggregated counts of all ingested events, grouped by source:

```json
{"total": 3, "sources": {"web": 2, "mobile": 1}}
```

---

## The Dependency Graph

The service depends on three vendored packages inside `environment/packages/`:

```
app.py
  ├── telemetry-sdk  (v1.4.0)           uses schema_lib.normalize_event(drop_empty=True)
  ├── reporting-sdk  (v1 or v2)         uses schema_lib.normalize_event(...)
  └── schema-lib     (v1.8.2 or v2.1.0) provides normalize_event(...)
```

Two versions of `reporting-sdk` and `schema-lib` are vendored side-by-side:

| Package              | Version | `normalize_event` signature                         | Compatible with `telemetry_sdk`? |
|----------------------|---------|-----------------------------------------------------|----------------------------------|
| `schema_lib_v1`      | 1.8.2   | `normalize_event(payload, *, drop_empty=False)`     | **Yes** — accepts `drop_empty`   |
| `schema_lib_v2`      | 2.1.0   | `normalize_event(payload, *, required_fields=())`   | **No** — `drop_empty` is unknown |
| `reporting_sdk_v1`   | 1.9.0   | calls `normalize_event(payload, drop_empty=True)`   | Yes (depends on schema_lib_v1)   |
| `reporting_sdk_v2`   | 2.2.0   | calls `normalize_event(payload, required_fields=…)` | No (declares dep on schema_lib_v2) |
| `telemetry_sdk`      | 1.4.0   | calls `normalize_event(payload, drop_empty=True)`   | Only with schema_lib_v1          |

---

## The Diamond Dependency Conflict

`telemetry_sdk` and `reporting_sdk_v2` both depend on `schema-lib` but need
different versions:

```
requirements.txt (broken)
  ├── telemetry-sdk    → needs schema_lib_v1  (calls drop_empty=True)
  ├── reporting-sdk-v2 → declares dep on schema_lib_v2
  └── schema-lib-v2   ← actively installed  ← CONFLICT
```

When Python tries to call `schema_lib.normalize_event(payload, drop_empty=True)`
via `telemetry_sdk`, it blows up:

```
TypeError: normalize_event() got an unexpected keyword argument 'drop_empty'
```

This is a diamond conflict because there is one shared library (`schema-lib`)
with two consumers that require incompatible interfaces. Since only one version
of `schema-lib` can be installed at a time, at least one consumer will break.

The resolution is to choose the branch where **all consumers are satisfied** —
which means switching to `reporting_sdk_v1` (which also uses `drop_empty=True`
and depends on `schema_lib_v1`), so the whole dependency tree can converge on
`schema_lib_v1`.

Current broken `requirements.txt`:

```
Flask==3.0.3
schema-lib @ file:///app/packages/schema_lib_v2     ← wrong version
telemetry-sdk @ file:///app/packages/telemetry_sdk
reporting-sdk @ file:///app/packages/reporting_sdk_v2  ← wrong branch
```

---

## The Ideal Fix (Step by Step)

**Step 1 — Identify the failing call**

Run the service and hit `POST /ingest`. The 500 response body or the Flask log
will show:

```
TypeError: normalize_event() got an unexpected keyword argument 'drop_empty'
```

Trace it: `app.py` → `telemetry_sdk.encode_event()` → `schema_lib.normalize_event(..., drop_empty=True)`.

**Step 2 — Inspect the installed schema_lib**

```bash
python -c "import schema_lib; import inspect; print(inspect.signature(schema_lib.normalize_event))"
# (payload, *, required_fields=()) — this is v2, incompatible
```

**Step 3 — Survey the vendored alternatives**

```bash
ls /app/packages/
# reporting_sdk_v1/  reporting_sdk_v2/  schema_lib_v1/  schema_lib_v2/  telemetry_sdk/
```

Check `schema_lib_v1`:
```bash
grep -n "def normalize_event" /app/packages/schema_lib_v1/schema_lib/__init__.py
# def normalize_event(payload: dict, *, drop_empty: bool = False) -> dict:
```

v1 accepts `drop_empty`. Also check `reporting_sdk_v1`:
```bash
grep -n "normalize_event" /app/packages/reporting_sdk_v1/reporting_sdk/formatter.py
# normalized = normalize_event(payload, drop_empty=True)  ← same calling convention
```

Both `telemetry_sdk` and `reporting_sdk_v1` call `normalize_event` with
`drop_empty=True`. Switching to the v1 branch makes the tree consistent.

**Step 4 — Edit `requirements.txt`**

```bash
# Change this:
# schema-lib @ file:///app/packages/schema_lib_v2
# reporting-sdk @ file:///app/packages/reporting_sdk_v2

# To this:
schema-lib @ file:///app/packages/schema_lib_v1
reporting-sdk @ file:///app/packages/reporting_sdk_v1
```

**Step 5 — Reinstall and verify**

```bash
pip install --no-cache-dir --force-reinstall -r /app/requirements.txt
python /app/app.py &
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/ingest \
  -H 'Content-Type: application/json' \
  -d '{"source":"web","event":"click","user_id":"u-1"}'
curl -s http://localhost:8000/reports/summary
```

All three should return success. The reinstall step is required — the verifier
checks the live installed package, not just the text in `requirements.txt`.

**What not to do:**
- Do not modify `telemetry_sdk/encoder.py` to remove `drop_empty=True` — the verifier checks this
- Do not modify `reporting_sdk_v2/pyproject.toml` to point at v1 — the verifier checks this file is unchanged
- Do not patch `schema_lib_v2` to add a `drop_empty` parameter — keeps v2 installed, fails structural checks
- Do not inline the normalization logic directly into `app.py` — bypasses the SDK

---

## How It Is Tested ([tests/test_outputs.py](tests/test_outputs.py))

The verifier runs 9 pytest tests split across four classes:

### `TestHealthEndpoint` (1 test)

- `test_health_status`: `GET /health` returns HTTP 200 and `{"status": "ok"}`

### `TestIngestEndpoint` (2 tests)

- `test_ingest_accepts_valid_event`: `POST /ingest` with a valid payload returns HTTP 202, and the response contains `schema_version = "1.8.2"` in both `telemetry` and `report` — confirming `schema_lib_v1` is live
- `test_ingest_validates_required_fields`: `POST /ingest` without `event` returns HTTP 400

### `TestReportingFlow` (1 test)

- `test_summary_aggregates_sources`: ingest from two different sources, then `GET /reports/summary` returns `total >= 2` with correct per-source counts

### `TestDependencyFixes` (5 tests) — structural guardrails

These tests prevent superficial fixes from passing by checking the actual files
and the installed runtime state:

| Test | What it checks |
|------|----------------|
| `test_requirements_select_compatible_reporting_branch` | Active (non-commented) lines in `requirements.txt` reference `schema_lib_v1` and `reporting_sdk_v1`, not v2 variants |
| `test_inactive_v2_branch_metadata_is_unchanged` | `reporting_sdk_v2/pyproject.toml` still declares `schema-lib-v2` as its dependency — ensures the agent did not mutate the v2 package to point at v1 |
| `test_selected_v1_branch_metadata_is_still_consistent` | `reporting_sdk_v1/pyproject.toml` is intact and still references `schema_lib_v1` |
| `test_telemetry_sdk_was_not_rewritten_around_conflict` | `telemetry_sdk/encoder.py` still calls `normalize_event(payload, drop_empty=True)` — ensures the agent did not remove the call that drives the requirement |
| `test_installed_schema_version_is_legacy_compatible` | Spawns a Python subprocess and imports `schema_lib` at runtime; calls `normalize_event({'event':'x'}, drop_empty=True)` and asserts the returned `schema_version` is `"1.8.2"` — confirms packages were actually reinstalled |

The last test (`test_installed_schema_version_is_legacy_compatible`) is what
trips agents that correctly edit `requirements.txt` but forget to run
`pip install --force-reinstall`. The requirements file is fixed, but the
running Python environment still has `schema_lib_v2` installed.

---

## Results

**Best run:** `jobs/2026-04-01__21-06-05_BEST_RUN`  
**Model:** Kimi K2 (`moonshotai/kimi-k2-0905`) via the `terminus-2` agent  
**Trials:** 10  
**Score:** 4 / 10 (mean = 0.4)

### Outcome by trial

| Trial | Result | Tests passed | Root failure |
|-------|--------|--------------|--------------|
| `mRbVRss` | PASS | 9 / 9 | — |
| `erujfzv` | PASS | 9 / 9 | — |
| `58Vs2pN` | PASS | 9 / 9 | — |
| `DUruTfa` | PASS | 9 / 9 | — |
| `j8wK7ju` | FAIL | 8 / 9 | Correctly updated `requirements.txt` to use v1 SDKs and left all vendor source unchanged, but **did not run `pip install --force-reinstall`**. The live installed schema_lib was still v2, so the runtime import check failed. |
| `R486uxs` | FAIL | 6 / 9 | Did not switch requirements to v1. Left telemetry_sdk untouched. The app still had schema_lib_v2 installed, causing the ingest endpoint and schema version checks to fail. |
| `XXfq3dU` | FAIL | 5 / 9 | Did not switch requirements. Also mutated `telemetry_sdk/encoder.py` to remove `drop_empty=True`, making ingest work around the conflict but breaking the structural `test_telemetry_sdk_was_not_rewritten_around_conflict` check. |
| `3bwAkd6` | FAIL | 5 / 9 | Same pattern as XXfq3dU — mutated telemetry_sdk to remove `drop_empty`, did not fix requirements. |
| `qHFCQhH` | FAIL | 5 / 9 | Same pattern as XXfq3dU. |
| `dbkgeV9` | FAIL | 5 / 9 | Same pattern as XXfq3dU. |

### Failure pattern summary

**"Hack the caller" pattern (4 trials):** The model diagnosed that `telemetry_sdk`
was calling `normalize_event` with an unsupported keyword. Instead of switching
to a compatible `schema_lib`, it patched `telemetry_sdk/encoder.py` to remove
the `drop_empty=True` call. This makes the app functional but represents the
wrong fix — the verifier rejects it because `telemetry_sdk` must remain
untouched.

**"Forgot to reinstall" pattern (1 trial):** The model correctly identified the
root cause and updated `requirements.txt`, but did not follow up with
`pip install --force-reinstall`. All file-level checks passed; only the runtime
import test failed.

**"Partial fix" pattern (1 trial):** The model did not switch requirements at
all, so the schema_lib_v2 remained installed and multiple tests failed.

**Full correct pattern (4 trials):** Switch `requirements.txt` to
`schema_lib_v1` + `reporting_sdk_v1`, run `pip install --force-reinstall`,
restart the app. All 9 tests pass.

Top-level task layout:

- [instruction.md](instruction.md): prompt shown to the agent
- [task.toml](task.toml): Harbor task metadata and runtime configuration
- [README.md](README.md): author-facing explanation of the task design
- [run_limited.sh](run_limited.sh): helper for running Harbor with tighter knobs
- [environment/](environment/): files copied into `/app/` in the container
- [solution/](solution/): oracle fix used to validate the task
- [tests/](tests/): verifier logic used after the solve step

Environment layout:

- [environment/Dockerfile](environment/Dockerfile): builds the task image and installs Python test/runtime dependencies
- [environment/entrypoint.sh](environment/entrypoint.sh): thin container entrypoint
- [environment/requirements.txt](environment/requirements.txt): top-level app dependencies installed into `/app/`
- [environment/app.py](environment/app.py): Flask service entrypoint with `/health`, `/ingest`, and `/reports/summary`
- [environment/packages/](environment/packages/): vendored internal SDK packages used by the service

Vendored package layout:

- [environment/packages/telemetry_sdk](environment/packages/telemetry_sdk): telemetry encoding package used by `/ingest`
- [environment/packages/reporting_sdk_v1](environment/packages/reporting_sdk_v1): healthy reporting branch used by `/ingest`
- [environment/packages/reporting_sdk_v2](environment/packages/reporting_sdk_v2): unused alternate reporting branch kept in the repo
- [environment/packages/schema_lib_v1](environment/packages/schema_lib_v1): healthy schema dependency used by telemetry/reporting v1
- [environment/packages/schema_lib_v2](environment/packages/schema_lib_v2): unused alternate schema branch kept in the repo
- [environment/packages/summary_sdk_v1](environment/packages/summary_sdk_v1): unused stable summary branch kept in the repo
- [environment/packages/summary_sdk_v2](environment/packages/summary_sdk_v2): installed summary branch containing the planted circular import

Validation layout:

- [solution/solve.sh](solution/solve.sh): oracle script that applies the accepted fix
- [tests/test.sh](tests/test.sh): fixed Harbor verifier wrapper
- [tests/test_outputs.py](tests/test_outputs.py): functional and source-level assertions

## Application Flow

The runtime starts in [environment/app.py](environment/app.py).

That file defines three HTTP paths:

1. `GET /health`
   - returns a trivial `{"status": "ok"}` response
   - exists to make the service look healthy at startup
2. `POST /ingest`
   - validates input
   - calls `telemetry_sdk.encode_event(...)`
   - calls `reporting_sdk.build_report_record(...)`
   - stores normalized event data in memory
3. `GET /reports/summary`
   - imports `summary_sdk.render_summary` lazily inside the handler
   - counts stored events by source
   - uses the summary SDK to produce the response payload

The important design choice is the lazy import inside the summary handler. That
is what allows startup and ingest to work while the summary endpoint still fails
at runtime.

## Effective Dependency Graph

```mermaid
graph TD
    A[app.py] --> B[telemetry-sdk]
    A --> C[reporting-sdk v1]
    A --> S[summary-sdk v2]

    B --> D[schema-lib v1]
    C --> D
    S --> V2[summary-sdk v2 package]

    B --> Y[normalize_event(..., drop_empty=True)]
    V2 --> Z[circular import between builder.py and labels.py]
```

The task deliberately mixes one healthy line and one broken line:

- ingest-side dependencies are healthy and should stay that way
- summary-side dependency is installed but internally broken

## Issues and Their Types

This version really has one root bug plus a few misleading signals around it.

### Issue 1: Runtime circular import in the installed summary SDK

Type:

- Python import-graph bug
- runtime-only failure
- vendored package defect

Code involved:

- [environment/packages/summary_sdk_v2/summary_sdk/__init__.py](environment/packages/summary_sdk_v2/summary_sdk/__init__.py)
- [environment/packages/summary_sdk_v2/summary_sdk/builder.py](environment/packages/summary_sdk_v2/summary_sdk/builder.py)
- [environment/packages/summary_sdk_v2/summary_sdk/labels.py](environment/packages/summary_sdk_v2/summary_sdk/labels.py)

Why it happens:

1. `summary_sdk.__init__` imports `render_summary` from `builder.py`
2. `builder.py` imports `TOTAL_FIELD` from `labels.py`
3. `labels.py` imports `normalize_sources` back from `builder.py`

That creates this cycle:

```text
builder.py -> labels.py -> builder.py
```

By the time Python evaluates `labels.py`, `builder.py` is only partially
initialized, so the import fails.

Observed symptom:

```text
ImportError: cannot import name 'normalize_sources' from partially initialized module 'summary_sdk.builder'
```

Where the user sees it:

- not on container startup
- not on `GET /health`
- not on `POST /ingest`
- only on `GET /reports/summary`

### Issue 2: Misleading healthy ingest path

Type:

- intentional distractor
- healthy dependency line
- not a bug in the current version

Code involved:

- [environment/requirements.txt](environment/requirements.txt)
- [environment/packages/telemetry_sdk/telemetry_sdk/encoder.py](environment/packages/telemetry_sdk/telemetry_sdk/encoder.py)
- [environment/packages/reporting_sdk_v1/reporting_sdk/formatter.py](environment/packages/reporting_sdk_v1/reporting_sdk/formatter.py)
- [environment/packages/schema_lib_v1/schema_lib/__init__.py](environment/packages/schema_lib_v1/schema_lib/__init__.py)

Why it matters:

The earlier versions of this task had a real dependency conflict on the ingest
side. That is no longer true. In the current version, the ingest path is
already correct, and any attempt to “fix” telemetry/reporting/schema is the
wrong move.

This matters because many models still overfit to the older dependency-conflict
pattern and start changing the healthy branch.

### Issue 3: Lazy import hides the bug until a real workflow is exercised

Type:

- delayed runtime failure
- debugging trap
- application wiring choice

Code involved:

- [environment/app.py](environment/app.py)

Why it matters:

The summary handler imports `summary_sdk` inside the function body rather than
at module load time. That means the service boots cleanly and looks healthy
until the summary route is actually hit.

This is not itself a defect to fix. It is the mechanism that makes the task
non-trivial.

## Current Broken Code

The active broken requirement selection is in
[environment/requirements.txt](environment/requirements.txt):

```text
Flask==3.0.3
schema-lib @ file:///app/packages/schema_lib_v1
telemetry-sdk @ file:///app/packages/telemetry_sdk
reporting-sdk @ file:///app/packages/reporting_sdk_v1
summary-sdk @ file:///app/packages/summary_sdk_v2
```

The key broken source pair is:

- [environment/packages/summary_sdk_v2/summary_sdk/builder.py](environment/packages/summary_sdk_v2/summary_sdk/builder.py)
- [environment/packages/summary_sdk_v2/summary_sdk/labels.py](environment/packages/summary_sdk_v2/summary_sdk/labels.py)

Their dependency direction is currently wrong because both files import from one
another.

## How It Should Be Fixed

The accepted fix is an in-place repair to `summary_sdk_v2`.

The intended approach is:

1. keep [environment/requirements.txt](environment/requirements.txt) on `summary_sdk_v2`
2. keep the ingest-side packages unchanged
3. remove the back-edge from `labels.py` to `builder.py`
4. preserve the existing public summary API

In practical terms, the fix should make [environment/packages/summary_sdk_v2/summary_sdk/labels.py](environment/packages/summary_sdk_v2/summary_sdk/labels.py) stop importing `normalize_sources` from `builder.py`.

After the fix, the dependency direction should become:

```text
builder.py -> labels.py
```

instead of:

```text
builder.py -> labels.py -> builder.py
```

The smallest correct final shape is:

- `TOTAL_FIELD = "total"` remains in `labels.py`
- `builder.py` continues importing `TOTAL_FIELD`
- `labels.py` no longer imports from `builder.py`
- summary rendering returns the same JSON shape as before

## What A Correct Result Looks Like

After the fix:

- `GET /health` returns `200`
- `POST /ingest` returns `202`
- `GET /reports/summary` returns `200`
- summary output contains `total` and `sources`
- reinstalling from `/app/requirements.txt` preserves the working state

The oracle implementation of that approach is in
[solution/solve.sh](solution/solve.sh).

## Wrong Fixes To Reject

The verifier should keep rejecting these classes of fixes:

1. bypassing `summary_sdk` inside [environment/app.py](environment/app.py)
2. patching only the live installed site-packages copy instead of the source under `/app/`
3. swapping the app over to `summary_sdk_v1`
4. changing telemetry, reporting, or schema packages even though ingest is already healthy
5. making one-off interactive `pip install` repairs that do not survive reinstall

## How The Tests Encode The Intended Fix

[tests/test_outputs.py](tests/test_outputs.py) checks both behavior and fix
shape:

- functional tests confirm health, ingest, and summary behavior
- requirement checks ensure the task still uses `summary_sdk_v2`
- source checks ensure `labels.py` was fixed in place
- runtime import checks ensure `summary_sdk` is actually importable after reinstall

That combination is what prevents superficial fixes from passing.
