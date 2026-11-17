"""
Nodify Agents Flow
==================

Sequential pipeline runner for AI agents, backed by Nodify.

The user enters a Nodify CONTENT code (e.g. JSON-DEV-1794941037653).
The runner fetches that content's payload from Nodify and caches it in a
local SQLite DB (description, config JSON, stats). Pipelines are executed
from the cached config and every run is stored in the DB.

The display name of a config is the Nodify content `description` field.

Sync:
    - At startup, all known configs are re-synced from Nodify.
    - A background thread re-syncs every SYNC_INTERVAL_SECONDS (default 600).

Environment:
    NODIFY_API_URL           Nodify API base URL
    OLLAMA_HOST              Ollama HTTP host
    OLLAMA_MODEL             Model name
    DB_PATH                  SQLite file path
    SYNC_INTERVAL_SECONDS    Background sync interval (default 600)
    PORT                     HTTP port (default 5000)
"""
import json
import os
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

import db
import llm
from nodify import NodifyClient
from sandbox import exec_before, exec_after


app = Flask(__name__, template_folder='templates')
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_prefix=1)

NODIFY_API_URL = os.environ.get('NODIFY_API_URL', '')
if not NODIFY_API_URL:
    raise SystemExit(
        "NODIFY_API_URL is not set. "
        "Export it before starting the runner, e.g. "
        "`export NODIFY_API_URL=https://nodify-api.azirar.ovh`"
    )

OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'qwen2.5:0.5b')
SYNC_INTERVAL = int(os.environ.get('SYNC_INTERVAL_SECONDS', '600'))

nodify = NodifyClient(NODIFY_API_URL)
db.init()
db.recover_abandoned_runs()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sanitize_key_part(s):
    return re.sub(r'[^a-zA-Z0-9_-]', '_', s or 'unnamed')


def parse_pipeline_config(payload):
    if payload is None:
        raise ValueError("content has no payload")
    if isinstance(payload, dict):
        config = payload
    elif isinstance(payload, str):
        config = json.loads(payload)
    else:
        raise ValueError("payload must be a JSON object or string")

    steps = config.get('steps')
    if not isinstance(steps, list) or not steps:
        raise ValueError("config.steps must be a non-empty list")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{i}] must be an object")
        if not step.get('name'):
            raise ValueError(f"steps[{i}].name is required")
        if 'context' not in step:
            raise ValueError(f"steps[{i}].context is required")
    return config


PLACEHOLDER_RE = re.compile(r'\{\{\s*state\.([a-zA-Z0-9_.]+)\s*\}\}')


def render_context(template, state):
    def repl(match):
        path = match.group(1).split('.')
        value = state
        for part in path:
            if isinstance(value, dict):
                value = value.get(part, '')
            else:
                return ''
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
    return PLACEHOLDER_RE.sub(repl, template)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
def sync_one(content_code):
    """Re-fetch a content from Nodify and update the cached config & datas."""
    try:
        content = nodify.get_content_by_code(content_code)
        if not content:
            print(f"[sync] Error: content not found for code {content_code}")
            db.set_sync_error(content_code, "content not found")
            return False
        
        config = parse_pipeline_config(content.get('payload'))
        description = (
            (content.get('description') or '').strip()
            or config.get('name')
            or content_code
        )
        name = config.get('name') or description
        db.upsert_config(content_code, description, name, config)

        datas = nodify.get_datas_by_content(content_code)
        print(f"[sync] Upserting {len(datas)} data entries for {content_code}")
        db.upsert_data_entries(content_code, datas)

        return True
    except Exception as e:
        print(f"[sync] Exception during sync_one for {content_code}: {e}")
        db.set_sync_error(content_code, str(e))
        return False


def sync_all():
    codes = db.list_config_codes()
    ok = err = 0
    for code in codes:
        if sync_one(code):
            ok += 1
        else:
            err += 1
    return ok, err


