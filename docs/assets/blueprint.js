/* Threadlight Blueprint — DOM controller.
 * Fetches the scenario library, renders the picker, and turns any selected or
 * described process into a derived skill arc + copy-paste prompts. All rendering
 * uses textContent / createElement (never innerHTML with data) so third-party
 * scenario text can never inject markup. */
(function () {
  'use strict';

  var L = window.TL_BLUEPRINT;
  var $ = function (id) { return document.getElementById(id); };
  var grid = $('bp-grid');
  if (!grid || !L) return;

  var domainSel = $('bp-domain');
  var cxSel = $('bp-complexity');
  var searchIn = $('bp-search');
  var resetBtn = $('bp-reset');
  var countEl = $('bp-count');
  var cDomainSel = $('bp-cdomain');
  var customForm = $('bp-custom');

  var DATA = [];
  var selectedId = null;
  var CX_LABEL = { low: 'Low', medium: 'Medium', high: 'High' };

  function pretty(s) { return L.prettyIndustry(s); }

  function domains() {
    var seen = {};
    DATA.forEach(function (e) { if (e.industry) seen[e.industry] = 1; });
    return Object.keys(seen).sort();
  }

  function fillDomainOptions() {
    domains().forEach(function (d) {
      [domainSel, cDomainSel].forEach(function (sel) {
        if (!sel) return;
        var o = document.createElement('option');
        o.value = d; o.textContent = pretty(d);
        sel.appendChild(o);
      });
    });
  }

  function matches(e) {
    var d = domainSel.value, c = cxSel.value, q = searchIn.value.trim().toLowerCase();
    if (d && e.industry !== d) return false;
    if (c && e.complexity !== c) return false;
    if (q) {
      var hay = [e.name, e.summary, e.description]
        .concat(Array.isArray(e.tags) ? e.tags : [])
        .join(' ').toLowerCase();
      if (hay.indexOf(q) === -1) return false;
    }
    return true;
  }

  function chip(cls, text) {
    var s = document.createElement('span');
    s.className = 'bp-chip ' + cls;
    s.textContent = text;
    return s;
  }

  function card(e) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'bp-card' + (e.id === selectedId ? ' is-selected' : '');
    b.setAttribute('role', 'listitem');
    b.setAttribute('data-id', e.id);
    if (e.id === selectedId) b.setAttribute('aria-pressed', 'true');

    var h = document.createElement('h3');
    h.textContent = e.name;
    var p = document.createElement('p');
    p.textContent = e.summary || e.description || '';

    var tags = document.createElement('div');
    tags.className = 'bp-tags';
    tags.appendChild(chip('dom', pretty(e.industry)));
    tags.appendChild(chip('cx-' + e.complexity, CX_LABEL[e.complexity] || e.complexity));

    b.appendChild(h); b.appendChild(p); b.appendChild(tags);
    b.addEventListener('click', function () { select(e); });
    return b;
  }

  function render() {
    var list = DATA.filter(matches);
    grid.textContent = '';
    if (!list.length) {
      var empty = document.createElement('div');
      empty.className = 'bp-empty';
      empty.textContent = 'No scenario matches those filters. Clear them, or describe your own process below.';
      grid.appendChild(empty);
    } else {
      list.forEach(function (e) { grid.appendChild(card(e)); });
    }
    if (countEl) {
      countEl.textContent = '';
      var b = document.createElement('b');
      b.textContent = String(list.length);
      countEl.appendChild(b);
      countEl.appendChild(document.createTextNode(
        ' of ' + DATA.length + (list.length === 1 ? ' scenario' : ' scenarios')));
    }
  }

  function arcInto(el, skills) {
    el.textContent = '';
    skills.forEach(function (name, i) {
      if (i) {
        var a = document.createElement('span');
        a.className = 'bp-arrow'; a.textContent = '→'; a.setAttribute('aria-hidden', 'true');
        el.appendChild(a);
      }
      var base = L.PIPELINE_ARC.indexOf(name) !== -1;
      var s = document.createElement('span');
      s.className = 'bp-skill ' + (base ? 'base' : 'added');
      var pip = document.createElement('span'); pip.className = 'pip'; pip.setAttribute('aria-hidden', 'true');
      s.appendChild(pip);
      s.appendChild(document.createTextNode(name));
      el.appendChild(s);
    });
  }

  function renderResult(proc) {
    $('bp-result-name').textContent = proc.name || 'Your process';
    arcInto($('bp-arc'), L.deriveSkills(proc));
    $('bp-prompt').textContent = L.buildPrompt(proc);
    $('bp-azd').textContent = L.buildAzd(proc);
    var panel = $('bp-result');
    panel.hidden = false;
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function select(e) {
    selectedId = e.id;
    render();
    renderResult(e);
  }

  // --- describe-your-own ---
  function titleFrom(desc) {
    var t = (desc || '').replace(/\s+/g, ' ').trim();
    if (!t) return 'Your process';
    var words = t.split(' ').slice(0, 8).join(' ');
    return words.charAt(0).toUpperCase() + words.slice(1) + (t.split(' ').length > 8 ? '…' : '');
  }

  if (customForm) {
    customForm.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var desc = $('bp-desc').value;
      var proc = {
        id: 'custom',
        name: titleFrom(desc),
        summary: desc.trim() || 'a business process',
        industry: cDomainSel.value || '',
        complexity: $('bp-ccomplexity').value || 'medium',
        external_integrations: $('bp-cint').checked ? [{ name: 'external system' }] : [],
        human_approvals: $('bp-capp').checked ? [{ step: 'human approval' }] : [],
        knowledge_sources: [],
        tags: $('bp-creg').checked ? ['regulated'] : [],
      };
      selectedId = null;
      render();
      renderResult(proc);
    });
  }

  // --- copy buttons ---
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest ? ev.target.closest('.bp-copy') : null;
    if (!btn) return;
    var target = $(btn.getAttribute('data-copy'));
    if (!target) return;
    var text = target.textContent;
    var done = function () {
      var prev = btn.textContent;
      btn.textContent = 'Copied ✓';
      btn.classList.add('copied');
      setTimeout(function () { btn.textContent = prev; btn.classList.remove('copied'); }, 1600);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text, done); });
    } else {
      fallbackCopy(text, done);
    }
  });

  function fallbackCopy(text, done) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', '');
    ta.style.position = 'absolute'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch (e) { /* no-op */ }
    document.body.removeChild(ta);
  }

  // --- filter wiring ---
  [domainSel, cxSel].forEach(function (el) { if (el) el.addEventListener('change', render); });
  if (searchIn) searchIn.addEventListener('input', render);
  if (resetBtn) resetBtn.addEventListener('click', function () {
    domainSel.value = ''; cxSel.value = ''; searchIn.value = ''; render();
  });

  // --- boot ---
  fetch('assets/process-library.json')
    .then(function (r) { if (!r.ok) throw new Error('load failed'); return r.json(); })
    .then(function (data) {
      DATA = Array.isArray(data) ? data : [];
      fillDomainOptions();
      render();
      var pre = L.parseScenarioParam(window.location.search);
      if (pre) {
        var hit = null;
        DATA.forEach(function (e) { if (e.id === pre) hit = e; });
        if (hit) select(hit);
      }
    })
    .catch(function () {
      grid.textContent = '';
      var m = document.createElement('div');
      m.className = 'bp-empty';
      m.textContent = 'Could not load the scenario library. You can still describe your own process below.';
      grid.appendChild(m);
      if (countEl) countEl.textContent = '';
    });
})();
