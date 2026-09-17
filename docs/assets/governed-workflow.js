(function () {
  'use strict';

  const scenarios = {
    normal: {
      story: 'Normal return: the case is eligible and current. Record a recommendation, not a payment.',
      steps: ['proposal', 'checks', 'allow', 'ack', 'effect', 'result'],
    },
    invalid: {
      story: 'Invalid proposal: the case is ineligible. A convincing explanation cannot authorize the write.',
      steps: ['proposal', 'checks', 'deny'],
    },
    supervisor: {
      story: 'Supervisor handoff: this case needs a person to authorize the specific exception, not a refund.',
      steps: ['proposal', 'checks', 'review', 'fresh', 'ack', 'effect', 'result'],
    },
  };
  const captions = {
    proposal: 'The agent proposes fixed arguments for this case. It does not supply its own authority.',
    checks: 'Identity, policy and trusted case facts determine which path the proposal may take.',
    allow: 'The ordinary recommendation is permitted. The authorization record still comes before execution.',
    deny: 'The proposal stops with no business effect. The refusal can be recorded without changing the case.',
    review: 'Wait for the person. This illustration pauses here; use Next step to show an approved handoff.',
    fresh: 'In this example the person approved the unchanged handoff. Recheck current authority, policy and case facts.',
    ack: 'The central audit ACK acknowledges authorization before the backend receives the business request.',
    effect: 'The backend independently checks the current case, then atomically records the decision and business audit.',
    result: 'The stable result and audit ID connect to the authorization trail. No payment was sent.',
  };
  const STEP_MS = 1800;

  function mount(document, window) {
    const root = document.getElementById('workflow-in-action');
    if (!root) return;
    const controls = root.querySelector('[data-flow-controls]');
    const story = root.querySelector('[data-flow-story]');
    const status = root.querySelector('[data-flow-status]');
    const motionNote = root.querySelector('[data-flow-motion]');
    const nodes = [...root.querySelectorAll('[data-flow-node]')];
    const buttons = Object.fromEntries([...root.querySelectorAll('[data-flow-control]')]
      .map((button) => [button.dataset.flowControl, button]));
    if (!controls || !story || !status || !motionNote ||
        !['start', 'pause', 'next', 'replay'].every((name) => buttons[name]) ||
        !Object.keys(captions).every((name) => nodes.some((node) => node.dataset.flowNode === name))) {
      console.error('Workflow illustration controls are incomplete; the static diagram remains available.');
      return;
    }
    const selectors = [...root.querySelectorAll('[data-flow-scenario]')];
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let scenario = 'normal';
    let step = -1;
    let playing = false;
    let timer = null;

    function pause() {
      window.clearTimeout(timer);
      timer = null;
      playing = false;
    }

    function render() {
      const selected = scenarios[scenario];
      const current = selected.steps[step];
      root.dataset.step = String(step);
      root.dataset.node = current || '';
      root.dataset.playing = String(playing);
      root.dataset.scenario = scenario;
      story.textContent = selected.story;
      nodes.forEach((node) => {
        if (node.dataset.flowNode === current) node.setAttribute('aria-current', 'step');
        else node.removeAttribute('aria-current');
      });
      selectors.forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.flowScenario === scenario));
      });
      const finished = step === selected.steps.length - 1;
      buttons.start.disabled = motion.matches || playing || finished || current === 'review';
      buttons.pause.disabled = !playing;
      buttons.next.disabled = finished;
      motionNote.textContent = motion.matches
        ? 'Reduced motion: use Next step. No timed playback.'
        : 'Start plays one path once. Human review pauses for a manual step.';
      status.textContent = current
        ? `Step ${step + 1} of ${selected.steps.length}. ${captions[current]}`
        : 'Choose a scenario, then start or step through the illustration.';
    }

    function advance() {
      const steps = scenarios[scenario].steps;
      if (step < steps.length - 1) step += 1;
      if (step === steps.length - 1 || steps[step] === 'review') pause();
      render();
    }

    function schedule() {
      if (!playing) return;
      timer = window.setTimeout(() => {
        advance();
        schedule();
      }, STEP_MS);
    }

    buttons.start.addEventListener('click', () => {
      if (motion.matches || playing || document.hidden) return;
      playing = true;
      if (step < 0) advance();
      else render();
      schedule();
    });
    buttons.pause.addEventListener('click', () => { pause(); render(); });
    buttons.next.addEventListener('click', () => { pause(); advance(); });
    buttons.replay.addEventListener('click', () => { pause(); step = -1; advance(); });
    selectors.forEach((button) => {
      button.addEventListener('click', () => {
        pause();
        scenario = button.dataset.flowScenario;
        step = -1;
        render();
      });
    });
    motion.addEventListener('change', () => { pause(); render(); });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { pause(); render(); }
    });
    window.addEventListener('pagehide', pause);
    render();
    controls.hidden = false;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { scenarios };
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => mount(document, window));
  } else {
    mount(document, window);
  }
})();
