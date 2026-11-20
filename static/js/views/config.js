import { $, escapeHtml, relTime, statusBadge } from '../utils.js';
import { api } from '../api.js';

let currentCode = null;

export async function renderConfig(code) {
    currentCode = code;
    try {
        const cfg = await api.getConfig(code);
        $('configTitle').textContent = cfg.description;
        let meta =
            `<code>${escapeHtml(cfg.content_code)}</code>
             · synced ${relTime(cfg.last_synced)}
             · ✓ ${cfg.success_count || 0} / ✗ ${cfg.failure_count || 0}`;
        if (cfg.last_sync_error) {
            meta += `<br><span style="color:var(--error)">Last sync error: ${escapeHtml(cfg.last_sync_error)}</span>`;
        }
        $('configMeta').innerHTML = meta;

        const { runs } = await api.listRuns(code);
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

export function bindConfigEvents() {
    $('runConfigBtn').addEventListener('click', async () => {
        if (!currentCode) return;
        try {
            const { job_id } = await api.runConfig(currentCode);
            window.location.hash = `#/run/${encodeURIComponent(job_id)}`;
        } catch (e) { alert(e.message); }
    });

    $('syncConfigBtn').addEventListener('click', async () => {
        if (!currentCode) return;
        try {
            await api.syncConfig(currentCode);
            await renderConfig(currentCode);
        } catch (e) { alert(e.message); }
    });

    $('deleteConfigBtn').addEventListener('click', async () => {
        if (!currentCode) return;
        if (!confirm(`Delete config ${currentCode} and all its runs?`)) return;
        try {
            await api.deleteConfig(currentCode);
            window.location.hash = '#/';
        } catch (e) { alert(e.message); }
    });
}