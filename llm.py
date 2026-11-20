"""LLM client with pluggable providers.

Providers:
- ollama   (default, local HTTP API)
- openai   (ChatGPT, or any OpenAI-compatible endpoint via LLM_BASE_URL)
- deepseek (OpenAI-compatible)
- gemini   (Google Generative Language API)

Env:
  LLM_PROVIDER   ollama | openai | deepseek | gemini   (default: ollama)
  LLM_MODEL      model name (default depends on provider)
  LLM_API_KEY    API key (required except for ollama)
  LLM_BASE_URL   base URL for OpenAI-compatible providers (optional)
  OLLAMA_HOST    Ollama host (default: http://localhost:11434)
  OLLAMA_MODEL   legacy fallback for the ollama model
"""
import json
import os
import urllib.error
import urllib.request

PROVIDER = os.environ.get('LLM_PROVIDER', 'ollama').strip().lower()
MODEL = os.environ.get('LLM_MODEL', '').strip() or None
API_KEY = os.environ.get('LLM_API_KEY', '').strip()
BASE_URL = os.environ.get('LLM_BASE_URL', '').strip()
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')

DEFAULT_MODELS = {
    'ollama':   os.environ.get('OLLAMA_MODEL', 'qwen2.5:0.5b'),
    'openai':   'gpt-4o-mini',
    'deepseek': 'deepseek-chat',
    'gemini':   'gemini-1.5-flash',
}

DEFAULT_BASE_URLS = {
    'openai':   'https://api.openai.com/v1',
    'deepseek': 'https://api.deepseek.com/v1',
}

OPENAI_COMPATIBLE = {'openai', 'deepseek'}


def resolve_model(model: str | None = None) -> str:
    return model or MODEL or DEFAULT_MODELS.get(PROVIDER, '')


def _post_json(url: str, body: dict, headers: dict | None = None, timeout: int = 120) -> dict:
    data = json.dumps(body).encode('utf-8')
    h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _chat_ollama(model, prompt, host, timeout):
    url = f"{(host or OLLAMA_HOST).rstrip('/')}/api/generate"
    result = _post_json(url, {'model': model, 'prompt': prompt, 'stream': False}, timeout=timeout)
    return (result.get('response') or '').strip()


def _chat_openai_compatible(model, prompt, timeout):
    base = (BASE_URL or DEFAULT_BASE_URLS.get(PROVIDER, '')).rstrip('/')
    if not base:
        raise ValueError(f"LLM_BASE_URL is required for provider '{PROVIDER}'")
    if not API_KEY:
        raise ValueError(f"LLM_API_KEY is required for provider '{PROVIDER}'")
    result = _post_json(
        f"{base}/chat/completions",
        {'model': model,
         'messages': [{'role': 'user', 'content': prompt}],
         'stream': False},
        headers={'Authorization': f'Bearer {API_KEY}'},
        timeout=timeout,
    )
    choices = result.get('choices') or []
    if not choices:
        raise RuntimeError("no choices in LLM response")
    return (choices[0].get('message', {}).get('content') or '').strip()


def _chat_gemini(model, prompt, timeout):
    if not API_KEY:
        raise ValueError("LLM_API_KEY is required for provider 'gemini'")
    url = (f"https://generativelanguage.googleapis.com/v1beta/"
           f"models/{model}:generateContent?key={API_KEY}")
    result = _post_json(url, {'contents': [{'parts': [{'text': prompt}]}]}, timeout=timeout)
    candidates = result.get('candidates') or []
    if not candidates:
        raise RuntimeError("no candidates in LLM response")
    parts = (candidates[0].get('content') or {}).get('parts') or []
    return ''.join(p.get('text', '') for p in parts).strip()


def chat(model, prompt, host=None, timeout=120):
    """Backward-compatible: chat(model, prompt, host=..., timeout=...).

    `model` may be None to fall back to LLM_MODEL / provider default.
    """
    m = resolve_model(model)
    try:
        if PROVIDER == 'ollama':
            return _chat_ollama(m, prompt, host, timeout)
        if PROVIDER in OPENAI_COMPATIBLE:
            return _chat_openai_compatible(m, prompt, timeout)
        if PROVIDER == 'gemini':
            return _chat_gemini(m, prompt, timeout)
        raise ValueError(f"unknown LLM_PROVIDER: {PROVIDER}")
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:500]
        print(f"[llm] HTTP {e.code}: {body}", flush=True)
        raise
    except Exception as e:
        print(f"[llm] {PROVIDER} error: {e}", flush=True)
        raise