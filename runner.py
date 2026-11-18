"""
Nodify Agents Flow — sequential pipeline runner.

The legacy `runs` table has been removed: run history lives only in
`datas` as `run_summary_*` entries, published to Nodify and fetched
back on sync. Datas are the single source of truth.

Environment:
    NODIFY_API_URL           Nodify API base URL (required)
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
from datetime import datetime, timedelta, timezone

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

# In-memory job registry (lost on restart by design — datas are the
# single source of truth for persisted run history).
_jobs = {}
_jobs_lock = threading.Lock()


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
    config = payload if isinstance(payload, dict) else json.loads(payload)
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
# Run-summary helpers (datas ⇄ runs)
# ---------------------------------------------------------------------------
def _parse_summary_value(raw):
    """A run_summary `value` may be a dict or a JSON string; parse defensively."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _normalize_run_summary(data):
    """Convert a `run_summary_*` datas row into the run shape the UI expects."""
    summary = _parse_summary_value(data.get('value'))
    started = summary.get('creationDate')
    duration = summary.get('durationSeconds') or 0
    finished = None
    if started and duration:
        try:
            dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
            finished = (dt + timedelta(seconds=float(duration))).isoformat()
        except Exception:
            finished = None

    status = summary.get('status') or 'unknown'
    ui_status = 'done' if status == 'success' else status

    return {
        'job_id': data.get('key') or data.get('id'),
        'content_code': data.get('content_code'),
        'description': data.get('name') or data.get('key'),
        'status': ui_status,
        'started': started,
        'finished': finished,
        'duration': duration,
        'error': summary.get('error'),
        'steps': summary.get('steps') or [],
    }


