import json
import pytest
import runner
import db


def _payload(config):
    return json.dumps(config)


# --- render_context ---------------------------------------------------------

def test_render_context_replaces_state_values():
    assert runner.render_context("Hello {{state.name}}", {'name': 'Bob'}) == "Hello Bob"


def test_render_context_handles_missing_keys():
    assert runner.render_context("Hello {{state.nope}}", {}) == "Hello "


def test_render_context_serializes_dicts():
    out = runner.render_context("{{state.data}}", {'data': {'a': 1}})
    assert '"a": 1' in out


# --- parse_pipeline_config --------------------------------------------------

def test_parse_pipeline_config_from_json_string():
    payload = _payload({'steps': [{'name': 'hello', 'context': 'Say hi'}]})
    assert runner.parse_pipeline_config(payload)['steps'][0]['name'] == 'hello'


def test_parse_pipeline_config_from_dict():
    payload = {'steps': [{'name': 'hello', 'context': 'Say hi'}]}
    assert runner.parse_pipeline_config(payload) == payload


def test_parse_pipeline_config_empty_raises():
    with pytest.raises(ValueError):
        runner.parse_pipeline_config(None)


def test_parse_pipeline_config_no_steps_raises():
    with pytest.raises(ValueError, match="steps"):
        runner.parse_pipeline_config({'name': 'x'})


# --- API: configs -----------------------------------------------------------

def test_create_config_syncs_and_stores_description(client, fake_nodify):
    fake_nodify.set_content(
        'JSON-DEV-1',
        _payload({'name': 'p1', 'steps': [{'name': 'a', 'context': 'x'}]}),
        description='My Pipeline',
    )
    r = client.post('/api/configs', json={'code': 'JSON-DEV-1'})
    assert r.status_code == 201
    data = r.get_json()
    assert data['description'] == 'My Pipeline'
    assert data['content_code'] == 'JSON-DEV-1'


def test_create_config_unknown_content_returns_400(client):
    r = client.post('/api/configs', json={'code': 'MISSING'})
    assert r.status_code == 400


def test_list_configs(client, fake_nodify):
    fake_nodify.set_content('JSON-DEV-1',
        _payload({'steps': [{'name': 'a', 'context': 'x'}]}), description='P1')
    fake_nodify.set_content('JSON-DEV-2',
        _payload({'steps': [{'name': 'b', 'context': 'y'}]}), description='P2')
    client.post('/api/configs', json={'code': 'JSON-DEV-1'})
    client.post('/api/configs', json={'code': 'JSON-DEV-2'})
    r = client.get('/api/configs')
    assert r.status_code == 200
    codes = [c['content_code'] for c in r.get_json()['configs']]
    assert 'JSON-DEV-1' in codes and 'JSON-DEV-2' in codes


def test_delete_config_removes_runs(client, fake_nodify):
    fake_nodify.set_content('JSON-DEV-1',
        _payload({'steps': [{'name': 'a', 'context': 'x'}]}), description='P1')
    client.post('/api/configs', json={'code': 'JSON-DEV-1'})
    client.post('/api/configs/JSON-DEV-1/run')
    r = client.delete('/api/configs/JSON-DEV-1')
    assert r.status_code == 200
    assert client.get('/api/configs/JSON-DEV-1').status_code == 404


# --- API: run & datas sync --------------------------------------------------

def test_run_config_runs_pipeline(client, fake_nodify):
    fake_nodify.set_content('JSON-DEV-1',
        _payload({'steps': [{'name': 'hello', 'context': 'Say hi'}]}), description='P1')
    client.post('/api/configs', json={'code': 'JSON-DEV-1'})

    r = client.post('/api/configs/JSON-DEV-1/run')
    assert r.status_code == 202
    job_id = r.get_json()['job_id']

    run = client.get(f'/api/jobs/{job_id}').get_json()
    assert run['status'] == 'done'
    assert run['description'] == 'P1'
    assert run['steps'][0]['status'] == 'done'


