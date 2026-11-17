"""SQLite storage for configs and runs."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.environ.get('DB_PATH', 'data/agentflow.db')
_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init():
    with _lock, _connect() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS configs (
                content_code TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                name TEXT,
                config_json TEXT NOT NULL,
                last_synced TEXT,
                last_sync_error TEXT,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_run_at TEXT,
                last_run_status TEXT
            );
            CREATE TABLE IF NOT EXISTS runs (
                job_id TEXT PRIMARY KEY,
                content_code TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                started TEXT NOT NULL,
                finished TEXT,
                error TEXT,
                traceback TEXT,
                config_json TEXT,
                steps_json TEXT,
                result_json TEXT,
                initial_state_json TEXT
            );
            CREATE TABLE IF NOT EXISTS datas (
                id TEXT PRIMARY KEY,
                content_code TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                name TEXT,
                data_type TEXT,
                creationDate INTEGER,
                modificationDate INTEGER,
                FOREIGN KEY(content_code) REFERENCES configs(content_code) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_runs_content ON runs(content_code);
            CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started DESC);
            CREATE INDEX IF NOT EXISTS idx_datas_content ON datas(content_code);
        ''')


def _config_row(row):
    if not row:
        return None
    d = dict(row)
    d['config'] = json.loads(d['config_json']) if d['config_json'] else None
    del d['config_json']
    return d


def _run_row(row):
    if not row:
        return None
    d = dict(row)
    for k in ('config_json', 'steps_json', 'result_json', 'initial_state_json'):
        try:
            d[k.replace('_json', '')] = json.loads(d[k]) if d.get(k) else None
        except json.JSONDecodeError:
            d[k.replace('_json', '')] = None
        del d[k]
    return d


# --- Configs ---

def upsert_config(content_code, description, name, config):
    with _lock, _connect() as conn:
        conn.execute('''
            INSERT INTO configs (content_code, description, name, config_json, last_synced, last_sync_error)
            VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT(content_code) DO UPDATE SET
                description = excluded.description,
                name = excluded.name,
                config_json = excluded.config_json,
                last_synced = excluded.last_synced,
                last_sync_error = NULL
        ''', (content_code, description, name,
              json.dumps(config, ensure_ascii=False), _now()))


def set_sync_error(content_code, error):
    with _lock, _connect() as conn:
        conn.execute(
            'UPDATE configs SET last_sync_error = ?, last_synced = ? WHERE content_code = ?',
            (error, _now(), content_code),
        )


def get_config(content_code):
    with _lock, _connect() as conn:
        row = conn.execute('SELECT * FROM configs WHERE content_code = ?',
                           (content_code,)).fetchone()
        return _config_row(row)


def list_configs():
    with _lock, _connect() as conn:
        rows = conn.execute('''
            SELECT * FROM configs
            ORDER BY COALESCE(last_run_at, last_synced) DESC
        ''').fetchall()
        return [_config_row(r) for r in rows]


def list_config_codes():
    with _lock, _connect() as conn:
        return [r['content_code']
                for r in conn.execute('SELECT content_code FROM configs').fetchall()]


def delete_config(content_code):
    with _lock, _connect() as conn:
        conn.execute('DELETE FROM runs WHERE content_code = ?', (content_code,))
        conn.execute('DELETE FROM datas WHERE content_code = ?', (content_code,))
        conn.execute('DELETE FROM configs WHERE content_code = ?', (content_code,))


def bump_stats(content_code, status, when):
    with _lock, _connect() as conn:
        if status == 'done':
            conn.execute('''
                UPDATE configs SET success_count = success_count + 1,
                                   last_run_at = ?, last_run_status = ?
                WHERE content_code = ?
            ''', (when, status, content_code))
        elif status == 'failed':
            conn.execute('''
                UPDATE configs SET failure_count = failure_count + 1,
                                   last_run_at = ?, last_run_status = ?
                WHERE content_code = ?
            ''', (when, status, content_code))


# --- Datas ---

def upsert_data_entries(content_code, datas):
    if not datas:
        return
    with _lock, _connect() as conn:
        conn.execute('DELETE FROM datas WHERE content_code = ?', (content_code,))
        for d in datas:
            if not isinstance(d, dict):
                continue
            val = d.get('value')
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            
            conn.execute('''
                INSERT INTO datas (id, content_code, key, value, name, data_type, creationDate, modificationDate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                d.get('id'),
                content_code,
                d.get('key'),
                val,
                d.get('name'),
                d.get('dataType'),
                d.get('creationDate'),
                d.get('modificationDate'),
            ))


def list_datas(content_code):
    with _lock, _connect() as conn:
        rows = conn.execute('SELECT * FROM datas WHERE content_code = ?', (content_code,)).fetchall()
        return [dict(r) for r in rows]


# --- Runs ---

def create_run(job_id, content_code, description, config, initial_state):
    with _lock, _connect() as conn:
        conn.execute('''
            INSERT INTO runs (job_id, content_code, description, status, started,
                              config_json, initial_state_json)
            VALUES (?, ?, ?, 'running', ?, ?, ?)
        ''', (
            job_id, content_code, description, _now(),
            json.dumps(config, ensure_ascii=False),
            json.dumps(initial_state, ensure_ascii=False),
        ))


def update_run_steps(job_id, steps):
    with _lock, _connect() as conn:
        conn.execute(
            'UPDATE runs SET steps_json = ? WHERE job_id = ?',
            (json.dumps(steps, ensure_ascii=False), job_id),
        )


def finish_run(job_id, status, steps, result, error=None, traceback_=None):
    with _lock, _connect() as conn:
        conn.execute('''
            UPDATE runs SET status = ?, finished = ?,
                            steps_json = ?, result_json = ?,
                            error = ?, traceback = ?
            WHERE job_id = ?
        ''', (
            status, _now(),
            json.dumps(steps, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False) if result is not None else None,
            error, traceback_,
            job_id,
        ))


def get_run(job_id):
    with _lock, _connect() as conn:
        row = conn.execute('SELECT * FROM runs WHERE job_id = ?', (job_id,)).fetchone()
        return _run_row(row)


def list_runs(content_code=None, limit=100):
    with _lock, _connect() as conn:
        if content_code:
            rows = conn.execute('''
                SELECT * FROM runs WHERE content_code = ?
                ORDER BY started DESC LIMIT ?
            ''', (content_code, limit)).fetchall()
        else:
            rows = conn.execute('''
                SELECT * FROM runs ORDER BY started DESC LIMIT ?
            ''', (limit,)).fetchall()
        return [_run_row(r) for r in rows]


def delete_run(job_id):
    with _lock, _connect() as conn:
        conn.execute('DELETE FROM runs WHERE job_id = ?', (job_id,))


def recover_abandoned_runs():
    """Mark runs still 'running'/'queued' (from a previous crash) as failed."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT job_id, content_code FROM runs WHERE status IN ('queued', 'running')"
        ).fetchall()
        if not rows:
            return
        conn.execute('''
            UPDATE runs SET status = 'failed', finished = ?,
                            error = 'abandoned (server restart)'
            WHERE status IN ('queued', 'running')
        ''', (_now(),))
        for row in rows:
            conn.execute('''
                UPDATE configs SET failure_count = failure_count + 1,
                                   last_run_at = ?, last_run_status = 'failed'
                WHERE content_code = ?
            ''', (_now(), row['content_code']))

def get_datas_for_content(content_code):
    with _connect() as conn:
        rows = conn.execute('''
            SELECT id, content_code, key, value, name, data_type, creationDate, modificationDate
            FROM datas
            WHERE content_code = ?
        ''', (content_code,)).fetchall()
        
        return [dict(row) for row in rows]