(function () {
  'use strict';

  const scenarios = {
    normal: {
      story: 'Allowed operation: the return case can be updated without human review.',
      note: 'Authorization receipt first; conditional decision and business audit afterward. This is not a payment.',
      steps: ['proposal', 'checks', 'ack', 'effect', 'result'],
    },
    invalid: {
      story: 'Blocked proposal: record the denial, then stop without a business write.',
      note: 'Backend facts/health may have been read. No business write is sent; no human approval is required.',
      steps: ['proposal', 'checks', 'denial-audit', 'deny'],
    },
    supervisor: {
      story: 'This case update needs human review: a person may permit this exact operation once, before its deadline.',
      note: 'Reject, expiry or changed facts stops the write. Continue shows an approved example; it never creates a real grant.',
      steps: ['proposal', 'checks', 'pending', 'review', 'verify', 'fresh', 'ack', 'effect', 'result'],
    },
    evidence: {
      story: 'Below the automatic amount limit, but purchase proof is still required.',
      note: 'A signed verification covers checked facts, not universal document certification. Human approval cannot replace missing proof.',
      steps: ['proposal', 'proof', 'present', 'checks', 'denial-audit', 'deny'],
    },
    confirmation: {
      story: 'The requesting user must confirm this exact return decision outside the agent conversation.',
      note: 'A notification is not consent. Reject, expiry or changed inputs stops the write. This is an illustration, not live evidence.',
      steps: ['proposal', 'checks', 'confirmation-pending', 'confirm', 'confirmation-verify', 'fresh', 'ack', 'effect', 'result'],
    },
  };
  const evidenceCases = {
    missing: {
      story: 'No corroborated purchase, no certificate. If the agent still asks to record the decision, the gateway blocks it.',
      proof: 'No proof issued: purchase not corroborated', verdict: 'Deny: required evidence missing',
      steps: ['proposal', 'proof', 'present', 'checks', 'denial-audit', 'deny'],
    },
    valid: {
      story: 'RMA-EXAMPLE: the purchase matches the case customer and amount. Signed proof covers revision r7; business rules still apply.',
      proof: 'Expiring proof for this case and revision', verdict: 'Allow: proof and policy match',
      steps: ['proposal', 'proof', 'present', 'checks', 'ack', 'effect', 'result'],
    },
    changed: {
      story: 'The call now names revision r8, but the signed proof covers r7. The old proof cannot authorize the changed inputs.',
      proof: 'Existing expiring proof for revision r7', verdict: 'Deny: proof covers a different revision',
      steps: ['proposal', 'proof', 'present', 'checks', 'denial-audit', 'deny'],
    },
  };
  const confirmationCases = {
    confirmed: { outcome: 'Confirmed for this proposal only' },
    rejected: { outcome: 'Rejected by the requesting user',
      steps: ['proposal', 'checks', 'confirmation-pending', 'confirm', 'confirmation-verify', 'denial-audit', 'deny'] },
    expired: { outcome: 'Expired before use',
      steps: ['proposal', 'checks', 'confirmation-pending', 'confirm', 'confirmation-verify', 'denial-audit', 'deny'] },
    changed: { outcome: 'Changed inputs: confirmation no longer matches',
      steps: ['proposal', 'checks', 'confirmation-pending', 'confirm', 'confirmation-verify', 'denial-audit', 'deny'] },
  };
  const decisionStep = id => ['review', 'confirm'].includes(id);
  function scenarioDetails(scenario, evidenceCase = scenario === 'confirmation' ? 'confirmed' : 'missing') {
    return scenario === 'evidence' ? { ...scenarios.evidence, ...evidenceCases[evidenceCase] }
      : scenario === 'confirmation' ? { ...scenarios.confirmation, ...confirmationCases[evidenceCase] } : scenarios[scenario];
  }
  const definitions = {
    proposal: { actor: 'Agent', actors: ['agent'], title: 'Agent proposes', action: 'Propose the exact case action', output: 'Exact case request', short: 'Exact proposal', icon: 'branch' },
    checks: { actor: 'Governed MCP gateway', actors: ['gateway'], title: 'Gateway enforces', action: 'Verify evidence, evaluate local ACS policy and enforce the verdict', output: '', icon: 'shield' },
    'denial-audit': { actor: 'Control plane', actors: ['control'], title: 'Control plane records denial', action: 'Persist the denial audit', output: 'Denial audit recorded', short: 'Denial recorded', icon: 'policy' },
    deny: { actor: 'Governed MCP gateway', actors: ['gateway'], title: 'Return blocked', action: 'Return the blocked outcome', output: 'No business write', icon: 'stop' },
    pending: { actor: 'Control plane + Logic Apps', actors: ['control'], title: 'Register review request', action: 'Persist intent and send the Outlook request', output: 'pending_approval', short: 'Pending approval', icon: 'policy' },
    review: { actor: 'Human in Outlook', actors: ['human'], title: 'Person decides in Outlook', action: 'Review this specific proposal', output: 'Awaiting human decision', short: 'Await decision', icon: 'person' },
    verify: { actor: 'Control plane', actors: ['control'], title: 'Verify response and grant', action: 'Verify native witness, responder and scope', output: 'One-use grant', icon: 'policy' },
    fresh: { actor: 'Gateway + control plane', actors: ['gateway', 'control'], title: 'Recheck and consume', action: 'Recheck current policy, unchanged arguments/facts and consume approval', output: 'Consumed once', icon: 'shield' },
    ack: { actor: 'Control plane', actors: ['control'], title: 'Acknowledge authorization', action: 'Persist authorization before the business request', output: 'Authorization receipt ACK', short: 'Receipt ACK', icon: 'policy' },
    effect: { actor: 'Business API / own writer', actors: ['business'], title: 'Backend checks and commits', action: 'Authorize and commit a conditional case transaction', output: 'Decision + business audit', short: 'Decision + audit', icon: 'store' },
    result: { actor: 'Agent', actors: ['agent'], title: 'Return the stable result', action: 'Return the recorded outcome', output: 'Stable audit ID', icon: 'branch' },
    proof: { actor: 'Evidence Provider', actors: [], title: 'Verify purchase evidence',
      action: 'The external provider compares the purchase snapshot with the case customer, amount and currency, then returns proof to the agent',
      output: '', icon: 'policy' },
    present: { actor: 'Agent', actors: ['agent'], title: 'Present proof with the action',
      action: 'Ask the gateway to perform the action, presenting the returned proof',
      output: 'Action request plus expiring proof', icon: 'branch' },
    'confirmation-pending': { actor: 'Gateway + confirmation service', actors: ['gateway', 'control'],
      title: 'Wait without acting', action: 'Save the exact proposal and notify the registered user',
      output: 'pending_confirmation + IDs; no effect', icon: 'policy' },
    confirm: { actor: 'Requesting user', actors: ['user'], title: 'User decides separately',
      action: 'Matching authenticated user views the exact proposal, then explicitly confirms or rejects',
      output: 'Awaiting user decision; no business write', icon: 'person' },
    'confirmation-verify': { actor: 'Confirmation service', actors: ['control'], title: 'Verify the user decision',
      action: 'Verify the matching subject, exact proposal and deadline', output: '', icon: 'policy' },
  };
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
  const narrationProfile = {
    language: 'en-US', engine: 'edge-tts', voice: 'en-US-AvaMultilingualNeural', rate: '+0%',
  };
  const caseIntroduction = 'A customer asks to return an order. The gateway invokes a real operation: updating this case and its decision audit, not a payment. ';
  const narrationClips = {
    'intro-normal': caseIntroduction + 'Here, the required checks pass, so the decision can proceed without human review.',
    'intro-invalid': caseIntroduction + 'Here, a policy requirement is not met. A convincing answer still cannot authorize the write.',
    'intro-supervisor': caseIntroduction + "Here, policy requires a person's approval for this exact decision, with a deadline and a single permitted use.",
    'intro-evidence-valid': caseIntroduction + 'Here, the agent must obtain signed purchase evidence before asking the gateway to act.',
    'intro-evidence-missing': caseIntroduction + 'Here, the purchase cannot be corroborated. Watch what happens if the agent still asks to record the decision.',
    'intro-evidence-changed': caseIntroduction + 'Here, proof covers an earlier revision. The agent changes the request, but presents that old proof.',
    'intro-confirmation': caseIntroduction + 'Here, the requesting user must confirm this exact decision separately from the agent conversation.',
    proposal: 'The agent proposes the case decision. That proposal is a request, not permission to act.',
    checks: 'The gateway checks trusted facts and policy, independently of how persuasive the agent sounds.',
    'denial-audit': 'The refusal is recorded so the decision can be traced. That audit is not a business write.',
    deny: 'The agent receives a blocked result. No request to change the business record is sent downstream.',
    pending: 'This proposal needs an authorized reviewer. The request is saved before asking for their decision.',
    review: 'We pause here for that person. Continuing this illustration shows approval, but creates no real grant.',
    verify: 'Approval is valid once for this exact proposal, only until the original deadline.',
    fresh: 'The gateway rechecks the same context, then consumes that permission before sending the action.',
    ack: 'Authorization is recorded before execution. Without that acknowledgement, the action cannot proceed.',
    effect: 'The gateway calls the business backend. It checks current data, then updates the return case and its audit.',
    result: 'The case has changed, not just been approved. Replay returns the recorded result without another execution.',
    'evidence-request': 'The agent asks the external provider to verify the purchase, using this case and the proposed inputs.',
    'evidence-proof-valid': 'The provider matches the purchase, customer and amount. It returns signed, expiring proof for this exact request.',
    'evidence-proof-missing': "The source does not corroborate the purchase. The provider refuses to turn the agent's claim into proof.",
    'evidence-proof-changed': "The provider's proof covers the original case revision. It does not automatically cover a later change.",
    'evidence-present-valid': 'The proof comes back to the same agent. That agent now asks the gateway to perform the action.',
    'evidence-present-missing': 'The agent still attempts the action without the required proof. This is the request the gateway must reject.',
    'evidence-present-changed': 'The agent presents its revised request with the old proof. The words have changed, but the proof has not.',
    'evidence-checks-valid': "The gateway checks the proof's source, expiry and matching request. Valid proof still does not override other rules.",
    'evidence-checks-missing': 'Required evidence is absent. The gateway rejects the request before sending any business write.',
    'evidence-checks-changed': 'The proof does not match the revised request. The gateway rejects it before business execution.',
    'confirmation-proposal': 'The registered user context binds this request. Its reference is not consent or a change of workload identity.',
    'confirmation-checks': 'The gateway checks policy and facts. This action also requires the requesting user to confirm.',
    'confirmation-pending': 'The service saves the proposal and notifies the user. No business change has happened.',
    confirm: 'We pause for the requesting user. Viewing a message or page never confirms. Choose an illustrative outcome.',
    'confirmation-verify-confirmed': 'The matching user explicitly confirmed this proposal. That permission is limited to one use before expiry.',
    'confirmation-verify-rejected': 'The user rejected this proposal. Record the refusal without a business write.',
    'confirmation-verify-expired': 'The confirmation expired before use. It cannot authorize the action.',
    'confirmation-verify-changed': 'The inputs changed. The old confirmation cannot authorize the new proposal.',
    'confirmation-fresh': 'Recheck policy, facts and user binding. Consume the exact permission once, then obtain the audit acknowledgement.',
  };
  function narrationFor(scenario, step, evidenceCase = scenario === 'confirmation' ? 'confirmed' : 'valid') {
    const id = step === 'intro' ? `intro-${scenario}${scenario === 'evidence' ? `-${evidenceCase}` : ''}`
      : scenario === 'confirmation' ? step === 'confirmation-verify' ? `confirmation-verify-${evidenceCase}`
        : ['proposal', 'checks', 'fresh'].includes(step) ? `confirmation-${step}` : step
      : scenario !== 'evidence' ? step : step === 'proposal' ? 'evidence-request'
      : ['proof', 'present', 'checks'].includes(step) ? `evidence-${step}-${evidenceCase}` : step;
    return { id, text: narrationClips[id] };
  }
  const moduleOrder = ['proposal', 'proof', 'present', 'checks', 'deny', 'review', 'confirm', 'fresh', 'ack', 'effect', 'result'];
  const moduleLabels = { proposal: 'Agent proposes', checks: 'Gateway checks', deny: 'Stop / no effect',
    proof: 'External Evidence Provider', present: 'Same agent calls the operational tool',
    review: 'Outlook review', confirm: 'User confirmation', fresh: 'Fresh checks', ack: 'Shared authority checks', effect: 'Backend write', result: 'Decision + audit' };
  const stepModule = id => ({ 'denial-audit': 'ack', pending: 'ack', verify: 'ack',
    'confirmation-pending': 'ack', 'confirmation-verify': 'ack' }[id] || id);
  function usedModules(scenario, evidenceCase = scenario === 'confirmation' ? 'confirmed' : 'missing') {
    if (scenario === 'confirmation') return evidenceCase === 'confirmed'
      ? ['proposal', 'checks', 'ack', 'confirm', 'fresh', 'effect', 'result']
      : ['proposal', 'checks', 'ack', 'confirm', 'deny'];
    if (scenario === 'evidence') return evidenceCase === 'valid'
      ? ['proposal', 'proof', 'present', 'checks', 'ack', 'effect', 'result']
      : ['proposal', 'proof', 'present', 'checks', 'ack', 'deny'];
    return { normal: ['proposal', 'checks', 'ack', 'effect', 'result'],
      invalid: ['proposal', 'checks', 'ack', 'deny'],
      supervisor: ['proposal', 'checks', 'ack', 'review', 'fresh', 'effect', 'result'] }[scenario];
  }
  function permittedEdges(scenario, evidenceCase = scenario === 'confirmation' ? 'confirmed' : 'missing') {
    if (scenario === 'confirmation') return ['proposal:checks', 'checks:confirm',
      ...(evidenceCase === 'confirmed' ? ['confirm:fresh', 'fresh:ack', 'ack:effect', 'effect:result']
        : ['confirm:ack', 'ack:deny'])];
    if (scenario === 'evidence') return ['proposal:proof', 'proof:present', 'present:checks', 'checks:ack',
      ...(evidenceCase === 'valid' ? ['ack:effect', 'effect:result'] : ['ack:deny'])];
    return { normal: ['proposal:checks', 'checks:ack', 'ack:effect', 'effect:result'],
      invalid: ['proposal:checks', 'checks:ack', 'ack:deny'],
      supervisor: ['proposal:checks', 'checks:review', 'review:fresh', 'fresh:ack', 'ack:effect', 'effect:result'] }[scenario];
  }
  function moduleState(scenario, id, current, evidenceCase = scenario === 'confirmation' ? 'confirmed' : 'missing') {
    if (!usedModules(scenario, evidenceCase).includes(id)) return 'Not used on this path';
    const route = scenarioDetails(scenario, evidenceCase).steps;
    if (current === route.length - 1) return 'Completed';
    if (stepModule(route[current]) === id) return stageState(scenario, current, current, evidenceCase);
    return route.some((step, index) => index > current && stepModule(step) === id) ? 'Waiting' : 'Completed';
  }

  function stepDetails(scenario, id, evidenceCase = scenario === 'confirmation' ? 'confirmed' : 'missing') {
    const result = { ...definitions[id] };
    if (scenario === 'confirmation') {
      if (id === 'proposal') result.action = 'Propose with the registered immutable user context; its reference is not consent';
      if (id === 'checks') result.output = 'User confirmation required';
      if (id === 'confirmation-verify') result.output = confirmationCases[evidenceCase].outcome;
      if (id === 'fresh') result.action = 'Recheck policy, inputs, facts and subject; consume the one-use confirmation with CAS';
      return result;
    }
    if (scenario === 'evidence') {
      if (id === 'proposal') {
        result.action = 'Ask the external Evidence Provider to verify the purchase before requesting the action';
        result.output = 'Verification request: case, revision and proposed inputs';
      }
      if (id === 'proof') {
        result.output = evidenceCases[evidenceCase].proof;
        if (evidenceCase === 'changed') {
          result.action = 'The external provider previously returned expiring proof for r7 to the agent';
        }
      }
      if (id === 'present') {
        if (evidenceCase === 'missing') {
          result.action = 'Attempt the action without proof; this illustrates a request the gateway must reject';
          result.output = 'Action request without required evidence';
        } else if (evidenceCase === 'changed') {
          result.action = 'Present the revised action for r8 with the previously returned r7 proof';
          result.output = 'New inputs plus old proof';
        }
      }
      if (id === 'checks') {
        result.action = 'Check who issued the proof, its expiry and request scope, then apply the rules';
        result.output = evidenceCases[evidenceCase].verdict;
      }
      if (['checks', 'deny'].includes(id)) result.actor = 'Gateway';
      if (id === 'ack') {
        result.actor = 'Shared governance services';
        result.output = 'Authorization recorded before the action';
      }
      if (id === 'effect') result.actor = 'Business system';
      return result;
    }
    if (id === 'checks') result.output = { normal: 'Allow', invalid: 'Deny', supervisor: 'Review required' }[scenario];
    return result;
  }

  function stageState(scenario, index, current, evidenceCase = scenario === 'confirmation' ? 'confirmed' : 'missing') {
    const route = scenarioDetails(scenario, evidenceCase).steps;
    if (index < current || current === route.length - 1) return 'Completed';
    if (index > current) return 'Waiting';
    return decisionStep(route[current]) ? 'Waiting for decision' : 'In progress';
  }

  function actorState(scenario, actor, current, evidenceCase = scenario === 'confirmation' ? 'confirmed' : 'missing') {
    if (actor === 'human' && scenario !== 'supervisor') return 'Not required';
    if (actor === 'user' && scenario !== 'confirmation') return 'Not required';
    if (actor === 'business' && !scenarioDetails(scenario, evidenceCase).steps.includes('effect')) return 'Not involved in write';
    const route = scenarioDetails(scenario, evidenceCase).steps;
    const positions = route.flatMap((id, index) => definitions[id].actors.includes(actor) ? [index] : []);
    if (current === route.length - 1) return 'Completed';
    if (positions.includes(current)) return stageState(scenario, current, current, evidenceCase);
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
    const playIcon = root.querySelector('[data-flow-play-icon]');
    const nextLabel = root.querySelector('[data-next-label]');
    const authorityLabel = root.querySelector('[data-authority-label]');
    const authorityDetail = root.querySelector('[data-authority-detail]');
    const panel = root.querySelector('#wf-case');
    const evidencePanel = root.querySelector('[data-flow-evidence]');
    const evidenceChoices = [...root.querySelectorAll('[data-flow-evidence-case]')];
    const evidenceSequence = root.querySelector('[data-evidence-sequence]');
    const evidenceJwt = root.querySelector('[data-evidence-jwt]');
    const businessProof = root.querySelector('[data-flow-proof]');
    const certifiedProof = root.querySelector('[data-proof-certified]');
    const unavailableProof = root.querySelector('[data-proof-unavailable]');
    const approvalPanel = root.querySelector('[data-flow-approval]');
    const approvalState = root.querySelector('[data-approval-state]');
    const confirmationPanel = root.querySelector('[data-flow-confirmation]');
    const confirmationState = root.querySelector('[data-confirmation-state]');
    const confirmationChoices = [...root.querySelectorAll('[data-confirmation-decision]')];
    const voiceLabel = root.querySelector('[data-flow-voice-label]');
    const narrationLabel = root.querySelector('[data-flow-narration]');
    const audioNote = root.querySelector('[data-flow-audio-note]');
    const buttons = Object.fromEntries([...root.querySelectorAll('[data-flow-control]')]
      .map((button) => [button.dataset.flowControl, button]));
    if (!controls.length || !svg || !nodeLayer || !edgeLayer || !mobile || !story || !title ||
        !stateLabel || !actorLabel || !actionLabel || !outputLabel || !caseNote || !progress ||
        !motionNote || !playLabel || !playIcon || !nextLabel || !authorityLabel || !authorityDetail || !panel ||
        !evidencePanel || evidenceChoices.length !== 3 || !evidenceSequence || !evidenceJwt ||
        !businessProof || !certifiedProof || !unavailableProof || !approvalPanel || !approvalState ||
        !confirmationPanel || !confirmationState || confirmationChoices.length !== 4 ||
        !voiceLabel || !narrationLabel || !audioNote ||
        !['play', 'previous', 'next', 'voice'].every((name) => buttons[name])) {
      console.error('Workflow illustration controls are incomplete; the static path remains available.');
      return;
    }
    const actors = [...document.querySelectorAll('#effect-authority [data-action-actor]')];
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let scenario = 'normal';
    let evidenceCase = 'valid';
    let confirmationCase = 'confirmed';
    let step = 0;
    let playing = false;
    let exampleApproved = false;
    let timer = null;
    let voiceOn = true;
    let introPending = true;
    let playbackEpoch = 0;
    let audioWatchdog = null;
    let traveller = null;
    let travelAnimation = null;
    const audio = typeof window.Audio === 'function' ? new window.Audio() : null;
    const script = document.querySelector('script[src*="assets/governed-workflow.js"]');
    const audioRevision = script ? new URL(script.src).search : '';
    if (audio) {
      audio.preload = 'none';
      audio.hidden = true;
      audio.dataset.flowAudio = '';
      root.appendChild(audio);
    } else {
      voiceOn = false;
      audioNote.textContent = 'Narration is not supported in this browser. Silent playback is available.';
    }
    const selectedCase = () => scenario === 'confirmation' ? confirmationCase : evidenceCase;
    const selected = () => scenarioDetails(scenario, selectedCase());

    function stopNarration() {
      playbackEpoch += 1;
      window.clearTimeout(audioWatchdog); audioWatchdog = null;
      if (!audio) return;
      audio.onended = audio.onerror = audio.onplaying = null;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
    function stopMotion() {
      travelAnimation?.cancel(); travelAnimation = null;
      traveller?.remove(); traveller = null;
    }
    function pause() {
      window.clearTimeout(timer); timer = null; playing = false;
      stopNarration(); stopMotion();
    }
    function svgElement(name, attributes, text) {
      const element = document.createElementNS('http://www.w3.org/2000/svg', name);
      Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
      if (text !== undefined) element.textContent = text;
      return element;
    }
    function iconReference(icon) { return icon === 'stop' ? '#wf-icon-stop' : `#production-icon-${icon}`; }

    function animateStep() {
      stopMotion();
      if (!playing || introPending || motion.matches || !svg.getBoundingClientRect().width) return;
      const current = selected().steps[step];
      const edge = scenario === 'evidence'
        ? evidenceSequence.querySelector(`[data-evidence-step="${current}"]:not([hidden]) .wf-edge`)
          || evidenceSequence.querySelector(`[data-evidence-step~="${current}"]:not([data-evidence-participant]):not([hidden]) .wf-edge`)
        : [...edgeLayer.querySelectorAll('[data-on-path="true"]')]
          .find(node => node.dataset.flowEdge.startsWith(`${stepModule(current)}:`));
      if (!edge || typeof edge.getTotalLength !== 'function') return;
      traveller = svgElement('circle', { r: 5, class: 'wf-flow-dot', 'data-flow-traveller': '',
        'aria-hidden': 'true', 'data-edge': edge.closest('[data-evidence-message]')?.dataset.evidenceMessage || edge.dataset.flowEdge || current });
      svg.appendChild(traveller);
      const length = edge.getTotalLength();
      const frames = Array.from({ length: 33 }, (_, index) => {
        const point = edge.getPointAtLength(length * index / 32);
        return { transform: `translate(${point.x}px, ${point.y}px)` };
      });
      travelAnimation = traveller.animate(frames, {
        duration: 900, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'forwards',
      });
    }

    function playNarration() {
      stopNarration();
      const epoch = playbackEpoch;
      const current = introPending ? 'intro' : selected().steps[step];
      const clip = narrationFor(scenario, current, selectedCase());
      const fail = () => {
        if (epoch !== playbackEpoch || !playing) return;
        pause(); voiceOn = false;
        audioNote.textContent = 'Narration unavailable. You can still use silent Play, or turn Voice on to retry.';
        render();
      };
      const beganAt = window.performance.now();
      let observedTime = 0;
      const watchProgress = () => {
        if (epoch !== playbackEpoch || !playing) return;
        const elapsed = window.performance.now() - beganAt;
        if (elapsed >= 90000 || audio.currentTime <= observedTime) { fail(); return; }
        observedTime = audio.currentTime;
        audioWatchdog = window.setTimeout(watchProgress, Math.min(28000, 90000 - elapsed));
      };
      audio.src = `assets/audio/governance/${clip.id}.mp3${audioRevision}`;
      let animated = false;
      audio.onplaying = () => {
        if (epoch !== playbackEpoch || !playing || animated) return;
        animated = true; animateStep();
      };
      audio.onerror = fail;
      audio.onended = () => {
        if (epoch !== playbackEpoch || !playing) return;
        stopNarration();
        if (current === 'intro') {
          introPending = false; render(); schedule(); return;
        }
        if (decisionStep(current) || step === selected().steps.length - 1) {
          pause(); render(); return;
        }
        advance(); schedule();
      };
      audioWatchdog = window.setTimeout(watchProgress, 28000);
      audio.play().catch(fail);
    }

    function buildPath() {
      const route = selected().steps;
      svg.setAttribute('viewBox', scenario === 'evidence' ? '0 0 1100 350' : '0 0 1100 215');
      nodeLayer.toggleAttribute('hidden', scenario === 'evidence');
      edgeLayer.toggleAttribute('hidden', scenario === 'evidence');
      evidenceSequence.toggleAttribute('hidden', scenario !== 'evidence');
      svg.querySelector('[data-proof-return-label]').textContent = evidenceCase === 'missing'
        ? 'No proof issued' : 'Expiring proof';
      svg.querySelector('[data-proof-call-label]').textContent = evidenceCase === 'missing'
        ? 'Action without proof' : evidenceCase === 'changed' ? 'Changed action + proof' : 'Action + proof';
      svg.querySelector('[data-proof-check-label]').textContent = evidenceCase === 'valid'
        ? 'Proof, rules + audit' : evidenceCase === 'missing' ? 'Required proof is missing' : 'Proof does not match';
      svg.querySelector('[data-proof-agent-label]').textContent = evidenceCase === 'missing'
        ? 'Requests action, no proof' : evidenceCase === 'changed' ? 'Uses proof for old request' : 'Presents action + proof';
      svg.querySelector('[data-proof-business-label]').textContent = evidenceCase === 'valid'
        ? 'Executes the operation' : 'Not called';
      svg.querySelector('desc').textContent = `${selected().story} ${route.map((id) => {
        const detail = stepDetails(scenario, id, selectedCase());
        return `${detail.actor}: ${detail.output}.`;
      }).join(' ')} ${selected().note}`;
      mobile.replaceChildren(); progress.replaceChildren();
      const mobileOrder = scenario === 'evidence' ? [...new Set(route.map(stepModule))] : moduleOrder;
      const exchanges = {
        proposal: '1. Agent → Evidence Provider', proof: '2. Evidence Provider → Agent',
        present: '3. Agent → Gateway', checks: 'Gateway checks proof + rules',
        ack: evidenceCase === 'valid' ? 'Authorization recorded' : 'Record denial audit',
        effect: '4. Gateway → Business system', result: 'Case updated + audit recorded', deny: 'No business effect',
      };
      mobileOrder.forEach(id => {
        if (['proof', 'present'].includes(id) && scenario !== 'evidence') return;
        if (id === 'confirm' && scenario !== 'confirmation') return;
        if (id === 'review' && scenario === 'confirmation') return;
        if (scenario === 'evidence' && ['review', 'fresh'].includes(id)) return;
        const item = document.createElement('li');
        item.dataset.mobileNode = id;
        const detail = definitions[id];
        const icon = svgElement('svg', { class: 'wf-icon', 'aria-hidden': 'true' });
        icon.appendChild(svgElement('use', { href: iconReference(detail.icon) }));
        const copy = document.createElement('div');
        const actor = document.createElement('strong');
        actor.textContent = scenario === 'evidence' ? exchanges[id] : moduleLabels[id];
        const state = document.createElement('small');
        state.setAttribute('data-node-state', '');
        const next = document.createElement('span');
        next.className = 'wf-mobile-next';
        if (scenario === 'evidence') {
          next.textContent = id === 'effect' ? 'Check current data, then execute the case update.'
            : id === 'present' ? (evidenceCase === 'missing' ? 'The action is attempted without the required proof.'
              : 'Present the action and its expiring proof.')
              : id === 'proposal' ? 'Check the purchase against trusted records.'
                : id === 'proof' ? evidenceCases[evidenceCase].proof : '';
        } else next.setAttribute('data-mobile-next', '');
        copy.append(actor, state, next); item.append(icon, copy); mobile.appendChild(item);
      });
      route.forEach((id, index) => {
        const detail = stepDetails(scenario, id, selectedCase());
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
      const route = selected().steps;
      const current = route[step];
      const detail = stepDetails(scenario, current, selectedCase());
      const finished = step === route.length - 1;
      root.dataset.step = String(step); root.dataset.node = current;
      root.dataset.playing = String(playing); root.dataset.scenario = scenario;
      root.dataset.narrationPhase = introPending ? 'intro' : 'step';
      root.dataset.evidenceCase = evidenceCase;
      root.dataset.confirmationCase = confirmationCase;
      evidencePanel.hidden = scenario !== 'evidence';
      evidenceJwt.hidden = scenario !== 'evidence';
      businessProof.hidden = scenario !== 'evidence';
      certifiedProof.hidden = evidenceCase === 'missing';
      unavailableProof.hidden = evidenceCase !== 'missing';
      approvalPanel.hidden = scenario !== 'supervisor';
      confirmationPanel.hidden = scenario !== 'confirmation';
      confirmationState.textContent = current === 'confirm' ? 'Paused: choose an illustrative user outcome.'
        : current === 'result' ? 'Completed; replay cannot create another effect.'
          : exampleApproved ? confirmationCases[confirmationCase].outcome
            : 'No confirmation yet. Play or step to the user decision.';
      confirmationChoices.forEach(button => { button.disabled = current !== 'confirm'; });
      approvalState.textContent = current === 'result' ? 'Completed — replay returns the recorded result'
        : ['fresh', 'ack', 'effect'].includes(current) ? 'Consumed — cannot authorize another write'
          : current === 'verify' ? 'Granted — same 10:15 deadline' : 'Not issued — waiting for the person';
      evidenceChoices.forEach(choice => { choice.checked = choice.value === evidenceCase; });
      evidenceSequence.querySelectorAll('[data-evidence-step]').forEach(node => {
        const steps = node.dataset.evidenceStep.split(' ');
        const used = steps.some(id => route.includes(id));
        node.dataset.used = String(used);
        node.toggleAttribute('hidden', !used && !node.hasAttribute('data-evidence-participant'));
        if (steps.includes(current)) node.setAttribute('aria-current', 'step');
        else node.removeAttribute('aria-current');
      });
      story.textContent = selected().story;
      title.textContent = introPending ? 'Illustration · The case' : `Illustration · ${step + 1} / ${route.length}`;
      stateLabel.textContent = stageState(scenario, step, step, selectedCase());
      actorLabel.textContent = detail.actor; actionLabel.textContent = detail.action; outputLabel.textContent = detail.output;
      caseNote.textContent = selected().note;
      panel.setAttribute('aria-labelledby', `wf-tab-${scenario}`);
      tabs.forEach((tab) => {
        const active = tab.dataset.flowScenario === scenario;
        tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
      });
      for (const [selector, key] of [['[data-flow-node]', 'flowNode'], ['[data-mobile-node]', 'mobileNode']]) {
        root.querySelectorAll(selector).forEach((node) => {
          const id = node.dataset[key];
          if (['proof', 'present'].includes(id)) node.toggleAttribute('hidden', scenario !== 'evidence');
          if (id === 'confirm') node.toggleAttribute('hidden', scenario !== 'confirmation');
          if (id === 'review') node.toggleAttribute('hidden', scenario === 'confirmation');
          const used = usedModules(scenario, selectedCase()).includes(id);
          const state = moduleState(scenario, id, step, selectedCase());
          node.dataset.used = String(used);
          node.dataset.stageState = state;
          node.setAttribute('aria-label', `${node.querySelector('strong')?.textContent || moduleLabels[id]}: ${state}`);
          const stateText = node.querySelector('[data-node-state]');
          if (stateText) stateText.textContent = state;
          const number = node.querySelector('[data-flow-number]');
          if (number) number.textContent = used ? String(usedModules(scenario, selectedCase()).indexOf(id) + 1) : '';
          const next = node.querySelector('[data-mobile-next]');
          if (next) {
            const edge = permittedEdges(scenario, selectedCase()).find(edge => edge.startsWith(`${id}:`));
            next.hidden = !edge;
            next.textContent = edge ? `Next: ${moduleLabels[edge.split(':')[1]]}` : '';
          }
          if (stepModule(current) === id) node.setAttribute('aria-current', 'step');
          else node.removeAttribute('aria-current');
        });
      }
      edgeLayer.querySelectorAll('[data-flow-edge]').forEach(edge => {
        const used = permittedEdges(scenario, selectedCase()).includes(edge.dataset.flowEdge) &&
          edge.dataset.flowEdge.split(':').every(id => usedModules(scenario, selectedCase()).includes(id));
        edge.dataset.onPath = String(used);
        edge.toggleAttribute('hidden', !used);
        if (used) edge.setAttribute('marker-end', 'url(#wf-arrow)');
        else edge.removeAttribute('marker-end');
      });
      const denied = !route.includes('effect');
      authorityLabel.textContent = denied ? 'Denial audit' : scenario === 'confirmation' ? 'User + audit'
        : scenario === 'supervisor' ? 'Review + audit' : 'Audit confirmed';
      authorityDetail.textContent = denied ? 'Before blocked return' : scenario === 'supervisor'
        ? ({ pending: 'Pending request', verify: 'One-use grant', ack: 'Authorization ACK' }[current] || 'Authority + ACK')
        : 'Before the effect';
      actors.forEach((actor) => {
        const status = actor.querySelector('[data-actor-state]');
        if (status) status.textContent = actorState(scenario, actor.dataset.actionActor, step, selectedCase());
      });
      const decisionIndex = route.findIndex(decisionStep);
      progress.querySelectorAll('button').forEach((button, index) => {
        const state = stageState(scenario, index, step, selectedCase());
        button.querySelector('[data-progress-state]').textContent = state === 'Waiting for decision' ? 'Waiting' : state;
        button.disabled = decisionIndex >= 0 && index > decisionIndex && !exampleApproved;
        if (index === step) button.setAttribute('aria-current', 'step');
        else button.removeAttribute('aria-current');
      });
      playLabel.textContent = playing ? 'Pause' : finished ? 'Replay' : 'Play';
      const icon = playing ? 'pause' : finished ? 'replay' : 'play';
      playIcon.dataset.icon = icon;
      playIcon.querySelector('path').setAttribute('d', {
        play: 'M8 5 19 12 8 19Z',
        pause: 'M8 5V19M16 5V19',
        replay: 'M3 11a9 9 0 1 1 2 7M3 4V11H10',
      }[icon]);
      buttons.play.disabled = !playing && !finished && ((motion.matches && !voiceOn) || decisionStep(current));
      buttons.voice.disabled = !audio;
      buttons.voice.setAttribute('aria-pressed', String(voiceOn));
      voiceLabel.textContent = voiceOn ? 'Voice on' : 'Voice off';
      narrationLabel.textContent = narrationFor(scenario, introPending ? 'intro' : current, selectedCase()).text;
      buttons.previous.disabled = step === 0; buttons.next.disabled = finished || current === 'confirm';
      buttons.next.setAttribute('aria-label', current === 'review' ? 'Show approved example' : 'Next step');
      nextLabel.hidden = current !== 'review';
      motionNote.textContent = motion.matches ? voiceOn
        ? 'Reduced motion: narration with static step highlights.'
        : 'Reduced motion: use step controls.' : '';
    }

    function moveTo(index) {
      const decisionIndex = selected().steps.findIndex(decisionStep);
      if (decisionIndex >= 0 && index <= decisionIndex) exampleApproved = false;
      introPending = false; step = index; render();
    }
    function advance() {
      introPending = false;
      const route = selected().steps;
      if (decisionStep(route[step]) && !exampleApproved) { pause(); render(); return; }
      if (step < route.length - 1) step += 1;
      if (!voiceOn && (step === route.length - 1 || decisionStep(route[step]))) pause();
      render();
    }
    function schedule() {
      if (!playing) return;
      if (voiceOn) { playNarration(); return; }
      animateStep();
      timer = window.setTimeout(() => { advance(); schedule(); }, STEP_MS);
    }
    buttons.play.addEventListener('click', () => {
      if (playing) { pause(); render(); return; }
      if (step === selected().steps.length - 1) {
        pause(); step = 0; introPending = true; exampleApproved = false; render(); return;
      }
      if ((motion.matches && !voiceOn) || document.hidden || decisionStep(selected().steps[step])) return;
      if (!voiceOn) introPending = false;
      playing = true; render(); schedule();
    });
    buttons.voice.addEventListener('click', () => {
      pause(); voiceOn = !voiceOn; audioNote.textContent = ''; render();
    });
    buttons.previous.addEventListener('click', () => { pause(); moveTo(Math.max(0, step - 1)); });
    buttons.next.addEventListener('click', () => {
      pause();
      if (selected().steps[step] === 'review') exampleApproved = true;
      advance();
    });
    confirmationChoices.forEach(button => button.addEventListener('click', () => {
      const choice = button.dataset.confirmationDecision;
      if (!Object.hasOwn(confirmationCases, choice)) {
        console.error('Unknown confirmation illustration; selection unchanged.');
        return;
      }
      if (scenario !== 'confirmation' || selected().steps[step] !== 'confirm') return;
      pause(); confirmationCase = choice; exampleApproved = true;
      buildPath(); advance(); buttons.next.focus();
    }));
    progress.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-flow-step]');
      if (!button || button.disabled) return;
      pause(); moveTo(Number(button.dataset.flowStep));
    });
    function select(tab) {
      pause(); scenario = tab.dataset.flowScenario; step = 0; introPending = true; exampleApproved = false; confirmationCase = 'confirmed';
      buildPath(); render();
    }
    evidenceChoices.forEach(choice => choice.addEventListener('change', () => {
      if (!Object.hasOwn(evidenceCases, choice.value)) {
        console.error('Unknown evidence illustration; selection unchanged.');
        render();
        return;
      }
      pause(); evidenceCase = choice.value; step = 0; introPending = true; exampleApproved = false;
      buildPath(); render();
    }));
    const selectEvidenceLink = () => {
      if (window.location.hash === '#production-input-proof') {
        select(tabs.find(tab => tab.dataset.flowScenario === 'evidence'));
      } else if (window.location.hash === '#user-confirmation') {
        select(tabs.find(tab => tab.dataset.flowScenario === 'confirmation'));
      }
    };
    window.addEventListener('hashchange', selectEvidenceLink);
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
    if (typeof window.IntersectionObserver === 'function') {
      const visibility = new window.IntersectionObserver(entries => {
        if (entries.some(entry => !entry.isIntersecting) && playing) { pause(); render(); }
      });
      visibility.observe(root);
    }
    const topic = root.closest('[data-topic-panel]');
    if (topic) new MutationObserver(() => { if (topic.hidden) { pause(); render(); } })
      .observe(topic, { attributes: true, attributeFilter: ['hidden'] });
    buildPath(); render(); root.setAttribute('data-enhanced', '');
    controls.forEach((control) => { control.hidden = false; });
    selectEvidenceLink();
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { scenarios, scenarioDetails, legacyRoutes, workbookRoutes, stepDetails, stageState, actorState, usedModules, permittedEdges, moduleState, narrationFor, narrationClips, narrationProfile };
  else if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => mount(document, window));
  else mount(document, window);
})();
