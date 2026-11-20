export const $ = (id) => document.getElementById(id);

export function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
}

export function relTime(iso) {
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

export function statusBadge(status) {
    return `<span class="status status-${status}">${status}</span>`;
}

export function circleSymbol(step, index) {
    if (step.status === 'done') return '✓';
    if (step.status === 'failed') return '✗';
    if (step.status === 'running') return '◐';
    return String(index + 1);
}