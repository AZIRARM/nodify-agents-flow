export async function fetchJson(url, opts) {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
}

const enc = encodeURIComponent;

export const api = {
    listConfigs:  ()           => fetchJson('/api/configs'),
    addConfig:    (code)       => fetchJson('/api/configs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
    }),
    getConfig:    (code)       => fetchJson(`/api/configs/${enc(code)}`),
    deleteConfig: (code)       => fetchJson(`/api/configs/${enc(code)}`, { method: 'DELETE' }),
    syncConfig:   (code)       => fetchJson(`/api/configs/${enc(code)}/sync`, { method: 'POST' }),
    runConfig:    (code)       => fetchJson(`/api/configs/${enc(code)}/run`, { method: 'POST' }),
    listRuns:     (code)       => fetchJson(`/api/configs/${enc(code)}/runs`),
    getJob:       (id)         => fetchJson(`/api/jobs/${enc(id)}`),
    deleteJob:    (id)         => fetchJson(`/api/jobs/${enc(id)}`, { method: 'DELETE' }),
    syncAll:      ()           => fetchJson('/api/sync-all', { method: 'POST' }),
};