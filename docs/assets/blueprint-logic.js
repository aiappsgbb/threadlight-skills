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
  // Mirror of scripts/build_process_library.py CANON — keep the two in lockstep.
  var CANON = [
    'threadlight-design',
    'threadlight-demo-data-factory',
    'threadlight-local-test',
    'threadlight-hitl-patterns',
    'threadlight-event-triggers',
    'threadlight-connect',
    'threadlight-ground',
    'threadlight-safe-check',
    'threadlight-redteam',
    'threadlight-govern',
    'threadlight-deploy',
    'threadlight-cicd',
    'threadlight-loadtest',
    'threadlight-production-ready',
    'threadlight-evals',
    'threadlight-consumption-iq',
    'threadlight-upgrade',
  ];

  var REGULATED = ['financial_services', 'healthcare', 'pharmaceutical', 'insurance', 'government'];

  function arr(x) { return Array.isArray(x) ? x : []; }
  function label(x) { return (x && (x.name || x.step || x.title || x.type)) || String(x); }

  function deriveSkills(p) {
    p = p || {};
    if (p.playbook && Array.isArray(p.playbook.build_skills)) {
      return CANON.filter(function (s) {
        return p.playbook.build_skills.indexOf(s) !== -1;
      });
    }
    var need = {
      'threadlight-design': 1, 'threadlight-local-test': 1, 'threadlight-safe-check': 1,
      'threadlight-deploy': 1, 'threadlight-cicd': 1, 'threadlight-evals': 1,
    };
    if (arr(p.external_integrations).length) {
      need['threadlight-demo-data-factory'] = 1;
      need['threadlight-connect'] = 1;
    }
    if (arr(p.human_approvals).length) need['threadlight-hitl-patterns'] = 1;
    if (arr(p.knowledge_sources).length) need['threadlight-ground'] = 1;

    var tags = arr(p.tags).map(function (t) { return String(t).toLowerCase(); });
    if (tags.some(function (t) { return /event|trigger|schedul|webhook|cron|real[- ]?time|stream/.test(t); }))
      need['threadlight-event-triggers'] = 1;
    if (tags.some(function (t) { return /high[- ]?volume|throughput|scalab|concurren|latency|load[- ]?test|peak[- ]?load|stress[- ]?test|requests?[- ]?per[- ]?second/.test(t); }))
      need['threadlight-loadtest'] = 1;
    if (tags.some(function (t) { return /preview|deprecat|migration|version[- ]?drift|compatib|upgrade|end[- ]?of[- ]?life/.test(t); }))
      need['threadlight-upgrade'] = 1;

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
    lines.push('Use threadlight-auto as the planner for "' + (p.name || 'this process') +
      '": derive a starter lifecycle for a working pilot on Microsoft Foundry.');
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
    lines.push('Start from this Threadlight lifecycle: ' + skills.join(' → ') + '.');
    lines.push('The coding agent executes eligible steps. Keep manual, live, cost-bearing and production handoffs explicit; do not infer readiness from a generated plan.');
    lines.push('Follow each skill\'s SKILL.md, keep the platform (Foundry) as the runtime, ' +
      'and produce the committed artefacts each leg leaves behind.');
    lines.push('Prepare production delivery through CI/CD (GitHub Actions + OIDC), not my laptop. ' +
      'Execution requires explicit approval, environment setup and current evidence.');
    return lines.join('\n');
  }

  // Proposed work is derived from the starter signals, not proof that it ran.
  function buildAutomation(p) {
    p = p || {};
    var skills = deriveSkills(p);
    function has(s) { return skills.indexOf(s) !== -1; }
    var steps = [];
    steps.push({ text: 'Drafts the Microsoft Foundry pilot design and a local validation plan.' });
    if (has('threadlight-demo-data-factory'))
      steps.push({ text: 'Scaffolds integration mocks; real endpoints require separate evidence and approval.' });
    if (has('threadlight-hitl-patterns'))
      steps.push({ text: 'Plans the human-approval gates you named; their implementation and enforcement need evidence.' });
    if (has('threadlight-event-triggers'))
      steps.push({ text: 'Plans event and schedule triggers for the selected deployment path.' });
    var safe = 'Requests structural checks';
    if (has('threadlight-redteam')) safe += ' and red-team evidence';
    if (has('threadlight-govern')) safe += '; selected runtime governance requires binding-scoped proof';
    steps.push({ text: safe + '.' });
    steps.push({
      text: 'Prepares CI/CD delivery (GitHub Actions + OIDC), with explicit approval and environment setup before execution.',
      accent: true,
    });
    var score = 'Plans quality evals';
    if (has('threadlight-consumption-iq')) score += ' and a cost forecast; measured actuals need later reconciliation';
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
