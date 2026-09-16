(() => {
  'use strict';

  function init() {
    const root = document.querySelector('.chapter-governance');
    if (!root) return;

    const monitor = root.querySelector('.decision-monitor');
    monitor.hidden = false;
    const heroSelector = root.querySelector('[data-hero-decision]');
    const hero = root.querySelector('.request-stage');
    heroSelector.disabled = false;
    const cases = [...root.querySelectorAll('.decision-case')];
    const states = {
      allowed: {
        title: 'Proceed on this path.',
        detail: 'All required controls hold. Reauthorize at actual dispatch.',
        path: ['CHECKS', 'PERMITTED'],
        visual: ['Controls', 'Required checks hold', 'Decision', 'May be recorded'],
        checks: ['Signed & fresh', 'Scoped & one-use', 'Backend checked', 'Acknowledged'],
      },
      missing: {
        title: 'Stop before the effect.',
        detail: 'The required approval is absent. A persuasive request cannot replace it.',
        path: ['STOP', 'BLOCKED'],
        visual: ['Stop', 'Required approval missing', 'No write', 'Blocked before the effect'],
        checks: ['Signed & fresh', 'Missing', 'Not sufficient alone', 'Cannot authorize alone'],
      },
      drift: {
        title: 'Collect new evidence.',
        detail: 'The deployment changed. The old proof does not apply to this scope.',
        path: ['SCOPE', 'UNVERIFIED'],
        visual: ['Recheck', 'Deployment no longer matches', 'Unverified', 'Collect fresh evidence'],
        checks: ['Reverify binding', 'Reverify scope', 'Observe new target', 'Collect fresh proof'],
      },
      idle: {
        title: 'Choose a scenario.',
        detail: 'This is an illustration, not a live policy engine or an evidence collector.',
        path: ['?', 'NOT ASSESSED'],
        visual: ['Select a case', 'No assessment performed', 'No claim', 'Illustration only'],
        checks: ['Not assessed', 'Not assessed', 'Not assessed', 'Not assessed'],
      },
    };
    const checkNames = ['policy', 'approval', 'facts', 'audit'];

    function render(state) {
      const selected = states[state];
      monitor.dataset.state = state;
      monitor.querySelector('[data-monitor-title]').textContent = selected.title;
      monitor.querySelector('[data-monitor-detail]').textContent = selected.detail;
      monitor.querySelector('.path-gate').textContent = selected.path[0];
      monitor.querySelector('.path-effect').textContent = selected.path[1];
      hero.dataset.state = state;
      hero.querySelector('[data-hero-outcome]').textContent = selected.title;
      hero.querySelector('[data-hero-detail]').textContent = selected.detail;
      ['gate', 'gate-detail', 'effect', 'effect-detail'].forEach((key, index) => {
        hero.querySelector(`[data-hero-${key}]`).textContent = selected.visual[index];
      });
      heroSelector.value = state;
      checkNames.forEach((key, index) => {
        monitor.querySelector(`[data-check="${key}"]`).textContent = selected.checks[index];
      });
    }

    cases.forEach((entry) => {
      entry.addEventListener('toggle', () => {
        if (entry.open) {
          const summary = entry.querySelector('summary');
          const before = summary.getBoundingClientRect().top;
          cases.filter((other) => other !== entry).forEach((other) => { other.open = false; });
          render(entry.id.replace('case-', ''));
          if (document.activeElement === summary) {
            window.scrollBy({ top: summary.getBoundingClientRect().top - before, behavior: 'instant' });
          }
        } else if (!cases.some((other) => other.open)) {
          render('idle');
        }
      });
    });
    heroSelector.addEventListener('change', () => {
      cases.forEach((entry) => { entry.open = entry.id === `case-${heroSelector.value}`; });
      render(heroSelector.value);
    });

    const masthead = root.querySelector('.masthead');
    const resize = new ResizeObserver(() => {
      root.style.setProperty('--masthead-height', `${masthead.offsetHeight}px`);
    });
    resize.observe(masthead);

  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
