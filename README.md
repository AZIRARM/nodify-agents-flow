# Nodify Agents Flow

**Nodify Agents Flow** is a lightweight, sequential AI agent pipeline runner backed by [Nodify](https://nodify-api.azirar.ovh). It allows users to store pipeline configurations inside Nodify content objects, run multi-step agent flows locally with Ollama, run Python pre/post-execution scripts, and automatically synchronize execution results and concise English status summaries back to Nodify's `datas` endpoint.

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

---

## Overview

1. **Content Binding**: Pipelines are registered using a Nodify content code (e.g. `JSON-DEV-1794941037653`).
2. **Local Caching & Execution**: The runner pulls the pipeline configuration payload from Nodify, stores it locally in SQLite, and runs the workflow step-by-step using Ollama as the LLM engine.
3. **Sandbox Scripts**: Steps can run optional Python scripts before execution (`script_before`) to adjust inputs or after execution (`script_after`) to process LLM outputs.
4. **Data Synchronization**: Execution results (success states or connection/runtime errors) are summarized in English and automatically saved to Nodify's `/datas/` REST endpoints as structured JSON objects.

---

## Architecture & Tech Stack

- **Framework**: Python 3.13, Flask (REST API & Web UI server)
- **Database**: SQLite3 (thread-safe, local caching of configs, runs, and datas)
- **LLM Engine**: Ollama HTTP API (e.g., `qwen2.5:0.5b`) via standard `urllib.request`
- **External API**: Nodify REST API (`/contents/code/{code}`, `/datas/`)
- **Testing**: `pytest` suite with `monkeypatch` mocks for API endpoints and LLM chats

### Core Components

| Module | Role |
| :--- | :--- |
| `runner.py` | Main application entry point, Flask routes, background sync threads, and `run_pipeline` runner logic. |
| `nodify.py` | Thin HTTP wrapper client for Nodify API interaction (`get_content_by_code`, `save_data`, `get_datas_by_content`). |
| `db.py` | Thread-safe SQLite persistence layer for configurations, execution runs, and cached data entries. |
| `llm.py` | Minimal Ollama API interface. |
| `sandbox.py` | Isolated execution helper for `script_before` and `script_after` user scripts. |

---

## Key Features

- **English-Only Reporting**: Error messages and step results posted to Nodify are standardized in English, overriding local OS-bound error strings (such as Windows `WinError 10061`).
- **Compact Summary Payloads**: Pushes concise JSON execution reports (containing step names, status, execution duration in seconds, and status messages) directly to Nodify `datas`.
- **Automatic Background Synchronization**: Re-syncs content payloads and data entries on startup and periodically (every 600s by default).
- **Graceful Failure Handling**: Failures inside steps or network timeouts are logged cleanly in local database runs and synchronized as `run_summary_<job_id>` entries in Nodify.

---

## Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `NODIFY_API_URL` | Base URL of the Nodify API (*Required*) | `https://nodify-api.azirar.ovh` |
| `OLLAMA_HOST` | Host address of the local Ollama LLM service | `http://localhost:11434` |
| `OLLAMA_MODEL` | Ollama model to use for prompts | `qwen2.5:0.5b` |
| `SYNC_INTERVAL_SECONDS` | Background periodic sync interval | `600` |
| `PORT` | Flask web server port | `5000` |
| `DB_PATH` | Path to SQLite database file | `runner.db` |

---

## Pipeline Configuration Format

The Nodify content `payload` field contains a JSON structure defining initial state and execution steps:

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

---

## Nodify Integration & Synchronization

### 1. Data Summaries Posted to Nodify (`POST /datas/`)

When a pipeline completes (or fails), a data payload is saved under the key `run_summary_<job_id_prefix>`:

#### Success Payload Example
```json
{
  "status": "success",
  "creationDate": "2026-11-17T22:14:11Z",
  "durationSeconds": 3.45,
  "steps": [
    {
      "name": "normalize",
      "status": "done",
      "duration": 1.12,
      "message": "Success"
    },
    {
      "name": "respond",
      "status": "done",
      "duration": 2.33,
      "message": "Success"
    }
  ]
}
```

#### Failure Payload Example
```json
{
  "status": "failed",
  "creationDate": "2026-11-17T22:14:11Z",
  "durationSeconds": 0.05,
  "error": "Connection refused: unable to connect to Ollama service",
  "steps": [
    {
      "name": "normalize",
      "status": "failed",
      "duration": 0.05,
      "message": "Failed: Connection refused (Ollama service unavailable)"
    }
  ]
}
```

---

## API Reference

### Configurations & Sync

- `GET /api/configs` — List all registered pipeline configurations.
- `POST /api/configs` — Import/sync a configuration from Nodify by code (`{"code": "JSON-DEV-1794941037653"}`).
- `GET /api/configs/<content_code>` — Retrieve a specific pipeline configuration.
- `DELETE /api/configs/<content_code>` — Remove a local configuration and all associated runs.
- `POST /api/configs/<content_code>/sync` — Force re-sync of a specific content configuration.
- `POST /api/sync-all` — Force re-sync of all cached configurations.

### Pipeline Execution & Data

- `POST /api/configs/<content_code>/run` — Trigger an asynchronous run job (Returns `202 Accepted` with `job_id`).
- `GET /api/configs/<content_code>/runs` — List run history for a given pipeline code.
- `GET /api/configs/<content_code>/datas` — Retrieve synced Nodify data entries stored locally.
- `GET /api/jobs/<job_id>` — Get real-time status, step progression, and results for a specific run job.
- `DELETE /api/jobs/<job_id>` — Delete a job execution record.

### System Health

- `GET /api/health` — Check server status and configured Ollama model name.

---

## Database Schema (SQLite)

### `configs`
- `content_code` (TEXT PRIMARY KEY)
- `description` (TEXT)
- `name` (TEXT)
- `config_json` (TEXT)
- `success_count` (INTEGER), `failure_count` (INTEGER)
- `last_run_status` (TEXT), `last_run_at` (TEXT), `last_sync_error` (TEXT)

### `runs`
- `id` (TEXT PRIMARY KEY)
- `content_code` (TEXT)
- `description` (TEXT)
- `status` (TEXT: `pending`, `running`, `done`, `failed`)
- `steps_json` (TEXT), `initial_state_json` (TEXT), `result_json` (TEXT)
- `error` (TEXT), `traceback` (TEXT)
- `started` (TEXT), `finished` (TEXT)

### `datas`
- `id` (TEXT PRIMARY KEY)
- `content_code` (TEXT)
- `key` (TEXT)
- `value` (TEXT - JSON encoded string)
- `name` (TEXT), `data_type` (TEXT)
- `creationDate` (TEXT), `modificationDate` (TEXT)

---

## Testing

The test suite uses `pytest` and mocks external HTTP dependencies (`nodify` client and `ollama` LLM).

To run all unit tests:
```bash
pytest -v
```

### Key Test File Overview
- `tests/test_runner.py`: Tests API routes, pipeline execution flow, context rendering, and summary saving to `datas`.
- `tests/test_sandbox.py`: Verifies safety and logic execution of `script_before` and `script_after` Python snippets.
- `tests/conftest.py`: Provides mock client fixtures (`fake_nodify`, `fake_llm`, test SQLite DB setup).

---

## Getting Started

### 1. Installation

Clone the repository and install dependencies:
```bash
pip install flask werkzeug pytest python-dotenv
```

### 2. Start Ollama Service

Ensure Ollama is running locally:
```bash
ollama serve
ollama pull qwen2.5:0.5b
```

### 3. Launch the Runner

Set the environment variable and run the Flask application:
```bash
# Windows PowerShell
$env:NODIFY_API_URL="https://nodify-api.azirar.ovh"
python runner.py

# Linux / macOS
export NODIFY_API_URL="https://nodify-api.azirar.ovh"
python runner.py
```

The runner will start on `http://localhost:5000`.