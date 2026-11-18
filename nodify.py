"""Minimal Nodify API client used by the runner."""
import json
import uuid
import urllib.request
from typing import Any, Optional


class NodifyClient:
    """Thin wrapper around the Nodify REST API."""

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip('/')

    # ------------------------------------------------------------------
    # Low-level request
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 timeout: int = 30, strict: bool = False) -> Any:
        url = f"{self.api_url}{path}"
        data = None
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8')
                print(f"[nodify] {method} {url} -> status {resp.status}, body length: {len(raw)}")
                if not raw or raw == 'null':
                    return None
                return json.loads(raw)
        except Exception as e:
            print(f"[nodify] {method} {url} ERROR: {type(e).__name__}: {e}")
            if strict:
                raise
            return None

    # ------------------------------------------------------------------
    # Contents
    # ------------------------------------------------------------------
    def get_content_by_code(self, content_code: str) -> Optional[dict]:
        """Fetch a single content by its content code (full object)."""
        return self._request('GET', f"/contents/code/{content_code}", strict=True)

    # ------------------------------------------------------------------
    # Datas
    # ------------------------------------------------------------------
    def save_data(self, content_node_code: str, key: str, value: Any,
                  name: str = '', data_type: str = 'json') -> bool:
        """Save a Data entry attached to a content node. Best-effort."""
        if isinstance(value, (dict, list)):
            formatted_value = json.dumps(value, ensure_ascii=False)
        else:
            formatted_value = str(value)

        body = {
            'id': str(uuid.uuid4()),
            'contentNodeCode': content_node_code,
            'key': key,
            'value': formatted_value,
            'name': name or key,
            'dataType': data_type,
        }
        return self._request('POST', '/datas/', body) is not None

    def get_datas_by_content(self, content_code: str) -> list:
        """List all Data entries attached to a content node.

        Raises RuntimeError if the HTTP call fails or returns an
        unexpected structure (so the sync can report it properly).
        """
        path = f"/datas/contentCode/{content_code}"
        res = self._request('GET', path, strict=True)

        if res is None:
            return []

        if isinstance(res, list):
            return res

        if isinstance(res, dict):
            # 1) clé directe
            for key in ('datas', 'data', 'items', 'content', 'results', 'values'):
                if isinstance(res.get(key), list):
                    return res[key]
            # 2) clé imbriquée (ex: {data:{items:[]}})
            for key in ('data', 'content', '_embedded'):
                sub = res.get(key)
                if isinstance(sub, dict):
                    for sub_key in ('datas', 'data', 'items', 'results', 'values'):
                        if isinstance(sub.get(sub_key), list):
                            return sub[sub_key]
            raise RuntimeError(f"Unexpected Nodify response keys: {list(res.keys())}")

        raise RuntimeError(f"Unexpected Nodify response type: {type(res)}")

    def delete_data(self, content_code: str, key: str) -> bool:
        """Delete a Data entry by content code + key."""
        return self._request(
            'DELETE',
            f"/datas/contents/{content_code}/key/{key}",
        ) is not None