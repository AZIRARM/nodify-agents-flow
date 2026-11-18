"""Execute Python scripts in a controlled namespace.

NOTE: This runs arbitrary Python code. It is safe ONLY because the scripts
come from your own Nodify nodes. Do not use this with untrusted input.
"""
import traceback
import json
import re
import datetime
import llm


def exec_before(code: str, state: dict, model: str = '', host: str = '') -> dict:
    """Run a `before` script. Signature: run(state) -> state."""
    if not code or not code.strip():
        return state
    
    ns = {
        '__builtins__': __builtins__,
        'json': json,
        're': re,
        'datetime': datetime,
        'llm': llm,
        'model': model,
        'host': host
    }
    try:
        exec(code, ns, ns)  # Double ns permet aux imports de fonctionner
        if 'run' not in ns:
            raise ValueError("script_before must define a `run(state)` function")
        return ns['run'](state)
    except Exception as e:
        print(f"[sandbox] before error: {e}\n{traceback.format_exc()}", flush=True)
        raise


def exec_after(code: str, state: dict, output: str, model: str = '', host: str = '') -> dict:
    """Run an `after` script. Signature: run(state, output) -> state."""
    if not code or not code.strip():
        return state
        
    ns = {
        '__builtins__': __builtins__,
        'json': json,
        're': re,
        'datetime': datetime,
        'llm': llm,
        'model': model,
        'host': host
    }
    try:
        exec(code, ns, ns)
        if 'run' not in ns:
            raise ValueError("script_after must define a `run(state, output)` function")
        return ns['run'](state, output)
    except Exception as e:
        print(f"[sandbox] after error: {e}\n{traceback.format_exc()}", flush=True)
        raise