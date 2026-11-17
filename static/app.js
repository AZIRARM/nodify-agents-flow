const POLL_MS = 2500;
const $ = (id) => document.getElementById(id);

let currentView = 'home';        // 'home' | 'config' | 'run'
let currentConfigCode = null;
let currentJobId = null;
let pollTimer = null;

// --- helpers ---
async function fetchJson(url, opts) {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
}

function statusBadge(status) {
    return `<span class="status status-${status}">${status}</span>`;
}

function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
}

function relTime(iso) {
    if (!iso) return '';
    const t = new Date(iso).getTime();
    const delta = Math.max(0, Date.now() - t);
    const s = Math.floor(delta / 1000);
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
}

function circleSymbol(step, index) {
    if (step.status === 'done') return '✓';
    if (step.status === 'failed') return '✗';
    if (step.status === 'running') return '◐';
    return String(index + 1);
}

function renderStepFlow(steps) {
    if (!steps || !steps.length) return '';
    const parts = [];
    steps.forEach((step, i) => {
        parts.push(`
            <div class="step-node ${step.status}" title="${escapeHtml(step.name)}">
                <div class="step-circle">${circleSymbol(step, i)}</div>
                <div class="step-label">${escapeHtml(step.name)}</div>
            </div>`);
        if (i < steps.length - 1) {
            const st = step.status === 'done' ? 'done'
                     : step.status === 'running' ? 'running'
                     : step.status === 'failed' ? 'failed' : '';
            parts.push(`<div class="step-arrow ${st}">→</div>`);
        }
    });
    return `<div class="step-flow">${parts.join('')}</div>`;
}

function renderStepCard(step, index) {
    return `
        <div class="step ${step.status}">
            <div class="step-header">
                <strong>${index + 1}. ${escapeHtml(step.name)}</strong>
                ${statusBadge(step.status)}
            </div>
            ${step.prompt ? `<div class="label">Prompt</div><pre>${escapeHtml(step.prompt)}</pre>` : ''}
            ${step.output ? `<div class="label">Output</div><pre>${escapeHtml(step.output)}</pre>` : ''}
            ${step.error  ? `<div class="label">Error</div><pre style="color:var(--error)">${escapeHtml(step.error)}</pre>` : ''}
        </div>`;
}

// --- Views ---
function showView(name) {
    ['home', 'config', 'run'].forEach(v => {
        $('view-' + v).classList.toggle('hidden', v !== name);
    });
    currentView = name;
    resetPolling();
    schedulePoll();
}

async function loadConfigs() {
    try {
        const { configs } = await fetchJson('/api/configs');
        const list = $('configsList');
        if (!configs.length) {
            list.innerHTML = '<p class="empty">No configurations yet.</p>';
            return;
        }
        list.innerHTML = configs.map(c => {
            return `
                <div class="config-card" data-code="${escapeHtml(c.content_code)}">
                    <div class="config-card-main">
                        <div class="config-desc">${escapeHtml(c.description)}</div>
                        <div class="config-code">${escapeHtml(c.content_code)}</div>
                    </div>
                    <div class="config-card-stats">
                        <span class="stat stat-ok">✓ ${c.success_count || 0}</span>
                        <span class="stat stat-ko">✗ ${c.failure_count || 0}</span>
                        <span class="stat stat-last">${relTime(c.last_run_at) || 'never run'}</span>
                    </div>
                    ${c.last_run_status ? statusBadge(c.last_run_status) : ''}
                    ${c.last_sync_error ? '<span class="status status-failed" title="'+escapeHtml(c.last_sync_error)+'">sync!</span>' : ''}
                </div>`;
        }).join('');
        list.querySelectorAll('.config-card').forEach(el => {
            el.addEventListener('click', () => {
                window.location.hash = `#/config/${encodeURIComponent(el.dataset.code)}`;
            });
        });
    } catch (e) {
        console.error(e);
    }
}

async function loadConfigView(code) {
    try {
        const cfg = await fetchJson(`/api/configs/${encodeURIComponent(code)}`);
        $('configTitle').textContent = cfg.description;
        $('configMeta').innerHTML =
            `<code>${escapeHtml(cfg.content_code)}</code>
             · synced ${relTime(cfg.last_synced)}
             · ✓ ${cfg.success_count || 0} / ✗ ${cfg.failure_count || 0}`;
        if (cfg.last_sync_error) {
            $('configMeta').innerHTML +=
                `<br><span style="color:var(--error)">Last sync error: ${escapeHtml(cfg.last_sync_error)}</span>`;
        }
        const { runs } = await fetchJson(`/api/configs/${encodeURIComponent(code)}/runs`);
        const list = $('configRunsList');
        if (!runs.length) {
            list.innerHTML = '<p class="empty">No runs yet.</p>';
            return;
        }
        list.innerHTML = runs.map(r => {
            const steps = r.steps || [];
            const done = steps.filter(s => s.status === 'done').length;
            return `
                <div class="run-card" data-id="${r.job_id}">
                    <div>
                        <div class="run-id">${r.job_id}</div>
                        <div class="run-meta">${relTime(r.started)} · ${done}/${steps.length} steps</div>
                    </div>
                    ${statusBadge(r.status)}
                </div>`;
        }).join('');
        list.querySelectorAll('.run-card').forEach(el => {
            el.addEventListener('click', () => {
                window.location.hash = `#/run/${encodeURIComponent(el.dataset.id)}`;
            });
        });
    } catch (e) {
        console.error(e);
    }
}

