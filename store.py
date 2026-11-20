"""Application store: content-code registry + in-memory cache.

Nodify is the single source of truth. This module keeps:
- a tiny JSON file (data/registry.json) with the list of registered content codes
- an in-memory cache of configs and datas, used when Nodify is unreachable

No SQLite, no `runs` table.
"""
import json
import os
import threading
from datetime import datetime, timezone

REGISTRY_PATH = os.environ.get('REGISTRY_PATH', 'data/registry.json')

_lock = threading.Lock()
_registry: list[str] = []
_configs: dict[str, dict] = {}
_datas: dict[str, list[dict]] = {}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load_registry() -> list[str]:
    try:
        with open(REGISTRY_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if isinstance(data, list):
        return [str(c) for c in data]
    if isinstance(data, dict):
        return [str(c) for c in data.get('codes', [])]
    return []


def _save_registry():
    d = os.path.dirname(REGISTRY_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(REGISTRY_PATH, 'w', encoding='utf-8') as f:
        json.dump(_registry, f, ensure_ascii=False, indent=2)


def init():
    global _registry
    _registry = _load_registry()


# ---- registry ----

def list_config_codes() -> list[str]:
    with _lock:
        return list(_registry)


def is_registered(content_code: str) -> bool:
    with _lock:
        return content_code in _registry


# ---- configs cache ----

def upsert_config(content_code: str, description: str, name: str, config: dict):
    with _lock:
        if content_code not in _registry:
            _registry.append(content_code)
            _save_registry()
        _configs[content_code] = {
            'content_code': content_code,
            'description': description,
            'name': name,
            'config': config,
            'last_synced': _now(),
            'last_sync_error': None,
        }


def set_sync_error(content_code: str, error: str | None):
    with _lock:
        rec = _configs.get(content_code)
        if rec is None:
            _configs[content_code] = {
                'content_code': content_code,
                'description': content_code,
                'name': content_code,
                'config': None,
                'last_synced': None,
                'last_sync_error': error,
            }
        else:
            rec['last_sync_error'] = error


def get_config(content_code: str):
    with _lock:
        rec = _configs.get(content_code)
        return dict(rec) if rec else None


def list_configs():
    with _lock:
        return [dict(c) for c in _configs.values()
                if c['content_code'] in _registry]


def delete_config(content_code: str):
    with _lock:
        if content_code in _registry:
            _registry.remove(content_code)
            _save_registry()
        _configs.pop(content_code, None)
        _datas.pop(content_code, None)


# ---- datas cache ----

def upsert_data_entries(content_code: str, datas) -> int:
    if not isinstance(datas, list):
        return 0
    with _lock:
        _datas[content_code] = [
            {**d, 'content_code': content_code}
            for d in datas if isinstance(d, dict)
        ]
        return len(_datas[content_code])


def get_datas_for_content(content_code: str) -> list[dict]:
    with _lock:
        return [dict(d) for d in _datas.get(content_code, [])]


def find_data_by_key(key: str):
    with _lock:
        for datas in _datas.values():
            for d in datas:
                if d.get('key') == key:
                    return dict(d)
        return None


def count_datas(content_code: str) -> int:
    with _lock:
        return len(_datas.get(content_code, []))