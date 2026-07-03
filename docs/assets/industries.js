/* Threadlight Industries — process-library gallery controller.
 * Fetches the shared scenario library, renders an industry-filter pill row and
 * a searchable card grid. Every card is a deep-link into the Blueprint composer
 * (blueprint.html?s=<id>), which pre-selects that process. All rendering uses
 * textContent / createElement (never innerHTML with data) so third-party
 * scenario text can never inject markup. */
(function () {
  'use strict';

  var L = window.TL_BLUEPRINT;
  var $ = function (id) { return document.getElementById(id); };
  var grid = $('ind-grid');
  var pillsEl = $('ind-pills');
  if (!grid || !pillsEl) return;

  var searchIn = $('ind-search');
  var countEl = $('ind-count');

  var DATA = [];
  var activeInd = '';
  var CX_LABEL = { low: 'Low', medium: 'Medium', high: 'High' };

  function pretty(s) { return L ? L.prettyIndustry(s) : String(s || ''); }

  function counts() {
    var m = {};
    DATA.forEach(function (e) { if (e.industry) m[e.industry] = (m[e.industry] || 0) + 1; });
    return m;
  }

  function orderedIndustries() {
    var m = counts();
    return Object.keys(m).sort(function (a, b) {
      if (m[b] !== m[a]) return m[b] - m[a];         // busiest first
      return pretty(a).localeCompare(pretty(b));      // then alphabetical
    });
  }

  function pill(ind, labelText, n) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'ind-pill' + (ind === activeInd ? ' is-active' : '');
    b.setAttribute('data-ind', ind);
    if (ind === activeInd) b.setAttribute('aria-pressed', 'true');
    b.appendChild(document.createTextNode(labelText));
    var c = document.createElement('span');
    c.className = 'n';
    c.textContent = String(n);
    b.appendChild(c);
    b.addEventListener('click', function () {
      activeInd = ind;
      buildPills();
      render();
    });
    return b;
  }

  function buildPills() {
    pillsEl.textContent = '';
    pillsEl.appendChild(pill('', 'All', DATA.length));
    var m = counts();
    orderedIndustries().forEach(function (ind) {
      pillsEl.appendChild(pill(ind, pretty(ind), m[ind]));
    });
  }

  function matches(e) {
    if (activeInd && e.industry !== activeInd) return false;
    var q = (searchIn && searchIn.value ? searchIn.value : '').trim().toLowerCase();
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
    s.className = 'ind-chip ' + cls;
    s.textContent = text;
    return s;
  }

  function card(e) {
    var a = document.createElement('a');
    a.className = 'ind-card';
    a.href = 'blueprint.html?s=' + encodeURIComponent(e.id);
    a.setAttribute('role', 'listitem');

    var h = document.createElement('h3');
    h.textContent = e.name;
    var p = document.createElement('p');
    p.textContent = e.summary || e.description || '';

    var foot = document.createElement('div');
    foot.className = 'ind-foot';
    foot.appendChild(chip('dom', pretty(e.industry)));
    foot.appendChild(chip('cx-' + e.complexity, CX_LABEL[e.complexity] || e.complexity));
    var go = document.createElement('span');
    go.className = 'ind-go';
    go.textContent = 'Compose';
    foot.appendChild(go);

    a.appendChild(h); a.appendChild(p); a.appendChild(foot);
    return a;
  }

  function render() {
    var list = DATA.filter(matches);
    grid.textContent = '';
    if (!list.length) {
      var empty = document.createElement('div');
      empty.className = 'ind-empty';
      empty.textContent = 'No process matches those filters. Clear the search, pick All, or describe your own in the composer.';
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
        ' of ' + DATA.length + (list.length === 1 ? ' process' : ' processes')));
    }
  }

  if (searchIn) searchIn.addEventListener('input', render);

  fetch('assets/process-library.json')
    .then(function (r) { if (!r.ok) throw new Error('load failed'); return r.json(); })
    .then(function (data) {
      DATA = Array.isArray(data) ? data : [];
      buildPills();
      render();
    })
    .catch(function () {
      grid.textContent = '';
      var m = document.createElement('div');
      m.className = 'ind-empty';
      m.textContent = 'Could not load the process library. Open the Blueprint composer to describe your process directly.';
      grid.appendChild(m);
      if (countEl) countEl.textContent = '';
    });
})();