def test_run_saves_summary_to_datas(client, fake_nodify):
    fake_nodify.set_content('JSON-DEV-DATA',
        _payload({'steps': [{'name': 'step1', 'context': 'test'}]}), description='PData')
    client.post('/api/configs', json={'code': 'JSON-DEV-DATA'})
    client.post('/api/configs/JSON-DEV-DATA/run')

    # Check that datas endpoint returns the run summary
    r = client.get('/api/configs/JSON-DEV-DATA/datas')
    assert r.status_code == 200
    datas = r.get_json()['datas']
    assert len(datas) > 0
    assert any('run_summary_' in d['key'] for d in datas)


def test_run_config_with_before_and_after(client, fake_nodify):
    fake_nodify.set_content('JSON-DEV-2', _payload({
        'initial_state': {'value': 0},
        'steps': [{
            'name': 'process',
            'context': 'Value: {{state.value}}',
            'script_before': "def run(state):\n    state['value'] = 42\n    return state",
            'script_after': "def run(state, output):\n    state['result'] = output\n    return state",
        }],
    }), description='P2')
    client.post('/api/configs', json={'code': 'JSON-DEV-2'})

    job_id = client.post('/api/configs/JSON-DEV-2/run').get_json()['job_id']
    run = client.get(f'/api/jobs/{job_id}').get_json()
    assert run['status'] == 'done'
    assert run['result'] == {'value': 42, 'result': 'ok'}


def test_run_config_increments_counts(client, fake_nodify):
    fake_nodify.set_content('JSON-DEV-3',
        _payload({'steps': [{'name': 'a', 'context': 'x'}]}), description='P3')
    client.post('/api/configs', json={'code': 'JSON-DEV-3'})
    client.post('/api/configs/JSON-DEV-3/run')
    client.post('/api/configs/JSON-DEV-3/run')

    cfg = client.get('/api/configs/JSON-DEV-3').get_json()
    assert cfg['success_count'] == 2
    assert cfg['failure_count'] == 0
    assert cfg['last_run_status'] == 'done'


def test_run_config_failed_saves_error(client, fake_nodify, fake_llm):
    fake_llm_orig = runner.llm.chat
    runner.llm.chat = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("LLM down"))
    try:
        fake_nodify.set_content('JSON-DEV-4',
            _payload({'steps': [{'name': 'boom', 'context': 'x'}]}), description='P4')
        client.post('/api/configs', json={'code': 'JSON-DEV-4'})
        job_id = client.post('/api/configs/JSON-DEV-4/run').get_json()['job_id']
        run = client.get(f'/api/jobs/{job_id}').get_json()
        assert run['status'] == 'failed'
        # Expecting the caught exception string or our custom english message
        assert 'LLM down' in run['error'] or 'Connection refused' in run['error']
        cfg = client.get('/api/configs/JSON-DEV-4').get_json()
        assert cfg['failure_count'] == 1
    finally:
        runner.llm.chat = fake_llm_orig

def test_config_runs_list(client, fake_nodify):
    fake_nodify.set_content('JSON-DEV-5',
        _payload({'steps': [{'name': 'a', 'context': 'x'}]}), description='P5')
    client.post('/api/configs', json={'code': 'JSON-DEV-5'})
    client.post('/api/configs/JSON-DEV-5/run')
    client.post('/api/configs/JSON-DEV-5/run')
    r = client.get('/api/configs/JSON-DEV-5/runs')
    assert r.status_code == 200
    assert len(r.get_json()['runs']) == 2


# --- API: sync --------------------------------------------------------------

def test_sync_all(client, fake_nodify):
    fake_nodify.set_content('JSON-DEV-6',
        _payload({'steps': [{'name': 'a', 'context': 'x'}]}), description='P6')
    client.post('/api/configs', json={'code': 'JSON-DEV-6'})
    r = client.post('/api/sync-all')
    assert r.status_code == 200
    assert r.get_json()['ok'] >= 1


# --- Health -----------------------------------------------------------------

def test_health(client):
    r = client.get('/api/health')
    assert r.status_code == 200
    assert r.get_json()['status'] == 'ok'