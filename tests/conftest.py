"""Shared pytest fixtures for the runner test suite."""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


@pytest.fixture
def fake_nodify(monkeypatch):
    """In-memory stand-in for NodifyClient with a tiny content/datas store."""
    import runner
    contents = {}
    datas_store = {}

    class FakeNodify:
        def get_content_by_code(self, code):
            return contents.get(code)

        def save_data(self, content_node_code, key, value, name='', data_type='json'):
            if content_node_code not in datas_store:
                datas_store[content_node_code] = {}
            # Mimic the real API: value is serialized to a JSON string.
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            datas_store[content_node_code][key] = {
                'id': f'fake-data-{key}',           # unique per key
                'contentNodeCode': content_node_code,
                'key': key,
                'value': value,
                'name': name,
                'dataType': data_type,
            }
            return True

        def get_datas_by_content(self, content_code):
            return list(datas_store.get(content_code, {}).values())

        def delete_data(self, content_code, key):
            if content_code in datas_store and key in datas_store[content_code]:
                del datas_store[content_code][key]
                return True
            return False

    fake = FakeNodify()
    monkeypatch.setattr(runner, 'nodify', fake)

    def set_content(code, payload, description='', parent='NODE-1'):
        contents[code] = {
            'payload': payload,
            'parentCode': parent,
            'description': description or code,
        }

    fake.set_content = set_content
    fake.datas_store = datas_store   # exposed for assertions in tests
    return fake


@pytest.fixture
def fake_llm(monkeypatch):
    """Deterministic llm.chat replacement."""
    import runner
    calls = []
    responses = {'default': 'ok'}

    def fake_chat(model, prompt, host='', timeout=120):
        calls.append({'model': model, 'prompt': prompt})
        for key, resp in responses.items():
            if key != 'default' and key in prompt:
                return resp
        return responses['default']

    monkeypatch.setattr(runner.llm, 'chat', fake_chat)
    return {'calls': calls, 'responses': responses}


@pytest.fixture
def client(tmp_path, monkeypatch, fake_nodify, fake_llm):
    """Flask test client backed by a fresh SQLite file per test.

    SYNC_RUN=True makes runs synchronous so assertions don't race with
    background threads.
    """
    import db
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    import runner
    runner.app.config['TESTING'] = True
    runner.app.config['SYNC_RUN'] = True
    # Clear the in-memory job registry between tests.
    runner._jobs.clear()
    return runner.app.test_client()