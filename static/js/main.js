import { router } from './router.js';
import { bindHomeEvents } from './views/home.js';
import { bindConfigEvents } from './views/config.js';
import { bindRunEvents } from './views/run.js';

bindHomeEvents();
bindConfigEvents();
bindRunEvents();

window.addEventListener('hashchange', router);
router();