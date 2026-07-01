/* ============================================================
   Threadlight — Scenario & Architecture Explorer
   ------------------------------------------------------------
   Self-contained. Renders the 19 curated library scenarios as a
   searchable / filterable grid, and a per-scenario hub-and-spoke
   architecture sketch. Teaser-level only: system archetypes and
   the governance pattern, never the gated deep-SPEC internals.
   The shared theme (masthead, reveal, floating-toc) comes from
   site.css + site.js; this file only drives the explorer UI.
   ============================================================ */
(function () {
  'use strict';

  // --- spoke kinds: colour + legend name (fills defined in explorer.css) ---
  var KIND = {
    system:    { name: 'System of record', cls: 'k-system' },
    knowledge: { name: 'Grounding',        cls: 'k-knowledge' },
    tool:      { name: 'Tool / skill',     cls: 'k-tool' },
    hitl:      { name: 'Human gate',        cls: 'k-hitl' },
    gov:       { name: 'Governance',        cls: 'k-gov' }
  };
  var KIND_ORDER = ['system', 'knowledge', 'tool', 'hitl', 'gov'];

  // --- sectors: display label + lucide sprite icon id ---
  var SECTORS = [
    { id: 'FSI',           label: 'Financial services', icon: 'landmark' },
    { id: 'Retail',        label: 'Retail',             icon: 'shopping-cart' },
    { id: 'Telco',         label: 'Telco',              icon: 'radio-tower' },
    { id: 'Manufacturing', label: 'Manufacturing',      icon: 'factory' },
    { id: 'Healthcare',    label: 'Healthcare',         icon: 'heart-pulse' },
    { id: 'Energy',        label: 'Energy & utilities', icon: 'zap' }
  ];
  var SECTOR_META = {};
  SECTORS.forEach(function (s) { SECTOR_META[s.id] = s; });

  // Canonical trait order for the filter row.
  var TRAITS = ['Regulated', 'Multi-system', 'Grounded', 'Multimodal',
    'Time-critical', 'Document-gen', 'Case-based', 'HITL sign-off'];

  // --- the 19 curated scenarios (faithful to industries.html copy) ---
  var SCENARIOS = [
    // FSI ---------------------------------------------------------------
    {
      id: 'fsi-card-dispute', sector: 'FSI', name: 'Card dispute investigation',
      blurb: 'Classify, time Reg E/Z, draft the analyst packet.',
      traits: ['Regulated', 'Case-based', 'Document-gen', 'HITL sign-off'],
      shape: ['Intake the dispute + evidence', 'Ground in Reg E/Z timers & scheme rules', 'Draft the analyst packet → sign-off'],
      hub: 'Dispute agent',
      spokes: [
        { k: 'system', label: 'Cards / core banking' },
        { k: 'system', label: 'Dispute case system' },
        { k: 'knowledge', label: 'Reg E/Z + scheme rules' },
        { k: 'tool', label: 'Evidence packet builder' },
        { k: 'hitl', label: 'Analyst sign-off' },
        { k: 'gov', label: 'Decision audit log' }
      ]
    },
    {
      id: 'fsi-fnol', sector: 'FSI', name: 'Insurance FNOL triage',
      blurb: 'Voice / web / photo → routed to an adjuster in under 90s.',
      traits: ['Multimodal', 'Time-critical', 'Multi-system', 'HITL sign-off'],
      shape: ['Take voice / web / photo FNOL', 'Classify against coverage + routing rules', 'Route to the right adjuster → review'],
      hub: 'FNOL agent',
      spokes: [
        { k: 'system', label: 'Claims / policy admin' },
        { k: 'knowledge', label: 'Coverage + routing rules' },
        { k: 'tool', label: 'Voice / photo intake' },
        { k: 'tool', label: 'Adjuster router' },
        { k: 'hitl', label: 'Adjuster review' },
        { k: 'gov', label: 'Audit trail' }
      ]
    },
    {
      id: 'fsi-kyc', sector: 'FSI', name: 'KYC / customer onboarding',
      blurb: 'Structured identity verification with a full audit trail.',
      traits: ['Regulated', 'Case-based', 'Multi-system', 'HITL sign-off'],
      shape: ['Collect identity + documents', 'Verify & screen against KYC/CDD policy', 'Assemble audit trail → compliance sign-off'],
      hub: 'Onboarding agent',
      spokes: [
        { k: 'system', label: 'CRM / customer master' },
        { k: 'system', label: 'Screening / watchlists' },
        { k: 'knowledge', label: 'KYC / CDD policy' },
        { k: 'tool', label: 'ID verification' },
        { k: 'hitl', label: 'Compliance sign-off' },
        { k: 'gov', label: 'Audit trail' }
      ]
    },
    {
      id: 'fsi-credit-memo', sector: 'FSI', name: 'SMB credit memo',
      blurb: 'Bureau + bank statements → a drafted five-page memo.',
      traits: ['Multi-system', 'Document-gen', 'Grounded', 'HITL sign-off'],
      shape: ['Pull bureau + bank statements', 'Ground in the credit policy', 'Draft the memo → credit-officer review'],
      hub: 'Credit-memo agent',
      spokes: [
        { k: 'system', label: 'Bureau feed' },
        { k: 'system', label: 'Bank statements' },
        { k: 'knowledge', label: 'Credit policy' },
        { k: 'tool', label: 'Memo drafter' },
        { k: 'hitl', label: 'Credit-officer review' },
        { k: 'gov', label: 'Citations / provenance' }
      ]
    },
    // Retail ------------------------------------------------------------
    {
      id: 'retail-pim', sector: 'Retail', name: 'PIM catalog enrichment',
      blurb: 'Bulk product copy and attributes from supplier feeds.',
      traits: ['Grounded', 'Document-gen', 'HITL sign-off'],
      shape: ['Read supplier feeds', 'Ground in brand + attribute schema', 'Write copy + attributes → merch QA'],
      hub: 'Catalog agent',
      spokes: [
        { k: 'system', label: 'PIM / product master' },
        { k: 'system', label: 'Supplier feeds' },
        { k: 'knowledge', label: 'Brand + attribute schema' },
        { k: 'tool', label: 'Copy + attribute writer' },
        { k: 'hitl', label: 'Merch QA' },
        { k: 'gov', label: 'Change log' }
      ]
    },
    {
      id: 'retail-promo', sector: 'Retail', name: 'Promo planning copilot',
      blurb: 'Quarterly calendar with cannibalisation and margin signals.',
      traits: ['Grounded', 'Multi-system', 'HITL sign-off'],
      shape: ['Read sales / POS history', 'Model margin + cannibalisation', 'Draft the calendar → category sign-off'],
      hub: 'Promo agent',
      spokes: [
        { k: 'system', label: 'Sales / POS history' },
        { k: 'knowledge', label: 'Margin + cannibal. model' },
        { k: 'tool', label: 'Calendar planner' },
        { k: 'hitl', label: 'Category-mgr sign-off' },
        { k: 'gov', label: 'Assumption log' }
      ]
    },
    {
      id: 'retail-returns', sector: 'Retail', name: 'Returns triage',
      blurb: 'Rule-grounded decisioning, exception lanes, audit-friendly.',
      traits: ['Grounded', 'Case-based', 'HITL sign-off'],
      shape: ['Intake the return', 'Decide against policy + fraud rules', 'Route exceptions → desk review'],
      hub: 'Returns agent',
      spokes: [
        { k: 'system', label: 'Order / returns system' },
        { k: 'knowledge', label: 'Policy + fraud rules' },
        { k: 'tool', label: 'Disposition decider' },
        { k: 'hitl', label: 'Desk exception review' },
        { k: 'gov', label: 'Audit-friendly log' }
      ]
    },
    // Telco -------------------------------------------------------------
    {
      id: 'telco-q2o', sector: 'Telco', name: 'B2B quote-to-order',
      blurb: 'Sales config → a validated order, no swivel-chair.',
      traits: ['Grounded', 'Multi-system', 'HITL sign-off'],
      shape: ['Take the sales config', 'Validate against catalog + price book', 'Write the order → deal-desk approval'],
      hub: 'Quote agent',
      spokes: [
        { k: 'system', label: 'CRM / CPQ' },
        { k: 'knowledge', label: 'Catalog + price book' },
        { k: 'tool', label: 'Config validator' },
        { k: 'tool', label: 'Order writer' },
        { k: 'hitl', label: 'Deal-desk approval' },
        { k: 'gov', label: 'Change log' }
      ]
    },
    {
      id: 'telco-fault', sector: 'Telco', name: 'Network fault triage',
      blurb: 'Alarms across radio / transport / core → a shortlist.',
      traits: ['Multi-system', 'Time-critical', 'HITL sign-off'],
      shape: ['Ingest alarms across domains', 'Correlate vs topology + fault library', 'Shortlist causes → NOC review'],
      hub: 'Fault-triage agent',
      spokes: [
        { k: 'system', label: 'Alarm / EMS' },
        { k: 'knowledge', label: 'Topology + fault library' },
        { k: 'tool', label: 'Correlator / shortlister' },
        { k: 'hitl', label: 'NOC engineer review' },
        { k: 'gov', label: 'Action log' }
      ]
    },
    {
      id: 'telco-fallout', sector: 'Telco', name: 'Order fallout resolution',
      blurb: 'Detect stalls, draft fixes, re-flight when ready.',
      traits: ['Multi-system', 'Time-critical', 'Case-based', 'HITL sign-off'],
      shape: ['Detect stalled orders', 'Diagnose across OMS / CRM / billing', 'Draft fixes → ops approval → re-flight'],
      hub: 'Fallout agent',
      spokes: [
        { k: 'system', label: 'OMS / CRM / billing' },
        { k: 'system', label: 'Inventory / ticketing' },
        { k: 'knowledge', label: 'Fallout playbooks' },
        { k: 'tool', label: 'Fix drafter + re-flight' },
        { k: 'hitl', label: 'Ops approval' },
        { k: 'gov', label: 'Case audit' }
      ]
    },
    // Manufacturing -----------------------------------------------------
    {
      id: 'mfg-eng-knowledge', sector: 'Manufacturing', name: 'Engineering knowledge copilot',
      blurb: 'Grounded answers from drawings, BOMs, and procedures.',
      traits: ['Grounded', 'Multimodal', 'HITL sign-off'],
      shape: ['Take the engineer\u2019s question', 'Ground in drawings · BOMs · procedures', 'Answer with citations → engineer verify'],
      hub: 'Eng-knowledge agent',
      spokes: [
        { k: 'system', label: 'PLM / doc store' },
        { k: 'knowledge', label: 'Drawings · BOMs · SOPs' },
        { k: 'tool', label: 'Grounded retrieval' },
        { k: 'hitl', label: 'Engineer verify' },
        { k: 'gov', label: 'Source citations' }
      ]
    },
    {
      id: 'mfg-handover', sector: 'Manufacturing', name: 'Shift handover briefing',
      blurb: 'OEE, short-stops, maintenance, safety — one structured brief.',
      traits: ['Multi-system', 'Document-gen', 'HITL sign-off'],
      shape: ['Pull MES + maintenance + safety', 'Summarise the shift', 'Compose the brief → lead review'],
      hub: 'Handover agent',
      spokes: [
        { k: 'system', label: 'MES / historian' },
        { k: 'system', label: 'CMMS / safety log' },
        { k: 'knowledge', label: 'Handover template' },
        { k: 'tool', label: 'Brief composer' },
        { k: 'hitl', label: 'Outgoing-lead review' },
        { k: 'gov', label: 'Shift record' }
      ]
    },
    {
      id: 'mfg-supplier-risk', sector: 'Manufacturing', name: 'Supplier risk monitoring',
      blurb: 'Signal triage → mitigation options before lines are exposed.',
      traits: ['Multi-system', 'Case-based', 'HITL sign-off'],
      shape: ['Watch supplier risk signals', 'Triage against thresholds', 'Draft mitigations → procurement review'],
      hub: 'Supplier-risk agent',
      spokes: [
        { k: 'system', label: 'ERP / supplier master' },
        { k: 'knowledge', label: 'Risk signals + thresholds' },
        { k: 'tool', label: 'Signal triage + mitigation' },
        { k: 'hitl', label: 'Procurement review' },
        { k: 'gov', label: 'Case audit' }
      ]
    },
    // Healthcare --------------------------------------------------------
    {
      id: 'hc-referral', sector: 'Healthcare', name: 'Patient referral triage',
      blurb: 'FHIR-aware routing with a care-coordination handoff.',
      traits: ['Regulated', 'Multi-system', 'Case-based', 'HITL sign-off'],
      shape: ['Read the referral (FHIR)', 'Route against care pathways', 'Hand off to coordination → clinician review'],
      hub: 'Referral agent',
      spokes: [
        { k: 'system', label: 'EHR (FHIR)' },
        { k: 'knowledge', label: 'Referral + care pathways' },
        { k: 'tool', label: 'Router + handoff' },
        { k: 'hitl', label: 'Clinician review' },
        { k: 'gov', label: 'Audit trail' }
      ]
    },
    {
      id: 'hc-adverse-event', sector: 'Healthcare', name: 'Adverse event detection',
      blurb: 'Case-based ADE monitoring with MedDRA + CTCAE coding.',
      traits: ['Regulated', 'Case-based', 'Grounded', 'HITL sign-off'],
      shape: ['Detect the adverse event', 'Code with MedDRA / CTCAE', 'Assemble the case → safety-physician review'],
      hub: 'Pharmacovigilance agent',
      spokes: [
        { k: 'system', label: 'Safety case system' },
        { k: 'knowledge', label: 'MedDRA / CTCAE' },
        { k: 'tool', label: 'Case detector + coder' },
        { k: 'hitl', label: 'Safety-physician review' },
        { k: 'gov', label: 'Regulatory audit' }
      ]
    },
    {
      id: 'hc-protocol', sector: 'Healthcare', name: 'Clinical trial protocol review',
      blurb: 'Compress a multi-week review to same-day blocker triage.',
      traits: ['Regulated', 'Document-gen', 'Grounded', 'HITL sign-off'],
      shape: ['Read the protocol', 'Check against GCP + standards', 'Triage blockers + redline → medical sign-off'],
      hub: 'Protocol-review agent',
      spokes: [
        { k: 'system', label: 'eTMF / doc store' },
        { k: 'knowledge', label: 'GCP + protocol standards' },
        { k: 'tool', label: 'Blocker triage + redline' },
        { k: 'hitl', label: 'Medical reviewer sign-off' },
        { k: 'gov', label: 'Review log' }
      ]
    },
    // Energy & utilities ------------------------------------------------
    {
      id: 'energy-outage', sector: 'Energy', name: 'Grid outage response',
      blurb: 'Correlated SCADA + customer signals → a response playbook.',
      traits: ['Multi-system', 'Time-critical', 'HITL sign-off'],
      shape: ['Correlate SCADA + customer signals', 'Match to a response playbook', 'Draft the response → control-room approval'],
      hub: 'Outage agent',
      spokes: [
        { k: 'system', label: 'SCADA / OMS' },
        { k: 'system', label: 'Customer / comms signals' },
        { k: 'knowledge', label: 'Response playbooks' },
        { k: 'tool', label: 'Correlator + selector' },
        { k: 'hitl', label: 'Control-room approval' },
        { k: 'gov', label: 'Event log' }
      ]
    },
    {
      id: 'energy-filing', sector: 'Energy', name: 'Regulatory compliance filing',
      blurb: 'Assemble FERC / NERC / state-utility filing packages.',
      traits: ['Regulated', 'Document-gen', 'Multi-system', 'HITL sign-off'],
      shape: ['Gather operational + financial data', 'Ground in FERC / NERC / state rules', 'Assemble the package → compliance sign-off'],
      hub: 'Filing agent',
      spokes: [
        { k: 'system', label: 'Operational systems' },
        { k: 'system', label: 'Financial systems' },
        { k: 'knowledge', label: 'FERC / NERC / state rules' },
        { k: 'tool', label: 'Package assembler' },
        { k: 'hitl', label: 'Compliance sign-off' },
        { k: 'gov', label: 'Provenance' }
      ]
    },
    {
      id: 'energy-renewable', sector: 'Energy', name: 'Renewable asset performance',
      blurb: 'Benchmark wind / solar sites against weather + maintenance.',
      traits: ['Grounded', 'Multi-system', 'HITL sign-off'],
      shape: ['Read SCADA telemetry', 'Benchmark vs weather + maintenance', 'Flag underperformers → asset-manager review'],
      hub: 'Asset-perf agent',
      spokes: [
        { k: 'system', label: 'SCADA / asset telemetry' },
        { k: 'knowledge', label: 'Weather + maint. baselines' },
        { k: 'tool', label: 'Benchmark analyzer' },
        { k: 'hitl', label: 'Asset-manager review' },
        { k: 'gov', label: 'Assumption log' }
      ]
    }
  ];

  // ---------------------------------------------------------------
  // helpers
  // ---------------------------------------------------------------
  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function iconUse(id) {
    return '<svg class="icon" aria-hidden="true"><use href="./assets/lucide-sprite.svg#icon-' + id + '"/></svg>';
  }

  // Radial hub-and-spoke sketch, generated from a scenario's spokes.
  // labelled=true renders spoke text (modal); false renders a clean
  // node-only thumbnail (card).
  function hubSpokeSVG(sc, w, h, labelled) {
    var cx = w / 2, cy = h / 2;
    var R = Math.min(w, h) * (labelled ? 0.30 : 0.34);
    var n = sc.spokes.length;
    var links = '', nodes = '', labels = '';
    for (var i = 0; i < n; i++) {
      var sp = sc.spokes[i];
      var ang = (-90 + i * (360 / n)) * Math.PI / 180;
      var x = cx + R * Math.cos(ang);
      var y = cy + R * Math.sin(ang);
      var cls = KIND[sp.k].cls;
      links += '<line class="hs-link" x1="' + cx.toFixed(1) + '" y1="' + cy.toFixed(1) +
        '" x2="' + x.toFixed(1) + '" y2="' + y.toFixed(1) + '"/>';
      nodes += '<circle class="hs-node ' + cls + '" cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) +
        '" r="' + (labelled ? 8 : 6) + '"/>';
      if (labelled) {
        var anchor = x < cx - 6 ? 'end' : (x > cx + 6 ? 'start' : 'middle');
        var dx = anchor === 'end' ? -13 : anchor === 'start' ? 13 : 0;
        var dy;
        if (anchor === 'middle') { dy = y < cy ? -14 : 22; }
        else { dy = 4; }
        labels += '<text class="hs-label" x="' + (x + dx).toFixed(1) + '" y="' + (y + dy).toFixed(1) +
          '" text-anchor="' + anchor + '">' + esc(sp.label) + '</text>';
      }
    }
    var hubW = labelled ? 108 : 66, hubH = labelled ? 40 : 28;
    var hub = '<g class="hs-hub">' +
      '<rect x="' + (cx - hubW / 2).toFixed(1) + '" y="' + (cy - hubH / 2).toFixed(1) +
      '" width="' + hubW + '" height="' + hubH + '" rx="' + (hubH / 2.6).toFixed(1) + '"/>' +
      '<text x="' + cx.toFixed(1) + '" y="' + (cy + 4).toFixed(1) + '" text-anchor="middle">' +
      esc(labelled ? sc.hub : 'agent') + '</text></g>';
    return '<svg class="hs-svg' + (labelled ? ' hs-svg-lg' : '') + '" viewBox="0 0 ' + w + ' ' + h +
      '" role="img" aria-label="' + esc(sc.name + ' — governed agent connecting ' + n + ' services') +
      '" preserveAspectRatio="xMidYMid meet">' + links + hub + nodes + labels + '</svg>';
  }

  function kindLegend() {
    var out = '<ul class="hs-legend" aria-label="Diagram legend">';
    KIND_ORDER.forEach(function (k) {
      out += '<li><span class="hs-swatch ' + KIND[k].cls + '"></span>' + esc(KIND[k].name) + '</li>';
    });
    return out + '</ul>';
  }

  function traitChips(traits, small) {
    return traits.map(function (t) {
      return '<span class="exp-chip exp-chip-trait' + (small ? ' is-sm' : '') + '">' + esc(t) + '</span>';
    }).join('');
  }

  // ---------------------------------------------------------------
  // card + grid render
  // ---------------------------------------------------------------
  function cardHTML(sc) {
    var sm = SECTOR_META[sc.sector];
    return '<article class="exp-card" tabindex="0" role="button" ' +
      'aria-label="Open architecture for ' + esc(sc.name) + '" data-id="' + esc(sc.id) + '">' +
      '<p class="exp-card-sector">' + iconUse(sm.icon) + '<span>' + esc(sm.label) + '</span></p>' +
      '<h3 class="exp-card-name">' + esc(sc.name) + '</h3>' +
      '<p class="exp-card-blurb">' + esc(sc.blurb) + '</p>' +
      '<div class="exp-card-diagram">' + hubSpokeSVG(sc, 240, 150, false) + '</div>' +
      '<div class="exp-card-traits">' + traitChips(sc.traits.slice(0, 3), true) + '</div>' +
      '<p class="exp-card-cta">View architecture <span class="arrow">→</span></p>' +
      '</article>';
  }

  var state = { sector: 'all', traits: {}, q: '' };
  var gridEl, countEl, emptyEl;

  function matches(sc) {
    if (state.sector !== 'all' && sc.sector !== state.sector) return false;
    var active = Object.keys(state.traits).filter(function (t) { return state.traits[t]; });
    for (var i = 0; i < active.length; i++) {
      if (sc.traits.indexOf(active[i]) === -1) return false;
    }
    if (state.q) {
      var hay = (sc.name + ' ' + sc.blurb + ' ' + SECTOR_META[sc.sector].label + ' ' +
        sc.traits.join(' ') + ' ' + sc.spokes.map(function (s) { return s.label; }).join(' ')).toLowerCase();
      if (hay.indexOf(state.q) === -1) return false;
    }
    return true;
  }

  function renderGrid() {
    var list = SCENARIOS.filter(matches);
    gridEl.innerHTML = list.map(cardHTML).join('');
    countEl.textContent = list.length;
    emptyEl.hidden = list.length !== 0;
    gridEl.hidden = list.length === 0;
  }

  // ---------------------------------------------------------------
  // modal
  // ---------------------------------------------------------------
  var modal, modalBody, lastFocus, inerted = [];

  // While the dialog is open, make the rest of the page inert so Tab and
  // AT can't reach controls behind the overlay (and background chips can't
  // be activated, which would re-render the grid and detach lastFocus).
  function setBackgroundInert(on) {
    if (on) {
      var kids = document.body.children;
      for (var i = 0; i < kids.length; i++) {
        var el = kids[i];
        if (el === modal || el.tagName === 'SCRIPT') continue;
        if (!el.hasAttribute('inert')) { el.setAttribute('inert', ''); inerted.push(el); }
      }
    } else {
      inerted.forEach(function (el) { el.removeAttribute('inert'); });
      inerted = [];
    }
  }

  function openModal(id) {
    var sc = SCENARIOS.find(function (s) { return s.id === id; });
    if (!sc) return;
    var sm = SECTOR_META[sc.sector];
    modalBody.innerHTML =
      '<button class="exp-modal-close" type="button" aria-label="Close">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>' +
      '</button>' +
      '<p class="exp-modal-sector">' + iconUse(sm.icon) + '<span>' + esc(sm.label) + '</span></p>' +
      '<h2 class="exp-modal-name" id="exp-modal-title">' + esc(sc.name) + '</h2>' +
      '<p class="exp-modal-blurb">' + esc(sc.blurb) + '</p>' +
      '<div class="exp-modal-diagram">' + hubSpokeSVG(sc, 560, 380, true) + kindLegend() + '</div>' +
      '<div class="exp-modal-cols">' +
        '<section class="exp-modal-shape"><h3>How the chain runs it</h3><ol>' +
          sc.shape.map(function (b) { return '<li>' + esc(b) + '</li>'; }).join('') +
        '</ol></section>' +
        '<section class="exp-modal-meta"><h3>Traits</h3>' +
          '<div class="exp-card-traits">' + traitChips(sc.traits, false) + '</div>' +
        '</section>' +
      '</div>' +
      '<p class="exp-modal-gate">Diagram is illustrative — a governed agent hub wiring systems of record, ' +
      'grounding, tools, a human gate, and an audit trail. The deep SPEC (data contracts, prompts, eval ' +
      'gates) lives in the Threadlight Library catalog — <strong>ask your Microsoft GBB SE for access</strong>.</p>' +
      '<div class="exp-modal-actions">' +
        '<a class="btn btn-ghost" href="./industries.html#the-library">Back to the sector library <span class="arrow">→</span></a>' +
        '<a class="btn btn-ghost" href="https://github.com/aiappsgbb/threadlight-skills/blob/main/THREADLIGHT.md" target="_blank" rel="noopener">Read THREADLIGHT.md <span class="arrow">→</span></a>' +
      '</div>';

    lastFocus = document.activeElement;
    modal.hidden = false;
    document.body.classList.add('exp-modal-open');
    setBackgroundInert(true);
    var closeBtn = modalBody.querySelector('.exp-modal-close');
    if (closeBtn) closeBtn.focus();
  }

  function closeModal() {
    if (modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove('exp-modal-open');
    setBackgroundInert(false);
    modalBody.innerHTML = '';
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // ---------------------------------------------------------------
  // filter controls
  // ---------------------------------------------------------------
  function buildControls() {
    // sector chips
    var secWrap = document.getElementById('exp-sectors');
    var secHTML = '<button class="exp-chip is-on" data-sector="all" type="button">All sectors</button>';
    SECTORS.forEach(function (s) {
      secHTML += '<button class="exp-chip" data-sector="' + s.id + '" type="button">' +
        iconUse(s.icon) + '<span>' + esc(s.label) + '</span></button>';
    });
    secWrap.innerHTML = secHTML;

    // trait chips
    var trWrap = document.getElementById('exp-traits');
    trWrap.innerHTML = TRAITS.map(function (t) {
      return '<button class="exp-chip exp-chip-trait" data-trait="' + esc(t) + '" type="button">' + esc(t) + '</button>';
    }).join('');

    secWrap.addEventListener('click', function (e) {
      var b = e.target.closest('[data-sector]');
      if (!b) return;
      state.sector = b.getAttribute('data-sector');
      secWrap.querySelectorAll('.exp-chip').forEach(function (c) {
        c.classList.toggle('is-on', c === b);
      });
      renderGrid();
    });

    trWrap.addEventListener('click', function (e) {
      var b = e.target.closest('[data-trait]');
      if (!b) return;
      var t = b.getAttribute('data-trait');
      state.traits[t] = !state.traits[t];
      b.classList.toggle('is-on', state.traits[t]);
      renderGrid();
    });

    var search = document.getElementById('exp-search');
    search.addEventListener('input', function () {
      state.q = search.value.trim().toLowerCase();
      renderGrid();
    });

    var reset = document.getElementById('exp-reset');
    reset.addEventListener('click', function () {
      state = { sector: 'all', traits: {}, q: '' };
      search.value = '';
      secWrap.querySelectorAll('.exp-chip').forEach(function (c) {
        c.classList.toggle('is-on', c.getAttribute('data-sector') === 'all');
      });
      trWrap.querySelectorAll('.exp-chip').forEach(function (c) { c.classList.remove('is-on'); });
      renderGrid();
    });
  }

  // ---------------------------------------------------------------
  // boot
  // ---------------------------------------------------------------
  function init() {
    gridEl = document.getElementById('exp-grid');
    if (!gridEl) return; // not the explorer page
    countEl = document.getElementById('exp-count');
    emptyEl = document.getElementById('exp-empty');
    var totalEl = document.getElementById('exp-total');
    if (totalEl) totalEl.textContent = SCENARIOS.length;
    modal = document.getElementById('exp-modal');
    modalBody = document.getElementById('exp-modal-body');

    buildControls();
    renderGrid();

    gridEl.addEventListener('click', function (e) {
      var card = e.target.closest('.exp-card');
      if (card) openModal(card.getAttribute('data-id'));
    });
    gridEl.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var card = e.target.closest('.exp-card');
      if (card) { e.preventDefault(); openModal(card.getAttribute('data-id')); }
    });

    modal.addEventListener('click', function (e) {
      if (e.target === modal || e.target.closest('.exp-modal-close')) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeModal();
    });
    // keep Tab within the dialog (belt-and-braces alongside inert bg)
    modal.addEventListener('keydown', function (e) {
      if (e.key !== 'Tab') return;
      var f = modalBody.querySelectorAll('a[href],button:not([disabled]),input,select,textarea,[tabindex]:not([tabindex="-1"])');
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
