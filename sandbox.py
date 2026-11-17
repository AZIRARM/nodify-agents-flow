"""Execute Python scripts in a controlled namespace.

NOTE: This runs arbitrary Python code. It is safe ONLY because the scripts
come from your own Nodify nodes. Do not use this with untrusted input.
"""
import traceback
from typing import Any, Optional


def exec_before(code: str, state: dict) -> dict:
    """Run a `before` script. Signature: run(state) -> state."""
    if not code or not code.strip():
        return state
    ns = {}
    try:
        exec(code, {'__builtins__': __builtins__}, ns)
        if 'run' not in ns:
            raise ValueError("script_before must define a `run(state)` function")
        return ns['run'](state)
    except Exception as e:
        print(f"[sandbox] before error: {e}\n{traceback.format_exc()}")
        raise


def exec_after(code: str, state: dict, output: str) -> dict:
    """Run an `after` script. Signature: run(state, output) -> state."""
    if not code or not code.strip():
        return state
    ns = {}
    try:
        exec(code, {'__builtins__': __builtins__}, ns)
        if 'run' not in ns:
            raise ValueError("script_after must define a `run(state, output)` function")
        return ns['run'](state, output)
    except Exception as e:
        print(f"[sandbox] after error: {e}\n{traceback.format_exc()}")
        raise