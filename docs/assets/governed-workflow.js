(function () {
  'use strict';

  const scenarios = {
    normal: {
      story: 'Eligible return — record a recommendation, not a payment.',
      steps: ['proposal', 'checks', 'ack', 'effect', 'result'],
    },
    invalid: {
      story: 'Ineligible return — the proposal must stop before a business write.',
      steps: ['proposal', 'checks', 'deny'],
    },
    supervisor: {
      story: 'Supervisor handoff — a person reviews this specific exception in Outlook.',
      steps: ['proposal', 'checks', 'review', 'fresh', 'ack', 'effect', 'result'],
    },
  };
  const legacyRoutes = {
    'effect-authority': './production.html#effect-authority',
    'workflow-in-action': './production.html#workflow-in-action',
    evidence: './production.html#evidence-boundaries',
    'human-decisions': './production.html#workflow-in-action',
    freshness: './production.html#effect-authority',
    limits: './production.html#effect-authority',
  };
  const steps = {
    proposal: ['Agent proposes', 'Propose', 'The agent proposes a return recommendation for this case. It does not supply its own permission.'],
    checks: ['Governed MCP gateway checks', 'Check', 'The governed MCP gateway checks identity, policy and trusted case facts, then routes only the authorized protected action.'],
    deny: ['Blocked — no business effect', 'Stop', 'Policy refuses the proposal. Record the refusal; do not change the case. This path ends here.'],
    review: ['Person authorizes · Outlook review', 'Outlook', 'Illustration paused at the human decision. Next shows an approved example, not a real approval or email.'],
    fresh: ['Recheck current authority', 'Recheck', 'The example handoff is approved, but its arguments must be unchanged. Recheck current policy, identity and case facts.'],
    ack: ['Control plane records authority', 'Audit ACK', 'The control plane supplies the central audit ACK before the backend receives the business request. No ACK means no dispatch.'],
    effect: ['Backend checks / executes', 'Commit', 'The independent backend checks the current case, then atomically records the decision and its business audit.'],
    result: ['Record connects the outcome', 'Trace', 'The stable result and audit ID join the authorization trail. Recording a recommendation is not a payment.'],
  };
  const STEP_MS = 2000;

  function mount(document, window) {
    const parent = document.body.dataset.chapterParent;
    if (parent) {
      document.querySelector(`.masthead .nav a[href="./${parent}.html"]`)?.setAttribute('aria-current', 'location');
      document.querySelector(`.cx-directory-group[data-site-group="${parent}"]`)?.setAttribute('data-current-group', '');
    }
    if (document.body.hasAttribute('data-web-workbook')) {
      const forward = () => {
        const destination = legacyRoutes[window.location.hash.slice(1)];
        if (destination) window.location.replace(destination);
      };
      window.addEventListener('hashchange', forward);
      forward();
    }
    const root = document.querySelector('[data-governed-flow]');
    if (!root) return;
    const controls = [...root.querySelectorAll('[data-flow-controls]')];
    const tabs = [...root.querySelectorAll('[data-flow-scenario]')];
    const nodes = [...root.querySelectorAll('[data-flow-node]')];
    const mobile = [...root.querySelectorAll('[data-mobile-node]')];
    const edges = [...root.querySelectorAll('[data-flow-edge]')];
    const story = root.querySelector('[data-flow-story]');
    const title = root.querySelector('[data-flow-title]');
    const caption = root.querySelector('[data-flow-caption]');
    const progress = root.querySelector('[data-flow-progress]');
    const motionNote = root.querySelector('[data-flow-motion]');
    const playLabel = root.querySelector('[data-flow-play-label]');
    const panel = root.querySelector('#wf-case');
    const buttons = Object.fromEntries([...root.querySelectorAll('[data-flow-control]')]
      .map((button) => [button.dataset.flowControl, button]));
    if (!controls.length || !story || !title || !caption || !progress || !motionNote || !playLabel || !panel ||
        !['play', 'previous', 'next'].every((name) => buttons[name]) ||
        !Object.keys(steps).every((name) => nodes.some((node) => node.dataset.flowNode === name))) {
      console.error('Workflow illustration controls are incomplete; the static path remains available.');
      return;
    }
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let scenario = 'normal';
    let step = 0;
    let playing = false;
    let timer = null;

    function pause() {
      window.clearTimeout(timer);
      timer = null;
      playing = false;
    }

    function buildProgress() {
      progress.replaceChildren();
      scenarios[scenario].steps.forEach((name, index) => {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.flowStep = String(index);
        button.setAttribute('aria-label', `Step ${index + 1}: ${steps[name][0]}`);
        const number = document.createElement('span');
        number.textContent = String(index + 1);
        const label = document.createElement('span');
        label.className = 'wf-step-name';
        label.textContent = steps[name][1];
        button.append(number, label);
        item.appendChild(button);
        progress.appendChild(item);
      });
    }

    function render() {
      const selected = scenarios[scenario];
      const current = selected.steps[step];
      const finished = step === selected.steps.length - 1;
      root.dataset.step = String(step);
      root.dataset.node = current;
      root.dataset.playing = String(playing);
      root.dataset.scenario = scenario;
      story.textContent = selected.story;
      title.textContent = `${step + 1} / ${selected.steps.length} · ${steps[current][0]}`;
      caption.textContent = steps[current][2];
      panel.setAttribute('aria-labelledby', `wf-tab-${scenario}`);
      tabs.forEach((tab) => {
        const active = tab.dataset.flowScenario === scenario;
        tab.setAttribute('aria-selected', String(active));
        tab.tabIndex = active ? 0 : -1;
      });
      nodes.forEach((node) => {
        const position = selected.steps.indexOf(node.dataset.flowNode);
        node.dataset.onPath = String(position >= 0);
        node.querySelector('[data-flow-number]').textContent = position >= 0 ? String(position + 1) : '';
        if (node.dataset.flowNode === current) node.setAttribute('aria-current', 'step');
        else node.removeAttribute('aria-current');
      });
      mobile.forEach((node) => {
        const position = selected.steps.indexOf(node.dataset.mobileNode);
        node.hidden = position < 0;
        node.toggleAttribute('data-path-last', position === selected.steps.length - 1);
        if (node.dataset.mobileNode === current) node.setAttribute('aria-current', 'step');
        else node.removeAttribute('aria-current');
      });
      edges.forEach((edge) => {
        const [from, to] = edge.dataset.flowEdge.split(':');
        const position = selected.steps.indexOf(from);
        edge.dataset.onPath = String(position >= 0 && selected.steps[position + 1] === to);
      });
      progress.querySelectorAll('button').forEach((button, index) => {
        if (index === step) button.setAttribute('aria-current', 'step');
        else button.removeAttribute('aria-current');
      });
      playLabel.textContent = finished ? 'Replay' : playing ? 'Pause' : 'Play';
      buttons.play.disabled = !finished && (motion.matches || current === 'review');
      buttons.previous.disabled = step === 0;
      buttons.next.disabled = finished;
      buttons.next.setAttribute('aria-label', current === 'review' ? 'Show approved example' : 'Next step');
      buttons.next.title = current === 'review' ? 'Continue the illustration after the example approval' : 'Next step';
      motionNote.textContent = motion.matches ? 'Reduced motion: choose a step or use the arrows.' : '';
    }

    function advance() {
      if (step < scenarios[scenario].steps.length - 1) step += 1;
      if (step === scenarios[scenario].steps.length - 1 || scenarios[scenario].steps[step] === 'review') pause();
      render();
    }

    function schedule() {
      if (!playing) return;
      timer = window.setTimeout(() => { advance(); schedule(); }, STEP_MS);
    }

    buttons.play.addEventListener('click', () => {
      if (step === scenarios[scenario].steps.length - 1) {
        pause(); step = 0; render(); return;
      }
      if (playing) { pause(); render(); return; }
      if (motion.matches || document.hidden || scenarios[scenario].steps[step] === 'review') return;
      playing = true;
      render();
      schedule();
    });
    buttons.previous.addEventListener('click', () => { pause(); step = Math.max(0, step - 1); render(); });
    buttons.next.addEventListener('click', () => { pause(); advance(); });
    progress.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-flow-step]');
      if (!button) return;
      pause(); step = Number(button.dataset.flowStep); render();
    });
    function select(tab) {
      pause(); scenario = tab.dataset.flowScenario; step = 0;
      buildProgress(); render();
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => select(tab));
      tab.addEventListener('keydown', (event) => {
        const offsets = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length,
          Home: 0, End: tabs.length - 1 };
        if (!(event.key in offsets)) return;
        event.preventDefault();
        const next = tabs[offsets[event.key]];
        select(next); next.focus();
      });
    });
    motion.addEventListener('change', () => { pause(); render(); });
    document.addEventListener('visibilitychange', () => { if (document.hidden) { pause(); render(); } });
    window.addEventListener('pagehide', pause);
    const topic = root.closest('[data-topic-panel]');
    if (topic) new MutationObserver(() => {
      if (topic.hidden) { pause(); render(); }
    }).observe(topic, { attributes: true, attributeFilter: ['hidden'] });
    buildProgress(); render();
    root.setAttribute('data-enhanced', '');
    controls.forEach((control) => { control.hidden = false; });
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { scenarios, legacyRoutes };
  else if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => mount(document, window));
  else mount(document, window);
})();
