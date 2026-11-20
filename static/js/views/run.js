import { $, escapeHtml, relTime, statusBadge, circleSymbol } from '../utils.js';
import { api } from '../api.js';

let currentId = null;

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

export async function renderRun(jobId) {
    currentId = jobId;
    try {
        const run = await api.getJob(jobId);
        $('runTitle').innerHTML =
            `${escapeHtml(run.description || run.content_code)} ${statusBadge(run.status)}`;
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

export function bindRunEvents() {
    $('deleteRunBtn').addEventListener('click', async () => {
        if (!currentId) return;
        if (!confirm(`Delete run ${currentId}?`)) return;
        try {
            const run = await api.getJob(currentId);
            await api.deleteJob(currentId);
            window.location.hash = `#/config/${encodeURIComponent(run.content_code)}`;
        } catch (e) { alert(e.message); }
    });
}