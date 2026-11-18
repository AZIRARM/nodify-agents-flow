"""SQLite storage for configs and datas."""
import json
import os
import sqlite3
import threading
import uuid
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
            CREATE INDEX IF NOT EXISTS idx_datas_content ON datas(content_code);
            CREATE INDEX IF NOT EXISTS idx_datas_key ON datas(content_code, key);
        ''')
        conn.execute('DROP TABLE IF EXISTS runs')

def _config_row(row):
    if not row:
        return None
    d = dict(row)
    d['config'] = json.loads(d['config_json']) if d['config_json'] else None
    del d['config_json']
    return d

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
        ''', (content_code, description, name, json.dumps(config, ensure_ascii=False), _now()))

def set_sync_error(content_code, error):
    with _lock, _connect() as conn:
        conn.execute('UPDATE configs SET last_sync_error = ?, last_synced = ? WHERE content_code = ?',
                     (error, _now(), content_code))

def get_config(content_code):
    with _lock, _connect() as conn:
        row = conn.execute('SELECT * FROM configs WHERE content_code = ?', (content_code,)).fetchone()
        return _config_row(row)

def list_configs():
    with _lock, _connect() as conn:
        rows = conn.execute('SELECT * FROM configs ORDER BY COALESCE(last_run_at, last_synced) DESC').fetchall()
        return [_config_row(r) for r in rows]

def list_config_codes():
    with _lock, _connect() as conn:
        return [r['content_code'] for r in conn.execute('SELECT content_code FROM configs').fetchall()]

def delete_config(content_code):
    with _lock, _connect() as conn:
        conn.execute('DELETE FROM datas WHERE content_code = ?', (content_code,))
        conn.execute('DELETE FROM configs WHERE content_code = ?', (content_code,))

def upsert_data_entries(content_code, datas) -> int:
    if datas is None or not isinstance(datas, list) or not datas:
        return 0
    inserted = 0
    with _lock, _connect() as conn:
        conn.execute('DELETE FROM datas WHERE content_code = ?', (content_code,))
        seen_ids = set()
        for d in datas:
            if not isinstance(d, dict):
                continue
            val = d.get('value')
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            data_id = d.get('id') or str(uuid.uuid4())
            while data_id in seen_ids:
                data_id = str(uuid.uuid4())
            seen_ids.add(data_id)
            key = d.get('key') or d.get('name') or data_id
            conn.execute('''
                INSERT INTO datas (id, content_code, key, value, name, data_type, creationDate, modificationDate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (data_id, content_code, key, val, d.get('name'), d.get('dataType') or d.get('data_type'),
                  d.get('creationDate'), d.get('modificationDate')))
            inserted += 1
    print(f"[db] upsert_data_entries: {inserted} rows for {content_code}", flush=True)
    return inserted

def get_datas_for_content(content_code):
    with _lock, _connect() as conn:
        rows = conn.execute('''
            SELECT id, content_code, key, value, name, data_type, creationDate, modificationDate
            FROM datas WHERE content_code = ? ORDER BY COALESCE(modificationDate, creationDate, 0) DESC
        ''', (content_code,)).fetchall()
        return [dict(row) for row in rows]

def find_data_by_key(key):
    with _lock, _connect() as conn:
        row = conn.execute('SELECT * FROM datas WHERE key = ? LIMIT 1', (key,)).fetchone()
        return dict(row) if row else None

def count_datas(content_code) -> int:
    with _lock, _connect() as conn:
        row = conn.execute('SELECT COUNT(*) AS c FROM datas WHERE content_code = ?', (content_code,)).fetchone()
        return row['c'] if row else 0