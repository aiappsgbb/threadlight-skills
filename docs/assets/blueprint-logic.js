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
    'threadlight-deploy', 'threadlight-evals',
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
      'threadlight-deploy': 1, 'threadlight-evals': 1,
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
    return lines.join('\n');
  }

  function buildAzd(p) {
    p = p || {};
    var slug = String(p.id || p.name || 'agent').toLowerCase()
      .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'agent';
    return [
      '# Provision Azure + deploy the generated agent',
      'azd auth login',
      'azd init --template ./' + slug,
      'azd up',
      '',
      '# Then run the evals leg to score it',
      'threadlight-evals',
    ].join('\n');
  }

  return {
    PIPELINE_ARC: PIPELINE_ARC,
    CANON: CANON,
    deriveSkills: deriveSkills,
    buildPrompt: buildPrompt,
    buildAzd: buildAzd,
    prettyIndustry: prettyIndustry,
  };
}));
