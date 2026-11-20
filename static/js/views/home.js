import { $, escapeHtml, relTime, statusBadge } from '../utils.js';
import { api } from '../api.js';

export async function renderHome() {
    try {
        const { configs } = await api.listConfigs();
        const list = $('configsList');
        if (!configs.length) {
            list.innerHTML = '<p class="empty">No configurations yet.</p>';
            return;
        }
        list.innerHTML = configs.map(c => `
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
                ${c.last_sync_error
                    ? `<span class="status status-failed" title="${escapeHtml(c.last_sync_error)}">sync!</span>`
                    : ''}
            </div>`).join('');
        list.querySelectorAll('.config-card').forEach(el => {
            el.addEventListener('click', () => {
                window.location.hash = `#/config/${encodeURIComponent(el.dataset.code)}`;
            });
        });
    } catch (e) {
        console.error(e);
    }
}

export function bindHomeEvents() {
    $('addForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const code = $('newCode').value.trim();
        if (!code) return;
        try {
            await api.addConfig(code);
            $('newCode').value = '';
            await renderHome();
        } catch (err) { alert(err.message); }
    });

    $('syncAllBtn').addEventListener('click', async () => {
        try {
            const r = await api.syncAll();
            await renderHome();
            if (r.errors) alert(`Synced ${r.ok} config(s), ${r.errors} error(s)`);
        } catch (e) { alert(e.message); }
    });

    $('refreshConfigsBtn').addEventListener('click', renderHome);
}