def _compute_run_stats(content_code):
    """Derive ✓/✗ counts and last-run info from persisted run_summary datas."""
    datas = db.get_datas_for_content(content_code)
    success = failure = 0
    last_run_at = None
    last_run_status = None

    for d in datas:
        if not (d.get('key') or '').startswith('run_summary_'):
            continue
        summary = _parse_summary_value(d.get('value'))
        status = summary.get('status')
        if status == 'success':
            success += 1
        elif status == 'failed':
            failure += 1
        started = summary.get('creationDate')
        if started and (last_run_at is None or started > last_run_at):
            last_run_at = started
            last_run_status = 'done' if status == 'success' else status

    return {
        'success_count': success,
        'failure_count': failure,
        'last_run_at': last_run_at,
        'last_run_status': last_run_status,
    }


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
def sync_one(content_code):
    """Re-fetch a content from Nodify and update the cached config & datas."""
    try:
        content = nodify.get_content_by_code(content_code)
        if not content:
            db.set_sync_error(content_code, "content not found")
            return False

        config = parse_pipeline_config(content.get('payload'))
        description = (
            (content.get('description') or '').strip()
            or config.get('name') or content_code
        )
        name = config.get('name') or description
        db.upsert_config(content_code, description, name, config)

        try:
            datas = nodify.get_datas_by_content(content_code)
        except Exception as e:
            db.set_sync_error(content_code, f"get datas failed: {e}")
            return False

        db.upsert_data_entries(content_code, datas)
        db.set_sync_error(content_code, None)
        print(f"[sync] {content_code}: OK, {len(datas)} datas")
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
            print("[sync] background sync starting")
            ok, err = sync_all()
            print(f"[sync] done: {ok} ok, {err} errors")
        except Exception as e:
            print(f"[sync] error: {e}")


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------
def run_pipeline(job_id):
    """Execute a pipeline and publish a summary to Nodify datas."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return

    config = job['config']
    steps_cfg = config['steps']
    state = dict(job['initial_state'] or {})
    steps_state = job['steps']
    start_time = time.time()

    try:
        for i, step_cfg in enumerate(steps_cfg):
            step_start = time.time()
            steps_state[i]['status'] = 'running'
            steps_state[i]['message'] = 'Running step'

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

                steps_state[i]['status'] = 'done'
                steps_state[i]['duration'] = round(time.time() - step_start, 2)
                steps_state[i]['message'] = 'Success'
            except Exception as e:
                steps_state[i]['status'] = 'failed'
                steps_state[i]['duration'] = round(time.time() - step_start, 2)
                steps_state[i]['message'] = f"Failed: {str(e)}"
                raise

        total = round(time.time() - start_time, 2)
        with _jobs_lock:
            job['status'] = 'done'
            job['finished'] = now_iso()
            job['result'] = state

        _publish_summary(job, steps_state, total, status='success')

    except Exception:
        total = round(time.time() - start_time, 2)
        error_msg = "Connection refused: unable to connect to Ollama service"
        with _jobs_lock:
            job['status'] = 'failed'
            job['finished'] = now_iso()
            job['error'] = error_msg

        _publish_summary(job, steps_state, total, status='failed', error=error_msg)
        print(f"[run] {job_id}: failed — {traceback.format_exc()}")


def _publish_summary(job, steps_state, duration, status, error=None):
    """Write the run summary to Nodify, then re-sync it into local datas."""
    summary = {
        "status": status,
        "creationDate": job['started'],
        "durationSeconds": duration,
        "steps": [
            {"name": s["name"], "status": s["status"],
             "duration": s["duration"], "message": s["message"]}
            for s in steps_state
        ],
    }
    if error:
        summary["error"] = error

    key = f"run_summary_{job['job_id'][:8]}"
    try:
        ok = nodify.save_data(job['content_code'], key, summary,
                              name=f"Run {job['job_id'][:8]} Summary",
                              data_type="json")
        if ok:
            sync_one(job['content_code'])
    except Exception as err:
        print(f"[nodify] Error saving summary: {err}")


def start_job(content_code, cfg):
    job_id = str(uuid.uuid4())
    initial_state = dict(cfg['config'].get('initial_state') or {})
    with _jobs_lock:
        _jobs[job_id] = {
            'job_id': job_id,
            'content_code': content_code,
            'description': cfg['description'],
            'status': 'running',
            'started': now_iso(),
            'finished': None,
            'error': None,
            'config': cfg['config'],
            'initial_state': initial_state,
            'steps': [
                {'name': s['name'], 'status': 'pending',
                 'duration': 0, 'message': 'Pending'}
                for s in cfg['config']['steps']
            ],
            'result': None,
        }
    # Honor SYNC_RUN (used by tests and by single-worker deployments
    # where async runs would race with the request teardown).
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
    configs = db.list_configs()
    for c in configs:
        c.update(_compute_run_stats(c['content_code']))
        c['datas_count'] = db.count_datas(c['content_code'])
    configs.sort(key=lambda c: c.get('last_run_at') or c.get('last_synced') or '',
                 reverse=True)
    return jsonify({'configs': configs})


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
    cfg = db.get_config(code)
    cfg.update(_compute_run_stats(code))
    return jsonify(cfg), 201


@app.route('/api/configs/<content_code>', methods=['GET'])
def api_get_config(content_code):
    cfg = db.get_config(content_code)
    if not cfg:
        return jsonify({'error': 'not found'}), 404
    cfg.update(_compute_run_stats(content_code))
    cfg['datas'] = db.get_datas_for_content(content_code)
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
    cfg = db.get_config(content_code)
    cfg.update(_compute_run_stats(content_code))
    return jsonify(cfg)


@app.route('/api/configs/<content_code>/run', methods=['POST'])
def api_run_config(content_code):
    cfg = db.get_config(content_code)
    if not cfg:
        return jsonify({'error': 'config not found; sync it first'}), 404
    job_id = start_job(content_code, cfg)
    return jsonify({'job_id': job_id}), 202


@app.route('/api/configs/<content_code>/datas', methods=['GET'])
def api_get_config_datas(content_code):
    datas = db.get_datas_for_content(content_code)
    return jsonify({'datas': datas}), 200


@app.route('/api/configs/<content_code>/runs', methods=['GET'])
def api_config_runs(content_code):
    """Return run_summary datas, normalized to the shape the UI expects."""
    datas = db.get_datas_for_content(content_code)
    runs = [
        _normalize_run_summary(d)
        for d in datas
        if (d.get('key') or '').startswith('run_summary_')
    ]
    return jsonify({'runs': runs})


@app.route('/api/sync-all', methods=['POST'])
def api_sync_all():
    ok, err = sync_all()
    return jsonify({'ok': ok, 'errors': err})


@app.route('/api/jobs/<job_id>', methods=['GET'])
def api_get_job(job_id):
    """Return an in-memory job if running, else fall back to a persisted
    run_summary found in datas."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job:
        return jsonify(job)

    data = db.find_data_by_key(job_id)
    if not data:
        return jsonify({'error': 'not found'}), 404
    return jsonify(_normalize_run_summary(data))


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
def api_delete_job(job_id):
    with _jobs_lock:
        _jobs.pop(job_id, None)
    return jsonify({'status': 'ok'})


@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({'status': 'ok', 'model': OLLAMA_MODEL})


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5000'))
    print(f"[startup] DB_PATH = {os.path.abspath(db.DB_PATH)}")
    print(f"[startup] NODIFY_API_URL = {NODIFY_API_URL}")

    def _startup_sync():
        print("[sync] startup sync starting")
        print(f"[sync] startup done: {sync_all()}")

    threading.Thread(target=_startup_sync, daemon=True).start()
    threading.Thread(target=background_sync_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=port)