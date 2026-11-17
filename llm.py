"""Minimal Ollama client using the HTTP API."""
import json
import urllib.request


def chat(model: str, prompt: str, host: str = 'http://localhost:11434', timeout: int = 120) -> str:
    url = f"{host.rstrip('/')}/api/generate"
    body = {
        'model': model,
        'prompt': prompt,
        'stream': False,
    }
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
            result = json.loads(raw)
            return result.get('response', '').strip()
    except Exception as e:
        print(f"[llm] chat error: {e}")
        raise