# Nodify Agents Flow

**Nodify Agents Flow** is a lightweight, sequential AI agent pipeline runner backed by [Nodify](https://nodify-api.azirar.ovh). Pipelines are stored as Nodify content objects, executed locally against Ollama, enriched by optional Python pre/post-execution scripts, and the execution summaries are automatically pushed back to Nodify's `datas` endpoint. Datas are the single source of truth: the local SQLite cache is a mirror of Nodify, and every run history entry is materialized as a `run_summary_*` data row.

---

## Table of Contents

- [Overview](#overview)
- [Architecture & Tech Stack](#architecture--tech-stack)
- [Key Features](#key-features)
- [Environment Variables](#environment-variables)
- [Pipeline Configuration Format](#pipeline-configuration-format)
- [Nodify Integration & Synchronization](#nodify-integration--synchronization)
- [API Reference](#api-reference)
- [Database Schema (SQLite)](#database-schema-sqlite)
- [Testing](#testing)
- [Getting Started](#getting-started)
- [Docker Deployment](#docker-deployment)

---

## Overview

1. **Content Binding**: Pipelines are registered by Nodify **content code** (e.g. `JSON-DEV-1794941037653`).
2. **Local Caching & Execution**: The runner pulls the pipeline payload from Nodify, caches the parsed config in SQLite, then executes the workflow step-by-step with Ollama as the LLM engine.
3. **Sandbox Scripts**: Each step can run optional Python snippets — `script_before` (mutates `state`) and `script_after` (processes the LLM `output`).
4. **Single Source of Truth = `datas`**: When a pipeline finishes (success **or** failure), the runner writes a `run_summary_<job_id_prefix>` entry to Nodify `datas`, then immediately re-syncs it back into the local SQLite cache. There is **no separate `runs` table** — the run history *is* the set of `run_summary_*` datas.
5. **Background Sync**: A background thread re-syncs every registered config (config payload + datas) at a configurable interval.

---

## Architecture & Tech Stack

- **Framework**: Python 3.13, Flask (REST API + Web UI server)
- **Database**: SQLite3 (thread-safe, cached mirror of Nodify configs + datas)
- **LLM Engine**: Ollama HTTP API (default `qwen2.5:0.5b`) via `urllib.request`
- **External API**: Nodify REST (`/contents/code/{code}`, `/datas/`)
- **Testing**: `pytest` with `monkeypatch` mocks for Nodify and Ollama
- **Deployment**: Docker Compose (optional embedded Ollama, or external Ollama host)

### Core Components

| Module | Role |
| :--- | :--- |
| `runner.py` | Flask routes, in-memory job registry, background sync thread, `run_pipeline()` orchestrator, run-summary normalization helpers. |
| `nodify.py` | Thin HTTP wrapper for the Nodify API (`get_content_by_code`, `save_data`, `get_datas_by_content`, `delete_data`). |
| `db.py` | Thread-safe SQLite persistence: `configs` + `datas` (the legacy `runs` table is dropped at startup). |
| `llm.py` | Minimal Ollama API client (`llm.chat(model, prompt, host)`). |
| `sandbox.py` | Isolated execution helper for `script_before` / `script_after` user scripts. |

---

## Key Features

- **English-only reporting**: Error strings sent to Nodify (e.g. connection failures) are normalized to English, overriding OS-specific messages like Windows `WinError 10061`.
- **Compact summary payloads**: `run_summary_*` datas contain step names, statuses, per-step durations (seconds), messages, and the overall status + error.
- **Automatic background sync**: Configs + datas are re-synced on startup and every `SYNC_INTERVAL_SECONDS` (default `600`).
- **Event-driven refresh after each run**: `_publish_summary()` writes the summary to Nodify and immediately calls `sync_one()`, so the UI reflects the new run without waiting for the next background tick.
- **Graceful failure handling**: Any exception in a step marks the run `failed`, records an English error, and still publishes a valid `run_summary_*` entry.
- **Resilient sync**: `get_datas_by_content` raises on HTTP errors (no more silent empty `[]`); `upsert_data_entries` guards against duplicate `id` in upstream payloads.
- **Configurable LLM**: `OLLAMA_HOST` can point to an embedded Docker service (`http://ollama:11434`) or to an external Ollama instance (`http://192.168.1.42:11434`, `http://host.docker.internal:11434`, …).

---

## Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `NODIFY_API_URL` | Base URL of the Nodify API (*required*) | — |
| `OLLAMA_HOST` | Ollama HTTP host (local service or external) | `http://localhost:11434` |
| `OLLAMA_MODEL` | Ollama model name | `qwen2.5:0.5b` |
| `SYNC_INTERVAL_SECONDS` | Background sync interval (seconds) | `600` |
| `PORT` | Flask HTTP port | `5000` |
| `DB_PATH` | Path to the SQLite file (inside container) | `data/agentflow.db` |

> `SYNC_RUN` is a **test-only** flag (`app.config['SYNC_RUN']=True`) that makes `run_pipeline` synchronous to avoid races in pytest. It is never set from environment variables in production.

---

## Pipeline Configuration Format

The Nodify content `payload` field contains a JSON structure defining the initial state and execution steps:

```json
{
  "name": "Customer Support Router",
  "initial_state": {
    "user_message": "i cant access my acount since yesterday!!! i need help fast"
  },
  "steps": [
    {
      "name": "normalize",
      "context": "Fix spelling and grammar. Return only the corrected text:\n\n{{state.user_message}}",
      "script_before": "def run(state):\n    state['user_message'] = state.get('user_message', '').strip()\n    return state"
    },
    {
      "name": "extract",
      "context": "Extract category and urgency from message: {{state.output_normalize}}"
    },
    {
      "name": "respond",
      "context": "Generate a concise customer response based on: {{state.output_extract}}",
      "script_after": "def run(state, output):\n    state['final_response'] = output\n    return state"
    }
  ]
}
```

**Placeholders** — `{{state.<path>}}` is substituted before each step:
- dotted paths access nested dicts (`{{state.user.id}}`)
- missing paths resolve to `""`
- dicts/lists are JSON-serialized inline

**State flow**:
- `script_before(state) -> state` mutates the state before rendering the prompt.
- After the LLM response, either `script_after(state, output) -> state` runs, **or** the default writes `state.output_<sanitized_step_name> = output`.

---

## Nodify Integration & Synchronization

### Sync triggers

| Trigger | Path | Frequency |
| :--- | :--- | :--- |
| Startup | `sync_all()` in a daemon thread | once |
| Background loop | `sync_all()` | every `SYNC_INTERVAL_SECONDS` |
| After a run | `_publish_summary()` → `sync_one()` | after every run |
| Manual | `POST /api/sync-all` or `POST /api/configs/<code>/sync` | on demand |
| Content add | `POST /api/configs` | on demand |

`sync_one(content_code)`:
1. `GET /contents/code/{code}` → parse `payload`, upsert the `configs` row.
2. `GET /datas/contentCode/{code}` → `upsert_data_entries()` replaces all local datas for that content code.
3. Any HTTP error is stored in `configs.last_sync_error` and surfaced via the API.

### Run summaries written to Nodify (`POST /datas/`)

Each completed or failed run appends a `run_summary_<job_id_prefix>` entry whose `value` is a JSON string:

**Success payload**
```json
{
  "status": "success",
  "creationDate": "2026-11-17T22:14:11+00:00",
  "durationSeconds": 3.45,
  "steps": [
    { "name": "normalize", "status": "done", "duration": 1.12, "message": "Success" },
    { "name": "respond",   "status": "done", "duration": 2.33, "message": "Success" }
  ]
}
```

**Failure payload**
```json
{
  "status": "failed",
  "creationDate": "2026-11-17T22:14:11+00:00",
  "durationSeconds": 0.05,
  "error": "Connection refused: unable to connect to Ollama service",
  "steps": [
    { "name": "normalize", "status": "failed", "duration": 0.05, "message": "Failed: LLM down" },
    { "name": "respond",   "status": "pending", "duration": 0, "message": "Pending" }
  ]
}
```

### How run history is exposed

There is **no `runs` table**. Run history is derived on the fly from `datas`:

- `GET /api/configs/{code}/runs` → every `run_summary_*` data, normalized to:
  ```json
  {
    "job_id": "run_summary_ab12cd34",
    "content_code": "JSON-DEV-...",
    "description": "Run ab12cd34 Summary",
    "status": "done | failed",
    "started": "2026-11-17T22:14:11+00:00",
    "finished": "2026-11-17T22:14:14+00:00",
    "duration": 3.45,
    "error": null,
    "steps": [ ... ]
  }
  ```
- `GET /api/configs/{code}` → adds computed `success_count` / `failure_count` / `last_run_at` / `last_run_status` derived from the same datas.
- `GET /api/jobs/{job_id}` → if the job is still **in memory** it returns live progress; otherwise it **falls back to the persisted `run_summary_*`** row via `db.find_data_by_key(job_id)`.

---

## API Reference

### Configurations & Sync

- `GET /api/configs` — List configs with **computed** `success_count`, `failure_count`, `last_run_at`, `last_run_status`, plus `datas_count`.
- `POST /api/configs` — Import/sync a config by code (`{"code": "JSON-DEV-..."}`). Returns `201` with the config (incl. computed stats) or `400` on sync failure.
- `GET /api/configs/<content_code>` — Config detail: config JSON, computed stats, and inline `datas` array.
- `DELETE /api/configs/<content_code>` — Remove the local config **and all its datas** (cascade).
- `POST /api/configs/<content_code>/sync` — Force re-sync of one config (returns the refreshed config with computed stats).
- `POST /api/sync-all` — Force re-sync of all configs → `{"ok": N, "errors": M}`.

### Pipeline Execution & Data

- `POST /api/configs/<content_code>/run` — Start an asynchronous run. Returns `202` with `{ "job_id": "<uuid>" }`.
- `GET /api/configs/<content_code>/runs` — Normalized run history (from `datas`).
- `GET /api/configs/<content_code>/datas` — Raw synced datas for the content code.
- `GET /api/jobs/<job_id>` — Live job if still in memory, otherwise the persisted `run_summary_*` in `datas`. `404` if neither exists.
- `DELETE /api/jobs/<job_id>` — Remove a job from the in-memory registry (persisted summaries remain).

### System Health

- `GET /api/health` — `{"status": "ok", "model": "<OLLAMA_MODEL>"}`.

---

## Database Schema (SQLite)

### `configs`
| Column | Type | Notes |
| :--- | :--- | :--- |
| `content_code` | TEXT PK | Nodify content code |
| `description` | TEXT | Nodify content description (display name) |
| `name` | TEXT | Pipeline `name` from payload |
| `config_json` | TEXT | Parsed pipeline config |
| `last_synced` | TEXT | ISO timestamp of last sync |
| `last_sync_error` | TEXT | Null when last sync succeeded |

> `success_count`, `failure_count`, `last_run_at`, `last_run_status` columns exist for backward compatibility but are **never written**; the API returns values computed from `datas`.

### `datas`
| Column | Type | Notes |
| :--- | :--- | :--- |
| `id` | TEXT PK | From Nodify (fallback: UUID) |
| `content_code` | TEXT FK | `configs.content_code` (ON DELETE CASCADE) |
| `key` | TEXT | Nodify data key (e.g. `run_summary_ab12cd34`) |
| `value` | TEXT | JSON string (verbatim from Nodify) |
| `name`, `data_type` | TEXT | Metadata |
| `creationDate`, `modificationDate` | INTEGER | Epoch milliseconds (from Nodify) |

**Indexes**: `idx_datas_content (content_code)`, `idx_datas_key (content_code, key)`.

> The legacy `runs` table is **dropped at startup** (`DROP TABLE IF EXISTS runs`) as part of the one-way migration to datas-as-single-source-of-truth.

---

## Testing

The suite uses `pytest` with mocked Nodify / Ollama fixtures and a per-test SQLite file.

```bash
pytest -v
```

Expected: **40 tests** across `tests/test_runner.py` and `tests/test_sandbox.py`.

### Test fixtures (`tests/conftest.py`)

- `fake_nodify` — in-memory replacement for `NodifyClient` (`contents`, `datas_store` exposed for assertions).
- `fake_llm` — deterministic `llm.chat`, configurable `responses` dict.
- `client` — Flask test client wired to a fresh SQLite, with `SYNC_RUN=True` so runs execute synchronously and assertions don't race.

### What is covered

- `render_context` (substitution, missing keys, dict serialization)
- `parse_pipeline_config` (JSON string, dict, invalid cases)
- Config CRUD + cascade delete (`datas` removed with the config)
- Pipeline execution (with / without `script_before` / `script_after`)
- `run_summary_*` persistence in `datas` (success **and** failure)
- `_compute_run_stats` (success / failure / last-run derivation)
- `_normalize_run_summary` (`done` ↔ `success`, finished timestamp computation)
- `_parse_summary_value` (dict, JSON string, invalid)
- `/api/jobs/{id}` fallback to persisted datas
- `/api/sync-all` (with and without configs)
- `sandbox.exec_before` / `exec_after` (state mutation, missing `run()`)

---

## Getting Started

### 1. Install dependencies

```bash
pip install flask werkzeug pytest python-dotenv
```

### 2. Start Ollama

```bash
ollama serve
ollama pull qwen2.5:0.5b
```

### 3. Launch the runner

```bash
# PowerShell
$env:NODIFY_API_URL="https://nodify-api.azirar.ovh"
python runner.py

# Linux / macOS
export NODIFY_API_URL="https://nodify-api.azirar.ovh"
python runner.py
```

The runner listens on `http://localhost:5000`.

### 4. Point at an external Ollama (optional)

```bash
export OLLAMA_HOST=http://192.168.1.42:11434
```

> On the Ollama host, `ollama serve` must listen on `0.0.0.0:11434` (export `OLLAMA_HOST=0.0.0.0:11434` before starting).

---

## Docker Deployment

### Files

- `Dockerfile` — Python 3.13-slim, copies the app, exposes port `5000`.
- `docker-compose.yml` — runner + optional embedded Ollama behind a `local-ollama` profile.
- `entrypoint.sh` — for the Ollama service: starts `ollama serve`, waits for readiness, pulls `$OLLAMA_MODEL` if absent, then execs the server.
- `.env` — Nodify URL, model, ports, sync interval.

### `.env` (example)

```env
NODIFY_API_URL=https://nodify-api.azirar.ovh
OLLAMA_MODEL=qwen2.5:0.5b
OLLAMA_HOST=http://ollama:11434
RUNNER_PORT=5700
SYNC_INTERVAL_SECONDS=30
COMPOSE_PROFILES=local-ollama
```

### `docker-compose.yml` (summary)

- `ollama` service (profile `local-ollama`): mounts `ollama_data` + `entrypoint.sh`, `healthcheck` via `ollama list`.
- `runner` service: mounts `./data:/app/data`, reads `DB_PATH=/app/data/agentflow.db`, `depends_on: ollama (service_healthy)`.
- `volumes`: `ollama_data`.

### Common commands

**Local Ollama (embedded)**

```bash
docker compose --profile local-ollama up -d --build
```

**External Ollama**

```bash
# Remove OLLAMA_HOST from .env, or set it to the external endpoint
docker compose up -d --build
```

**Migrating the DB path**

The container path is fixed (`DB_PATH=/app/data/agentflow.db`); only the **host** side of the volume changes:

```yaml
volumes:
  - /srv/nodify/data:/app/data              # Linux / macOS
  - C:/nodify/data:/app/data                # Windows
  - "C:/Users/Mon Nom/nodify/data:/app/data" # with spaces
```

**Inspect / reset**

```bash
docker compose ps
docker compose logs -f runner
docker compose down           # keep volumes (ollama models + local DB)
docker compose down -v        # ⚠ also wipes ollama_data and the DB
```

### Persistence notes

- `ollama_data` — Docker-managed volume for downloaded models. Survives `down` but not `down -v`.
- `./data` — bind mount, holds `agentflow.db`. Survives everything unless you delete the folder.
- On the **first startup**, `entrypoint.sh` pulls `$OLLAMA_MODEL` (can take a few minutes for larger models — the `healthcheck.start_period: 60s` gives it room).