async function loadRunView(jobId) {
    try {
        const run = await fetchJson(`/api/jobs/${encodeURIComponent(jobId)}`);
        $('runTitle').innerHTML = `${escapeHtml(run.description || run.content_code)} ${statusBadge(run.status)}`;
        $('runMeta').innerHTML =
            `<code>${run.job_id}</code> · ${escapeHtml(run.content_code)}
             · started ${relTime(run.started)}${run.finished ? ' · finished ' + relTime(run.finished) : ''}`;
        $('runBackLink').href = `#/config/${encodeURIComponent(run.content_code)}`;
        $('runError').innerHTML = run.error
            ? `<p style="color:var(--error)">Error: ${escapeHtml(run.error)}</p>` : '';
        $('runFlow').innerHTML = renderStepFlow(run.steps || []);
        $('runSteps').innerHTML = (run.steps || []).map(renderStepCard).join('');
    } catch (e) {
        console.error(e);
    }
}

// --- Polling: only the current view ---
function schedulePoll() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
        if (document.visibilityState !== 'visible') return;
        if (currentView === 'home') await loadConfigs();
        else if (currentView === 'config' && currentConfigCode) await loadConfigView(currentConfigCode);
        else if (currentView === 'run' && currentJobId) await loadRunView(currentJobId);
    }, POLL_MS);
}

function resetPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// --- Router ---
function router() {
    const hash = window.location.hash || '#/';
    if (hash.startsWith('#/config/')) {
        const code = decodeURIComponent(hash.slice('#/config/'.length));
        currentConfigCode = code;
        currentJobId = null;
        showView('config');
        loadConfigView(code);
    } else if (hash.startsWith('#/run/')) {
        const id = decodeURIComponent(hash.slice('#/run/'.length));
        currentJobId = id;
        showView('run');
        loadRunView(id);
    } else {
        currentConfigCode = null;
        currentJobId = null;
        showView('home');
        loadConfigs();
    }
}

// --- Actions ---
$('addForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const code = $('newCode').value.trim();
    if (!code) return;
    try {
        await fetchJson('/api/configs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
        });
        $('newCode').value = '';
        await loadConfigs();
    } catch (err) {
        alert(err.message);
    }
});

$('syncAllBtn').addEventListener('click', async () => {
    try {
        const r = await fetchJson('/api/sync-all', { method: 'POST' });
        await loadConfigs();
        if (r.errors) alert(`Synced ${r.ok} config(s), ${r.errors} error(s)`);
    } catch (e) { alert(e.message); }
});

$('refreshConfigsBtn').addEventListener('click', loadConfigs);

$('runConfigBtn').addEventListener('click', async () => {
    if (!currentConfigCode) return;
    try {
        const { job_id } = await fetchJson(
            `/api/configs/${encodeURIComponent(currentConfigCode)}/run`,
            { method: 'POST' });
        window.location.hash = `#/run/${encodeURIComponent(job_id)}`;
    } catch (e) { alert(e.message); }
});

$('syncConfigBtn').addEventListener('click', async () => {
    if (!currentConfigCode) return;
    try {
        await fetchJson(`/api/configs/${encodeURIComponent(currentConfigCode)}/sync`,
                        { method: 'POST' });
        await loadConfigView(currentConfigCode);
    } catch (e) { alert(e.message); }
});

$('deleteConfigBtn').addEventListener('click', async () => {
    if (!currentConfigCode) return;
    if (!confirm(`Delete config ${currentConfigCode} and all its runs?`)) return;
    try {
        await fetchJson(`/api/configs/${encodeURIComponent(currentConfigCode)}`,
                        { method: 'DELETE' });
        window.location.hash = '#/';
    } catch (e) { alert(e.message); }
});

$('deleteRunBtn').addEventListener('click', async () => {
    if (!currentJobId) return;
    if (!confirm(`Delete run ${currentJobId}?`)) return;
    try {
        const run = await fetchJson(`/api/jobs/${encodeURIComponent(currentJobId)}`);
        await fetchJson(`/api/jobs/${encodeURIComponent(currentJobId)}`,
                        { method: 'DELETE' });
        window.location.hash = `#/config/${encodeURIComponent(run.content_code)}`;
    } catch (e) { alert(e.message); }
});

window.addEventListener('hashchange', router);
router();