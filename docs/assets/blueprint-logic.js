/* Threadlight Blueprint — pure derivation logic (no DOM).
 * UMD: usable from the browser (window.TL_BLUEPRINT) and node (require).
 * Skills are DERIVED from process fields, never hardcoded per template. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.TL_BLUEPRINT = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // The always-on spine, shown as the pipeline arc on the page.
  var PIPELINE_ARC = [
    'threadlight-design', 'threadlight-local-test', 'threadlight-safe-check',
    'threadlight-deploy', 'threadlight-cicd', 'threadlight-evals',
  ];

  // Canonical order every derived sequence is filtered through — guarantees a
  // deterministic, sensibly-ordered, de-duplicated arc regardless of input order.
  var CANON = [
    'threadlight-design',
    'threadlight-demo-data-factory',
    'threadlight-local-test',
    'threadlight-hitl-patterns',
    'threadlight-event-triggers',
    'threadlight-safe-check',
    'threadlight-redteam',
    'threadlight-govern',
    'threadlight-deploy',
    'threadlight-cicd',
    'threadlight-production-ready',
    'threadlight-evals',
    'threadlight-consumption-iq',
  ];

  var REGULATED = ['financial_services', 'healthcare', 'pharmaceutical', 'insurance', 'government'];

  function arr(x) { return Array.isArray(x) ? x : []; }
  function label(x) { return (x && (x.name || x.step || x.title || x.type)) || String(x); }

  function deriveSkills(p) {
    p = p || {};
    var need = {
      'threadlight-design': 1, 'threadlight-local-test': 1, 'threadlight-safe-check': 1,
      'threadlight-deploy': 1, 'threadlight-cicd': 1, 'threadlight-evals': 1,
    };
    if (arr(p.external_integrations).length) need['threadlight-demo-data-factory'] = 1;
    if (arr(p.human_approvals).length) need['threadlight-hitl-patterns'] = 1;

    var tags = arr(p.tags).map(function (t) { return String(t).toLowerCase(); });
    if (tags.some(function (t) { return /event|trigger|schedul|webhook|cron|real[- ]?time|stream/.test(t); }))
      need['threadlight-event-triggers'] = 1;

    if (p.complexity === 'high') {
      need['threadlight-production-ready'] = 1;
      need['threadlight-govern'] = 1;
      need['threadlight-redteam'] = 1;
    }

    var regTag = tags.some(function (t) { return /regulat|complian|hipaa|gdpr|sox|pci|audit/.test(t); });
    if (REGULATED.indexOf(p.industry) !== -1 || regTag) need['threadlight-consumption-iq'] = 1;

    return CANON.filter(function (s) { return need[s]; });
  }

  function prettyIndustry(s) {
    return String(s || '').replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  // Read a scenario id from a URL query string (e.g. "?s=commercial-loan-origination").
  // Sanitised to a kebab/underscore slug charset so a deep-link can never carry
  // markup or a path-traversal payload into the composer. Returns '' when absent.
  function parseScenarioParam(search) {
    var q = String(search || '');
    var i = q.indexOf('?');
    if (i !== -1) q = q.slice(i + 1);
    var m = /(?:^|&)s=([^&]*)/.exec(q);
    if (!m) return '';
    var v;
    try { v = decodeURIComponent(m[1].replace(/\+/g, ' ')); } catch (e) { v = m[1]; }
    return v.replace(/[^A-Za-z0-9_-]/g, '');
  }

  function buildPrompt(p) {
    p = p || {};
    var skills = deriveSkills(p);
    var lines = [];
    lines.push('Use threadlight-auto to take "' + (p.name || 'this process') +
      '" from idea to a production-ready Azure AI Foundry agent.');
    lines.push('');
    if (p.summary) lines.push('What it does: ' + p.summary);
    lines.push('Domain: ' + prettyIndustry(p.industry) + ' · Complexity: ' + (p.complexity || 'medium'));

    var ints = arr(p.external_integrations).map(label);
    if (ints.length) lines.push('Integrations to wire: ' + ints.join(', ') + '.');
    var apps = arr(p.human_approvals).map(label);
    if (apps.length) lines.push('Human approval gates (stop and wait at each): ' + apps.join(', ') + '.');
    var ks = arr(p.knowledge_sources).map(label);
    if (ks.length) lines.push('Ground it on: ' + ks.join(', ') + '.');

    lines.push('');
    lines.push('Run the Threadlight arc in order: ' + skills.join(' → ') + '.');
    lines.push('Follow each skill\'s SKILL.md, keep the platform (Foundry) as the runtime, ' +
      'and produce the committed artefacts each leg leaves behind.');
    lines.push('Deploy through CI/CD (GitHub Actions + OIDC) — provision and ship from the ' +
      'pipeline, not my laptop.');
    return lines.join('\n');
  }

  // What Copilot does once the prompt is pasted — DERIVED from the same process
  // signals as the arc, so it always matches. The whole point: the human runs no
  // commands. Deploy is a CI/CD outcome, never a laptop `azd up`. Each entry is
  // { text, accent? } — accent marks the deploy line for emphasis on the page.
  function buildAutomation(p) {
    p = p || {};
    var skills = deriveSkills(p);
    function has(s) { return skills.indexOf(s) !== -1; }
    var steps = [];
    steps.push({ text: 'Designs the agent on Azure AI Foundry and proves it locally on real cases.' });
    if (has('threadlight-demo-data-factory'))
      steps.push({ text: 'Wires your integrations behind a realistic demo-data harness.' });
    if (has('threadlight-hitl-patterns'))
      steps.push({ text: 'Adds the human-approval gates you named — it stops and waits at each.' });
    if (has('threadlight-event-triggers'))
      steps.push({ text: 'Stands up your event & schedule triggers.' });
    var safe = 'Safe-checks every change';
    if (has('threadlight-redteam')) safe += ', red-teams it';
    if (has('threadlight-govern')) safe += ' and governs it at runtime';
    steps.push({ text: safe + '.' });
    steps.push({
      text: 'Ships to Azure through your CI/CD pipeline (GitHub Actions + OIDC) — ' +
        'you never run a deploy command.',
      accent: true,
    });
    var score = 'Scores it with evals';
    if (has('threadlight-consumption-iq')) score += ' and prices every run';
    steps.push({ text: score + '.' });
    return steps;
  }

  return {
    PIPELINE_ARC: PIPELINE_ARC,
    CANON: CANON,
    deriveSkills: deriveSkills,
    buildPrompt: buildPrompt,
    buildAutomation: buildAutomation,
    prettyIndustry: prettyIndustry,
    parseScenarioParam: parseScenarioParam,
  };
}));