def background_sync_loop():
    while True:
        try:
            time.sleep(SYNC_INTERVAL)
            print(f"[sync] background sync starting")
            ok, err = sync_all()
            print(f"[sync] done: {ok} ok, {err} errors")
        except Exception as e:
            print(f"[sync] error: {e}")


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------
def run_pipeline(job_id):
    """Execute pipeline and log a concise summary to Nodify datas."""
    run = db.get_run(job_id)
    if not run:
        return
    config = run['config']
    steps_cfg = config['steps']
    state = dict(run['initial_state'] or {})
    
    start_time = time.time()
    steps_state = [
        {'name': s['name'], 'status': 'pending', 'duration': 0, 'message': 'Pending'}
        for s in steps_cfg
    ]
    db.update_run_steps(job_id, steps_state)

    try:
        for i, step_cfg in enumerate(steps_cfg):
            step_start = time.time()
            steps_state[i]['status'] = 'running'
            steps_state[i]['message'] = 'Running step'
            db.update_run_steps(job_id, steps_state)
            
            try:
                safe_name = sanitize_key_part(step_cfg['name'])

                if step_cfg.get('script_before'):
                    state = exec_before(step_cfg['script_before'], state)

                prompt = render_context(step_cfg.get('context', ''), state)
                output = llm.chat(OLLAMA_MODEL, prompt, host=OLLAMA_HOST)

                if step_cfg.get('script_after'):
                    state = exec_after(step_cfg['script_after'], state, output)
                else:
                    state[f'output_{safe_name}'] = output

                step_duration = round(time.time() - step_start, 2)
                steps_state[i]['status'] = 'done'
                steps_state[i]['duration'] = step_duration
                steps_state[i]['message'] = 'Success'
                db.update_run_steps(job_id, steps_state)

            except Exception as e:
                step_duration = round(time.time() - step_start, 2)
                steps_state[i]['status'] = 'failed'
                steps_state[i]['duration'] = step_duration
                steps_state[i]['message'] = f"Failed: {str(e)}"
                db.update_run_steps(job_id, steps_state)
                raise

        total_duration = round(time.time() - start_time, 2)
        db.finish_run(job_id, 'done', steps_state, state)
        db.bump_stats(run['content_code'], 'done', run['started'])

        try:
            content_code = run['content_code']
            summary_payload = {
                "status": "success",
                "creationDate": run['started'],
                "durationSeconds": total_duration,
                "steps": [{"name": s["name"], "status": s["status"], "duration": s["duration"], "message": s["message"]} for s in steps_state]
            }
            nodify.save_data(content_code, f"run_summary_{job_id[:8]}", summary_payload, name=f"Run {job_id[:8]} Summary", data_type="json")
            db.upsert_data_entries(content_code, nodify.get_datas_by_content(content_code))
        except Exception as err:
            print(f"[nodify] Error saving summary: {err}")

    except Exception as e:
        total_duration = round(time.time() - start_time, 2)
        error_msg = "Connection refused: unable to connect to Ollama service"
        db.finish_run(job_id, 'failed', steps_state, None, error=error_msg, traceback_=traceback.format_exc())
        db.bump_stats(run['content_code'], 'failed', run['started'])

        try:
            content_code = run['content_code']
            summary_payload = {
                "status": "failed",
                "creationDate": run['started'],
                "durationSeconds": total_duration,
                "error": error_msg,
                "steps": [{"name": s["name"], "status": s["status"], "duration": s["duration"], "message": s["message"]} for s in steps_state]
            }
            nodify.save_data(content_code, f"run_summary_{job_id[:8]}", summary_payload, name=f"Run {job_id[:8]} Summary", data_type="json")
            db.upsert_data_entries(content_code, nodify.get_datas_by_content(content_code))
        except Exception as err:
            print(f"[nodify] Error saving error summary: {err}")


def start_job(content_code, cfg):
    job_id = str(uuid.uuid4())
    initial_state = dict(cfg['config'].get('initial_state') or {})
    db.create_run(job_id, content_code, cfg['description'],
                  cfg['config'], initial_state)
    if app.config.get('SYNC_RUN'):
        run_pipeline(job_id)
    else:
        threading.Thread(target=run_pipeline, args=(job_id,), daemon=True).start()
    return job_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html', model=OLLAMA_MODEL,
                           sync_interval=SYNC_INTERVAL)


@app.route('/api/configs', methods=['GET'])
def api_list_configs():
    return jsonify({'configs': db.list_configs()})


@app.route('/api/configs', methods=['POST'])
def api_create_config():
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip()
    if not code:
        return jsonify({'error': 'code is required'}), 400
    if not sync_one(code):
        cfg = db.get_config(code)
        err = cfg.get('last_sync_error') if cfg else 'sync failed'
        return jsonify({'error': err}), 400
    return jsonify(db.get_config(code)), 201


@app.route('/api/configs/<content_code>', methods=['GET'])
def api_get_config(content_code):
    cfg = db.get_config(content_code)
    if not cfg:
        return jsonify({'error': 'not found'}), 404
    return jsonify(cfg)


@app.route('/api/configs/<content_code>', methods=['DELETE'])
def api_delete_config(content_code):
    db.delete_config(content_code)
    return jsonify({'status': 'ok'})


@app.route('/api/configs/<content_code>/sync', methods=['POST'])
def api_sync_config(content_code):
    if not sync_one(content_code):
        cfg = db.get_config(content_code)
        err = cfg.get('last_sync_error') if cfg else 'sync failed'
        return jsonify({'error': err}), 400
    return jsonify(db.get_config(content_code))


@app.route('/api/configs/<content_code>/run', methods=['POST'])
def api_run_config(content_code):
    cfg = db.get_config(content_code)
    if not cfg:
        return jsonify({'error': 'config not found; sync it first'}), 404
    job_id = start_job(content_code, cfg)
    return jsonify({'job_id': job_id}), 202


@app.route('/api/configs/<content_code>/runs', methods=['GET'])
def api_config_runs(content_code):
    return jsonify({'runs': db.list_runs(content_code)})


@app.route('/api/sync-all', methods=['POST'])
def api_sync_all():
    ok, err = sync_all()
    return jsonify({'ok': ok, 'errors': err})


@app.route('/api/jobs/<job_id>', methods=['GET'])
def api_get_job(job_id):
    run = db.get_run(job_id)
    if not run:
        return jsonify({'error': 'not found'}), 404
    return jsonify(run)


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
def api_delete_job(job_id):
    db.delete_run(job_id)
    return jsonify({'status': 'ok'})


@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({'status': 'ok', 'model': OLLAMA_MODEL})


@app.route('/api/configs/<content_code>/datas', methods=['GET'])
def api_get_config_datas(content_code):
    datas = db.get_datas_for_content(content_code)
    return jsonify({'datas': datas}), 200
    
# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5000'))
    threading.Thread(target=lambda: (print("[sync] startup sync starting"),
                                     print(f"[sync] startup done: {sync_all()}")),
                     daemon=True).start()
    threading.Thread(target=background_sync_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=port)