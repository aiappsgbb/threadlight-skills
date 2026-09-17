(function () {
  'use strict';

  const scenarios = {
    normal: {
      story: 'Allowed recommendation: no human review is required.',
      note: 'Authorization receipt first; conditional decision and business audit afterward. This is not a payment.',
      steps: ['proposal', 'checks', 'ack', 'effect', 'result'],
    },
    invalid: {
      story: 'Blocked proposal: record the denial, then stop without a business write.',
      note: 'Backend facts/health may have been read. No business write is sent; no human approval is required.',
      steps: ['proposal', 'checks', 'denial-audit', 'deny'],
    },
    supervisor: {
      story: 'Human review: wait for the exact Outlook decision before any business write.',
      note: 'Reject, expiry or changed facts stops the write. Continue shows an approved example; it never creates a real grant.',
      steps: ['proposal', 'checks', 'pending', 'review', 'verify', 'fresh', 'ack', 'effect', 'result'],
    },
  };
  const definitions = {
    proposal: { actor: 'Agent', actors: ['agent'], title: 'Agent proposes', action: 'Propose the exact case action', output: 'Exact case request', short: 'Exact proposal', icon: 'branch' },
    checks: { actor: 'Governed MCP gateway', actors: ['gateway'], title: 'Gateway decides', action: 'Evaluate local ACS/Rego and trusted facts', output: '', icon: 'shield' },
    'denial-audit': { actor: 'Control plane', actors: ['control'], title: 'Control plane records denial', action: 'Persist the denial audit', output: 'Denial audit recorded', short: 'Denial recorded', icon: 'policy' },
    deny: { actor: 'Governed MCP gateway', actors: ['gateway'], title: 'Return blocked', action: 'Return the blocked outcome', output: 'No business write', icon: 'stop' },
    pending: { actor: 'Control plane + Logic Apps', actors: ['control'], title: 'Register review request', action: 'Persist intent and send the Outlook request', output: 'pending_approval', short: 'Pending approval', icon: 'policy' },
    review: { actor: 'Human in Outlook', actors: ['human'], title: 'Person decides in Outlook', action: 'Review this specific proposal', output: 'Awaiting human decision', short: 'Await decision', icon: 'person' },
    verify: { actor: 'Control plane', actors: ['control'], title: 'Verify response and grant', action: 'Verify native witness, responder and scope', output: 'One-use grant', icon: 'policy' },
    fresh: { actor: 'Gateway + control plane', actors: ['gateway', 'control'], title: 'Recheck and consume', action: 'Recheck current policy, unchanged arguments/facts and consume approval', output: 'Consumed once', icon: 'shield' },
    ack: { actor: 'Control plane', actors: ['control'], title: 'Acknowledge authorization', action: 'Persist authorization before the business request', output: 'Authorization receipt ACK', short: 'Receipt ACK', icon: 'policy' },
    effect: { actor: 'Business API / own writer', actors: ['business'], title: 'Backend checks and commits', action: 'Authorize and commit a conditional case transaction', output: 'Decision + business audit', short: 'Decision + audit', icon: 'store' },
    result: { actor: 'Agent', actors: ['agent'], title: 'Return the stable result', action: 'Return the recorded outcome', output: 'Stable audit ID', icon: 'branch' },
  };
  const actorLabels = { agent: 'Agent', gateway: 'Gateway', control: 'Control plane', human: 'Human / Outlook', business: 'Business API' };
  const legacyRoutes = {
    'effect-authority': './production.html#effect-authority',
    'workflow-in-action': './production.html#workflow-in-action',
    evidence: './production.html#evidence-boundaries',
    'human-decisions': './production.html#workflow-in-action',
    freshness: './production.html#effect-authority',
    limits: './production.html#effect-authority',
  };
  const workbookBase = 'https://github.com/aiappsgbb/threadlight-skills/blob/7782eba93754fb7cff85336d3f4a8703892bad76/docs/first-governed-workflow.md';
  const workbookRoutes = {
    define: `${workbookBase}#phase-1-describe-the-decision-you-want-to-improve`,
    prepare: `${workbookBase}#phase-2-prepare-the-people-inputs-and-access-handoff`,
    local: `${workbookBase}#phase-3-rehearse-locally-without-calling-it-azure-proof`,
    authorize: `${workbookBase}#phase-4-opt-in-to-governance-and-review-the-hosted-setup`,
    validate: `${workbookBase}#phase-5-demonstrate-allow-deny-and-a-real-human-decision`,
    operate: `${workbookBase}#phase-6-hand-off-a-controlled-release-and-safe-operations`,
  };
  const STEP_MS = 2500;

  function stepDetails(scenario, id) {
    const result = { ...definitions[id] };
    if (id === 'checks') result.output = { normal: 'Allow', invalid: 'Deny', supervisor: 'Review required' }[scenario];
    return result;
  }

  function stageState(scenario, index, current) {
    const route = scenarios[scenario].steps;
    if (index < current || current === route.length - 1) return 'Completed';
    if (index > current) return 'Waiting';
    return route[current] === 'review' ? 'Waiting for decision' : 'In progress';
  }

  function actorState(scenario, actor, current) {
    if (actor === 'human' && scenario !== 'supervisor') return 'Not required';
    if (actor === 'business' && scenario === 'invalid') return 'Not involved in write';
    const route = scenarios[scenario].steps;
    const positions = route.flatMap((id, index) => definitions[id].actors.includes(actor) ? [index] : []);
    if (current === route.length - 1) return 'Completed';
    if (positions.includes(current)) return stageState(scenario, current, current);
    return positions.some((index) => index > current) ? 'Waiting' : 'Completed';
  }

  function mount(document, window) {
    const parent = document.body.dataset.chapterParent;
    if (parent) {
      document.querySelector(`.masthead .nav a[href="./${parent}.html"]`)?.setAttribute('aria-current', 'location');
      document.querySelector(`.cx-directory-group[data-site-group="${parent}"]`)?.setAttribute('data-current-group', '');
    }
    if (document.body.hasAttribute('data-web-workbook')) {
      const forward = () => {
        const key = window.location.hash.slice(1);
        const destination = legacyRoutes[key] || workbookRoutes[key];
        if (destination) window.location.replace(destination);
      };
      window.addEventListener('hashchange', forward);
      forward();
    }
    const root = document.querySelector('[data-governed-flow]');
    if (!root) return;
    const controls = [...root.querySelectorAll('[data-flow-controls]')];
    const tabs = [...root.querySelectorAll('[data-flow-scenario]')];
    const svg = root.querySelector('.wf-diagram');
    const nodeLayer = root.querySelector('[data-node-layer]');
    const edgeLayer = root.querySelector('[data-edge-layer]');
    const mobile = root.querySelector('.wf-mobile');
    const story = root.querySelector('[data-flow-story]');
    const title = root.querySelector('[data-flow-title]');
    const stateLabel = root.querySelector('[data-current-state]');
    const actorLabel = root.querySelector('[data-flow-actor]');
    const actionLabel = root.querySelector('[data-flow-action]');
    const outputLabel = root.querySelector('[data-flow-output]');
    const caseNote = root.querySelector('[data-flow-case-note]');
    const progress = root.querySelector('[data-flow-progress]');
    const motionNote = root.querySelector('[data-flow-motion]');
    const playLabel = root.querySelector('[data-flow-play-label]');
    const nextLabel = root.querySelector('[data-next-label]');
    const panel = root.querySelector('#wf-case');
    const buttons = Object.fromEntries([...root.querySelectorAll('[data-flow-control]')]
      .map((button) => [button.dataset.flowControl, button]));
    if (!controls.length || !svg || !nodeLayer || !edgeLayer || !mobile || !story || !title ||
        !stateLabel || !actorLabel || !actionLabel || !outputLabel || !caseNote || !progress ||
        !motionNote || !playLabel || !nextLabel || !panel || !['play', 'previous', 'next'].every((name) => buttons[name])) {
      console.error('Workflow illustration controls are incomplete; the static path remains available.');
      return;
    }
    const actors = [...document.querySelectorAll('#effect-authority [data-action-actor]')];
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let scenario = 'normal';
    let step = 0;
    let playing = false;
    let exampleApproved = false;
    let timer = null;

    function pause() { window.clearTimeout(timer); timer = null; playing = false; }
    function svgElement(name, attributes, text) {
      const element = document.createElementNS('http://www.w3.org/2000/svg', name);
      Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
      if (text !== undefined) element.textContent = text;
      return element;
    }
    function iconReference(icon) { return icon === 'stop' ? '#wf-icon-stop' : `#production-icon-${icon}`; }

    function buildPath() {
      const route = scenarios[scenario].steps;
      const columns = route.length > 5 ? 3 : route.length;
      const width = columns === 3 ? 300 : columns === 4 ? 230 : 180;
      const gap = (1060 - columns * width) / (columns - 1);
      const rows = Math.ceil(route.length / columns);
      svg.setAttribute('viewBox', `0 0 1100 ${rows * 128 - 10}`);
      svg.querySelector('desc').textContent = `${scenarios[scenario].story} ${route.map((id) => {
        const detail = stepDetails(scenario, id);
        return `${detail.actor}: ${detail.output}.`;
      }).join(' ')} ${scenarios[scenario].note}`;
      nodeLayer.replaceChildren(); edgeLayer.replaceChildren(); mobile.replaceChildren(); progress.replaceChildren();
      const positions = route.map((_, index) => {
        const row = Math.floor(index / columns);
        const column = row % 2 ? columns - 1 - index % columns : index % columns;
        return { x: 20 + column * (width + gap), y: 12 + row * 128, row };
      });
      route.forEach((id, index) => {
        const detail = stepDetails(scenario, id);
        const { x, y } = positions[index];
        const node = svgElement('g', { class: 'wf-node', 'data-flow-node': id, transform: `translate(${x} ${y})` });
        node.append(
          svgElement('rect', { width, height: 94, rx: 10 }),
          svgElement('use', { class: 'wf-icon', 'data-component-icon': '', 'aria-hidden': 'true',
            href: iconReference(detail.icon), x: 14, y: 13, width: 24, height: 24 }),
          svgElement('text', { class: 'wf-label', x: 46, y: 29 }, actorLabels[detail.actors[0]]),
          svgElement('text', { class: 'wf-number', x: width - 16, y: 12 }, index + 1),
          svgElement('text', { class: 'wf-detail', 'data-node-output': '', x: 14, y: 58 }, detail.short || detail.output),
          svgElement('text', { class: 'wf-node-state', 'data-node-state': '', x: 14, y: 81 }, 'Waiting'),
        );
        nodeLayer.appendChild(node);
        if (index) {
          const previous = positions[index - 1];
          const d = y !== previous.y
            ? `M${previous.x + width / 2} ${previous.y + 94}V${y - 5}`
            : x > previous.x
              ? `M${previous.x + width} ${y + 47}H${x - 5}`
              : `M${previous.x} ${y + 47}H${x + width + 5}`;
          edgeLayer.appendChild(svgElement('path', { class: 'wf-edge', 'data-flow-edge': `${route[index - 1]}:${id}`,
            d, 'marker-end': 'url(#wf-arrow)' }));
        }
        const item = document.createElement('li');
        item.dataset.mobileNode = id;
        if (index === route.length - 1) item.setAttribute('data-path-last', '');
        const icon = svgElement('svg', { class: 'wf-icon', 'aria-hidden': 'true' });
        icon.appendChild(svgElement('use', { href: iconReference(detail.icon) }));
        const copy = document.createElement('div');
        const actor = document.createElement('strong');
        actor.textContent = `${index + 1}. ${actorLabels[detail.actors[0]]}`;
        const output = document.createElement('span');
        output.setAttribute('data-node-output', '');
        output.textContent = detail.output;
        const state = document.createElement('small');
        state.setAttribute('data-node-state', '');
        copy.append(actor, output, state); item.append(icon, copy); mobile.appendChild(item);
        const marker = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button'; button.dataset.flowStep = String(index);
        button.setAttribute('aria-label', `Step ${index + 1}: ${detail.title}`);
        const number = document.createElement('span');
        number.textContent = String(index + 1);
        const stateText = document.createElement('small');
        stateText.setAttribute('data-progress-state', '');
        button.append(number, stateText); marker.appendChild(button); progress.appendChild(marker);
      });
    }

    function render() {
      const route = scenarios[scenario].steps;
      const current = route[step];
      const detail = stepDetails(scenario, current);
      const finished = step === route.length - 1;
      root.dataset.step = String(step); root.dataset.node = current;
      root.dataset.playing = String(playing); root.dataset.scenario = scenario;
      story.textContent = scenarios[scenario].story;
      title.textContent = `Illustration · ${step + 1} / ${route.length}`;
      stateLabel.textContent = stageState(scenario, step, step);
      actorLabel.textContent = detail.actor; actionLabel.textContent = detail.action; outputLabel.textContent = detail.output;
      caseNote.textContent = scenarios[scenario].note;
      panel.setAttribute('aria-labelledby', `wf-tab-${scenario}`);
      tabs.forEach((tab) => {
        const active = tab.dataset.flowScenario === scenario;
        tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
      });
      for (const [selector, key] of [['[data-flow-node]', 'flowNode'], ['[data-mobile-node]', 'mobileNode']]) {
        root.querySelectorAll(selector).forEach((node) => {
          const index = route.indexOf(node.dataset[key]);
          const state = stageState(scenario, index, step);
          node.querySelector('[data-node-state]').textContent = state;
          const output = node.querySelector('[data-node-output]');
          const item = stepDetails(scenario, node.dataset[key]);
          output.textContent = node.dataset[key] === 'review' && state === 'Completed' && exampleApproved
            ? 'Approved example' : key === 'flowNode' ? item.short || item.output : item.output;
          node.dataset.stageState = state;
          if (index === step) node.setAttribute('aria-current', 'step');
          else node.removeAttribute('aria-current');
        });
      }
      edgeLayer.querySelectorAll('path').forEach((edge, index) => {
        edge.dataset.onPath = 'true';
        edge.dataset.edgeState = index < step || finished ? 'Completed' : 'Waiting';
      });
      actors.forEach((actor) => {
        const status = actor.querySelector('[data-actor-state]');
        if (status) status.textContent = actorState(scenario, actor.dataset.actionActor, step);
      });
      const reviewIndex = route.indexOf('review');
      progress.querySelectorAll('button').forEach((button, index) => {
        const state = stageState(scenario, index, step);
        button.querySelector('[data-progress-state]').textContent = state === 'Waiting for decision' ? 'Waiting' : state;
        button.disabled = reviewIndex >= 0 && index > reviewIndex && !exampleApproved;
        if (index === step) button.setAttribute('aria-current', 'step');
        else button.removeAttribute('aria-current');
      });
      playLabel.textContent = finished ? 'Replay' : playing ? 'Pause' : 'Play';
      buttons.play.disabled = !finished && (motion.matches || current === 'review');
      buttons.previous.disabled = step === 0; buttons.next.disabled = finished;
      buttons.next.setAttribute('aria-label', current === 'review' ? 'Show approved example' : 'Next step');
      nextLabel.hidden = current !== 'review';
      motionNote.textContent = motion.matches ? 'Reduced motion: choose a step or use the arrows.' : '';
    }

    function moveTo(index) {
      const reviewIndex = scenarios[scenario].steps.indexOf('review');
      if (reviewIndex >= 0 && index <= reviewIndex) exampleApproved = false;
      step = index; render();
    }
    function advance() {
      const route = scenarios[scenario].steps;
      if (route[step] === 'review' && !exampleApproved) { pause(); render(); return; }
      if (step < route.length - 1) step += 1;
      if (step === route.length - 1 || route[step] === 'review') pause();
      render();
    }
    function schedule() {
      if (!playing) return;
      timer = window.setTimeout(() => { advance(); schedule(); }, STEP_MS);
    }
    buttons.play.addEventListener('click', () => {
      if (step === scenarios[scenario].steps.length - 1) { pause(); moveTo(0); return; }
      if (playing) { pause(); render(); return; }
      if (motion.matches || document.hidden || scenarios[scenario].steps[step] === 'review') return;
      playing = true; render(); schedule();
    });
    buttons.previous.addEventListener('click', () => { pause(); moveTo(Math.max(0, step - 1)); });
    buttons.next.addEventListener('click', () => {
      pause();
      if (scenarios[scenario].steps[step] === 'review') exampleApproved = true;
      advance();
    });
    progress.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-flow-step]');
      if (!button || button.disabled) return;
      pause(); moveTo(Number(button.dataset.flowStep));
    });
    function select(tab) {
      pause(); scenario = tab.dataset.flowScenario; step = 0; exampleApproved = false;
      buildPath(); render();
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => select(tab));
      tab.addEventListener('keydown', (event) => {
        const offsets = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length,
          Home: 0, End: tabs.length - 1 };
        if (!(event.key in offsets)) return;
        event.preventDefault(); const next = tabs[offsets[event.key]]; select(next); next.focus();
      });
    });
    motion.addEventListener('change', () => { pause(); render(); });
    document.addEventListener('visibilitychange', () => { if (document.hidden) { pause(); render(); } });
    window.addEventListener('pagehide', pause);
    const topic = root.closest('[data-topic-panel]');
    if (topic) new MutationObserver(() => { if (topic.hidden) { pause(); render(); } })
      .observe(topic, { attributes: true, attributeFilter: ['hidden'] });
    buildPath(); render(); root.setAttribute('data-enhanced', '');
    controls.forEach((control) => { control.hidden = false; });
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { scenarios, legacyRoutes, workbookRoutes, stepDetails, stageState, actorState };
  else if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => mount(document, window));
  else mount(document, window);
})();
