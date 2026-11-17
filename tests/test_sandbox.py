import pytest
from sandbox import exec_before, exec_after


def test_exec_before_modifies_state():
    code = "def run(state):\n    state['x'] = state.get('x', 0) + 1\n    return state"
    assert exec_before(code, {'x': 1}) == {'x': 2}


def test_exec_before_empty_returns_state_unchanged():
    state = {'x': 1}
    assert exec_before('', state) == state
    assert exec_before('   ', state) == state


def test_exec_after_uses_output():
    code = "def run(state, output):\n    state['out'] = output.upper()\n    return state"
    assert exec_after(code, {}, 'hi') == {'out': 'HI'}


def test_exec_after_empty_returns_state_unchanged():
    state = {'x': 1}
    assert exec_after('', state, 'hi') == state


def test_exec_before_missing_run_raises():
    with pytest.raises(ValueError, match="run\\(state\\)"):
        exec_before("x = 1", {})


def test_exec_after_missing_run_raises():
    with pytest.raises(ValueError, match="run\\(state, output\\)"):
        exec_after("x = 1", {}, 'hi')