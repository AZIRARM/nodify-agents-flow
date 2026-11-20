import { renderHome } from './views/home.js';
import { renderConfig } from './views/config.js';
import { renderRun } from './views/run.js';

const POLL_MS = 2500;
let currentView = 'home';
let currentConfigCode = null;
let currentJobId = null;
let pollTimer = null;

function showView(name) {
    ['home', 'config', 'run'].forEach(v => {
        document.getElementById('view-' + v).classList.toggle('hidden', v !== name);
    });
    currentView = name;
    resetPolling();
    schedulePoll();
}

function schedulePoll() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
        if (document.visibilityState !== 'visible') return;
        if (currentView === 'home')                        await renderHome();
        else if (currentView === 'config' && currentConfigCode) await renderConfig(currentConfigCode);
        else if (currentView === 'run'    && currentJobId)      await renderRun(currentJobId);
    }, POLL_MS);
}

function resetPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

export function router() {
    const hash = window.location.hash || '#/';
    if (hash.startsWith('#/config/')) {
        const code = decodeURIComponent(hash.slice('#/config/'.length));
        currentConfigCode = code;
        currentJobId = null;
        showView('config');
        renderConfig(code);
    } else if (hash.startsWith('#/run/')) {
        const id = decodeURIComponent(hash.slice('#/run/'.length));
        currentJobId = id;
        showView('run');
        renderRun(id);
    } else {
        currentConfigCode = null;
        currentJobId = null;
        showView('home');
        renderHome();
    }
}