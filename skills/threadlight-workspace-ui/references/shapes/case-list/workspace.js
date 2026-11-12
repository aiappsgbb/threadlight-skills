/* case-list workspace reference: queue + detail pane + HITL panels.
   Vanilla JS, no build step, no network beyond the static origin.
   This page never approves or records a governed decision itself. */
(function () {
  'use strict';

  var DATA_URL = './data/cases.json';
  var PANELS = {
    decision: './components/decision-pane.html',
    toolbar: './components/action-toolbar.html',
    audit: './components/audit-viewer.html'
  };
  var SCHEMA = 'threadlight-workspace-cases/v1';
  var STATUS = {
    pending_approval: { label: 'Pending approval', rank: 0 },
    awaiting_review: { label: 'Awaiting review', rank: 1 },
    escalated: { label: 'Escalated', rank: 2 },
    approved: { label: 'Approved', rank: 3 },
    completed: { label: 'Completed', rank: 4 },
    rejected: { label: 'Rejected', rank: 5 }
  };
  var RISK = { high: 0, medium: 1, low: 2 };

  var state = {
    doc: null,
    cases: [],
    filters: { status: 'all', risk: 'all', q: '' },
    sort: { key: 'status', dir: 'asc' },
    selectedId: null
  };

  var $ = function (id) { return document.getElementById(id); };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'className') node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }

  function statusLabel(s) { return (STATUS[s] && STATUS[s].label) || String(s || 'unknown').replace(/_/g, ' '); }

  function money(amount) {
    if (amount == null) return '—';
    var currency = (state.doc && state.doc.workspace && state.doc.workspace.currency) || 'EUR';
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency }).format(Number(amount) || 0);
    } catch (e) {
      return String(amount);
    }
  }

  function clock() {
    var asOf = state.doc && state.doc.workspace && state.doc.workspace.as_of;
    var t = asOf ? Date.parse(asOf) : NaN;
    return isNaN(t) ? Date.now() : t;
  }

  function sla(iso) {
    var due = Date.parse(iso);
    if (isNaN(due)) return { text: '—', state: '' };
    var mins = Math.round((due - clock()) / 60000);
    if (mins < 0) return { text: 'Overdue', state: 'breach' };
    var text = mins >= 60 ? Math.floor(mins / 60) + 'h ' + (mins % 60) + 'm' : mins + 'm';
    return { text: text, state: mins < 10 ? 'breach' : mins < 120 ? 'soon' : '' };
  }

  function showBanner(html) {
    var b = $('banner');
    b.innerHTML = html;
    b.hidden = false;
  }

  /* ---------- panels ---------- */

  function loadPanel(url, slot) {
    return fetch(url).then(function (res) {
      if (!res.ok) throw new Error(url + ' returned ' + res.status);
      return res.text();
    }).then(function (html) {
      var tpl = document.createElement('template');
      tpl.innerHTML = html;
      var scripts = Array.prototype.slice.call(tpl.content.querySelectorAll('script'));
      scripts.forEach(function (s) { s.remove(); });
      slot.appendChild(tpl.content);
      scripts.forEach(function (s) {
        var run = document.createElement('script');
        run.textContent = s.textContent;
        slot.appendChild(run);
      });
    });
  }

  function loadPanels() {
    return loadPanel(PANELS.decision, $('decision-slot')).then(function () {
      var toolbarSlot = document.querySelector('[data-slot="action-toolbar"]');
      return loadPanel(PANELS.toolbar, toolbarSlot);
    }).then(function () {
      return loadPanel(PANELS.audit, $('audit-slot'));
    }).then(applyAccent);
  }

  function applyAccent() {
    var accent = state.doc && state.doc.workspace && state.doc.workspace.accent;
    if (!accent) return;
    document.documentElement.style.setProperty('--accent', accent);
    document.querySelectorAll('[data-panel]').forEach(function (p) { p.style.setProperty('--accent', accent); });
  }

  /* ---------- data ---------- */

  function loadData() {
    return fetch(DATA_URL, { cache: 'no-store' }).then(function (res) {
      if (!res.ok) throw new Error(DATA_URL + ' returned ' + res.status);
      return res.json();
    }).then(function (doc) {
      if (doc.schema && doc.schema !== SCHEMA) {
        showBanner('Unexpected data schema <code>' + escapeText(doc.schema) + '</code>; expected <code>' + SCHEMA + '</code>.');
      }
      state.doc = doc;
      state.cases = (Array.isArray(doc.cases) ? doc.cases : Array.isArray(doc.records) ? doc.records : [])
        .map(function (c) { var copy = Object.assign({}, c); delete copy._meta; return copy; });
      bindIdentity();
      applyAccent();
    });
  }

  function escapeText(s) {
    var d = document.createElement('div');
    d.textContent = String(s == null ? '' : s);
    return d.innerHTML;
  }

  function bindIdentity() {
    var ws = state.doc.workspace || {};
    document.querySelectorAll('[data-bind]').forEach(function (node) {
      var key = node.getAttribute('data-bind');
      if (key === 'source') node.textContent = DATA_URL.replace('./', '');
      else if (ws[key]) node.textContent = ws[key];
    });
    if (ws.process_name) document.title = ws.process_name + ' workspace';
  }

  /* ---------- filters + list ---------- */

  function visibleCases() {
    var f = state.filters;
    var q = f.q.trim().toLowerCase();
    var rows = state.cases.filter(function (c) {
      if (f.status !== 'all' && c.status !== f.status) return false;
      if (f.risk !== 'all' && c.risk !== f.risk) return false;
      if (!q) return true;
      return [c.id, c.customer, c.item, c.decision, c.owner].join(' ').toLowerCase().indexOf(q) !== -1;
    });
    var key = state.sort.key;
    var dir = state.sort.dir === 'asc' ? 1 : -1;
    var value = function (c) {
      if (key === 'status') return (STATUS[c.status] || { rank: 99 }).rank;
      if (key === 'risk') return RISK[c.risk] == null ? 9 : RISK[c.risk];
      if (key === 'amount') return Number(c.amount) || 0;
      if (key === 'sla_due') return Date.parse(c.sla_due) || 0;
      return String(c[key] || '').toLowerCase();
    };
    return rows.sort(function (a, b) {
      var va = value(a), vb = value(b);
      if (va < vb) return -dir;
      if (va > vb) return dir;
      return String(a.id).localeCompare(String(b.id));
    });
  }

  function renderFilters() {
    var counts = { all: state.cases.length };
    var risks = { all: state.cases.length };
    state.cases.forEach(function (c) {
      counts[c.status] = (counts[c.status] || 0) + 1;
      risks[c.risk] = (risks[c.risk] || 0) + 1;
    });
    var statuses = ['all'].concat(Object.keys(counts).filter(function (s) { return s !== 'all'; })
      .sort(function (a, b) { return ((STATUS[a] || { rank: 99 }).rank) - ((STATUS[b] || { rank: 99 }).rank); }));
    pillList($('filters'), statuses, counts, 'status', function (s) { return s === 'all' ? 'All cases' : statusLabel(s); });
    var riskKeys = ['all'].concat(Object.keys(risks).filter(function (r) { return r !== 'all' && r !== 'undefined'; })
      .sort(function (a, b) { return (RISK[a] == null ? 9 : RISK[a]) - (RISK[b] == null ? 9 : RISK[b]); }));
    pillList($('risk-filters'), riskKeys, risks, 'risk', function (r) { return r === 'all' ? 'Any risk' : r.charAt(0).toUpperCase() + r.slice(1); });
  }

  function pillList(list, keys, counts, field, label) {
    list.textContent = '';
    keys.forEach(function (k) {
      var btn = el('button', { type: 'button', className: 'ws-pill', 'aria-pressed': String(state.filters[field] === k), 'data-filter': field + ':' + k }, [
        el('span', { text: label(k) }),
        el('span', { className: 'ws-pill-count', text: String(counts[k] || 0) })
      ]);
      btn.addEventListener('click', function () {
        state.filters[field] = k;
        renderFilters();
        renderRows();
      });
      list.appendChild(el('li', {}, [btn]));
    });
  }

  function renderRows() {
    var rows = visibleCases();
    var tbody = $('rows');
    tbody.textContent = '';
    rows.forEach(function (c) {
      var s = sla(c.sla_due);
      var idBtn = el('button', { type: 'button', className: 'ws-row-id', text: c.id, 'aria-label': 'Open case ' + c.id });
      idBtn.addEventListener('click', function (e) { e.stopPropagation(); select(c.id, true); });
      var tr = el('tr', { 'data-case-id': c.id, 'aria-selected': String(c.id === state.selectedId) }, [
        el('td', {}, [idBtn, el('span', { className: 'ws-sub', text: [c.customer, c.item].filter(Boolean).join(' · ') })]),
        el('td', {}, [el('span', { className: 'ws-badge', 'data-status': c.status, text: statusLabel(c.status) })]),
        el('td', { className: 'ws-num', text: money(c.amount) }),
        el('td', { text: c.decision || '—' }),
        el('td', { className: 'ws-hide-narrow' }, [el('span', { className: 'ws-risk', 'data-risk': c.risk || '', text: c.risk || '—' })]),
        el('td', { className: 'ws-hide-narrow' }, [el('span', { className: 'ws-sla', 'data-state': s.state, text: s.text })])
      ]);
      tr.addEventListener('click', function () { select(c.id, false); });
      tbody.appendChild(tr);
    });
    $('empty').hidden = rows.length > 0;
    var pending = state.cases.filter(function (c) { return c.status === 'pending_approval'; }).length;
    $('count').textContent = rows.length + ' of ' + state.cases.length + ' shown · ' + pending + ' pending approval';
    document.querySelectorAll('.ws-table th').forEach(function (th) {
      var key = th.querySelector('button').getAttribute('data-sort');
      th.setAttribute('aria-sort', key === state.sort.key ? (state.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
    });
  }

  /* ---------- detail ---------- */

  function find(id) {
    for (var i = 0; i < state.cases.length; i++) if (state.cases[i].id === id) return state.cases[i];
    return null;
  }

  function select(id, focusDetail) {
    var c = find(id);
    if (!c) return;
    state.selectedId = id;
    document.querySelectorAll('#rows tr').forEach(function (tr) {
      tr.setAttribute('aria-selected', String(tr.getAttribute('data-case-id') === id));
    });
    renderDetail(c);
    $('open-audit').disabled = false;
    if (focusDetail && window.matchMedia('(max-width: 1100px)').matches) {
      $('detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
      $('detail-title').focus({ preventScroll: true });
    }
  }

  function fieldList(target, pairs) {
    target.textContent = '';
    pairs.forEach(function (p) {
      if (p[1] == null || p[1] === '') return;
      target.appendChild(el('dt', { text: p[0] }));
      target.appendChild(el('dd', { text: String(p[1]) }));
    });
  }

  function renderDetail(c) {
    $('detail-empty').hidden = true;
    $('detail-body').hidden = false;
    $('detail-title').textContent = c.id + (c.customer ? ' · ' + c.customer : '');
    var badge = $('detail-status');
    badge.setAttribute('data-status', c.status);
    badge.textContent = statusLabel(c.status);
    var s = sla(c.sla_due);
    fieldList($('detail-fields'), [
      ['Item', c.item], ['Amount', money(c.amount)], ['Decision', c.decision],
      ['Risk', c.risk], ['Owner', c.owner], ['SLA', s.text], ['Opened', c.opened_at]
    ]);
    renderPending(c);

    var reasoning = $('reasoning');
    reasoning.textContent = '';
    (c.reasoning || []).forEach(function (r) {
      reasoning.appendChild(el('li', {}, [el('code', { text: r.tool || 'step' }), document.createTextNode(' ' + (r.text || ''))]));
    });
    if (!reasoning.children.length) reasoning.appendChild(el('li', { text: 'No reasoning trace captured for this case.' }));

    var cfg = (state.doc && state.doc.config) || {};
    var actionable = (cfg.actionable_statuses || ['awaiting_review']).indexOf(c.status) !== -1 && !c.pending;
    var tl = window.threadlight;
    tl.activeCase = { id: c.id, entity: c.customer, status: c.status, sla: { hardDeadline: c.sla_due } };
    tl.recommendation = c.recommendation || null;
    tl.actions = actionable ? ((state.doc && state.doc.actions) || []) : [];
    tl.audit = (c.audit || []).slice();
    window.dispatchEvent(new CustomEvent('threadlight:case-changed', { detail: { caseId: c.id } }));
    window.dispatchEvent(new CustomEvent('threadlight:actions-updated'));
  }

  function reviewCommand(c) {
    var rc = ((state.doc && state.doc.config) || {}).review_command || {};
    var decision = (document.querySelector('input[name="review-decision"]:checked') || {}).value || 'approve';
    return [
      'threadlight-review-action --pending ' + (c.pending_file || 'pending.json') + ' \\',
      '  --control-plane-url ' + (rc.control_plane_url || 'https://<control-plane-host>') + ' \\',
      '  --scope ' + (rc.scope || 'api://<CONTROL_API_ID>/.default') + ' \\',
      '  --tenant ' + (rc.tenant || '<TENANT_ID>') + ' --client-id ' + (rc.client_id || '<REVIEW_CLIENT_ID>') + ' \\',
      '  --role ' + (rc.role || 'Approver') + ' --decision ' + decision
    ].join('\n');
  }

  function renderPending(c) {
    var box = $('pending');
    var p = c.pending;
    if (!p || p.status !== 'pending_approval') { box.hidden = true; return; }
    box.hidden = false;
    fieldList($('pending-fields'), [
      ['Operation', p.operation_id],
      ['Action', p.review_context && p.review_context.action],
      ['Result', p.status]
    ]);
    var args = $('pending-args');
    args.textContent = '';
    Object.keys(p.proposed_arguments || {}).forEach(function (k) {
      args.appendChild(el('tr', {}, [el('th', { scope: 'row', text: k }), el('td', { text: String(p.proposed_arguments[k]) })]));
    });
    var approval = ((state.doc && state.doc.config) || {}).approval || {};
    var mode = approval.mode === 'email' || approval.mode === 'panel' ? approval.mode : null;
    $('pending-email').hidden = mode !== 'email';
    $('pending-email').textContent = mode === 'email'
      ? 'Approval email sent to ' + (approval.recipient || 'the approver') + '. The approver clicks Approve or Reject in Outlook; nothing to do here.'
      : '';
    $('pending-panel').hidden = mode !== 'panel';
    $('pending-approve').disabled = false;
    $('pending-reject').disabled = false;
    $('panel-status').textContent = '';
    $('pending-cli').hidden = !!mode;
    $('pending-file').textContent = c.pending_file || 'pending.json';
    $('pending-command').textContent = reviewCommand(c);
    $('copy-status').textContent = '';
  }

  /* ---------- panel approval (authenticated endpoint records it as the approver) ---------- */

  function panelDecide(approved) {
    var c = find(state.selectedId);
    var approval = ((state.doc && state.doc.config) || {}).approval || {};
    if (!c || !c.pending || approval.mode !== 'panel' || !approval.url) return;
    $('pending-approve').disabled = true;
    $('pending-reject').disabled = true;
    $('panel-status').textContent = 'Sign in as the approver in the browser window that opens…';
    fetch(approval.url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Threadlight-Panel': approval.token || '' },
      body: JSON.stringify({ operation_id: c.pending.operation_id, approved: approved })
    }).then(function (res) {
      return res.json().then(function (body) { return { ok: res.ok, body: body }; });
    }).then(function (r) {
      if (!r.ok) throw new Error(r.body && r.body.error || 'not recorded');
      $('panel-status').textContent = (approved ? 'Approved' : 'Rejected') +
        ' as you. Nothing has executed yet: the agent resumes the held write.';
    }).catch(function (e) {
      $('pending-approve').disabled = false;
      $('pending-reject').disabled = false;
      $('panel-status').textContent = 'Not recorded (' + e.message + ').';
    });
  }

  function copyCommand() {
    var text = $('pending-command').textContent;
    var done = function () { $('copy-status').textContent = 'Copied. Nothing was recorded; run it as the reviewer.'; };
    var fallback = function () {
      var range = document.createRange();
      range.selectNodeContents($('pending-command'));
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      $('copy-status').textContent = 'Selected. Press Ctrl+C or Cmd+C to copy.';
    };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }

  /* ---------- writeback (agent endpoint, never a local approval) ---------- */

  function onAction(actionId, payload) {
    var cfg = (state.doc && state.doc.config) || {};
    if (!cfg.writeback_url) {
      return Promise.reject(new Error('Not recorded: no writeback endpoint is configured (config.writeback_url). This reference never records decisions in the browser.'));
    }
    return fetch(cfg.writeback_url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: actionId, case_id: payload.caseId, reason: payload.reason || '' })
    }).then(function (res) {
      if (!res.ok) throw new Error('Writeback returned ' + res.status + '; nothing recorded by this page.');
      return res.json();
    }).then(function (body) {
      var c = find(payload.caseId);
      if (c && body && body.status === 'pending_approval') {
        c.status = 'pending_approval';
        c.pending = body;
        renderFilters();
        renderRows();
        renderDetail(c);
      }
      return body;
    });
  }

  /* ---------- wiring ---------- */

  function wire() {
    window.threadlight = { activeCase: null, recommendation: null, actions: [], audit: [], onAction: onAction };

    $('search').addEventListener('input', function (e) { state.filters.q = e.target.value; renderRows(); });
    document.querySelectorAll('.ws-table th button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-sort');
        state.sort = { key: key, dir: state.sort.key === key && state.sort.dir === 'asc' ? 'desc' : 'asc' };
        renderRows();
      });
    });
    $('rows').addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      var buttons = Array.prototype.slice.call(document.querySelectorAll('#rows .ws-row-id'));
      var i = buttons.indexOf(document.activeElement);
      if (i === -1) return;
      e.preventDefault();
      var next = buttons[Math.max(0, Math.min(buttons.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))];
      next.focus();
      select(next.textContent, false);
    });
    document.querySelectorAll('input[name="review-decision"]').forEach(function (r) {
      r.addEventListener('change', function () { var c = find(state.selectedId); if (c) renderPending(c); });
    });
    $('copy-command').addEventListener('click', copyCommand);
    $('pending-approve').addEventListener('click', function () { panelDecide(true); });
    $('pending-reject').addEventListener('click', function () { panelDecide(false); });
    $('open-audit').addEventListener('click', function () {
      if (typeof window.openThreadlightAudit !== 'function') return;
      window.openThreadlightAudit();
      var close = document.querySelector('[data-panel="audit-viewer"] [data-action="close"]');
      if (close) close.focus();
    });
    document.addEventListener('keydown', function (e) {
      var drawer = document.querySelector('[data-panel="audit-viewer"]');
      if (e.key === 'Escape' && drawer && !drawer.hidden && typeof window.closeThreadlightAudit === 'function') {
        window.closeThreadlightAudit();
        $('open-audit').focus();
      }
    });
    $('reload').addEventListener('click', function () {
      loadData().then(refresh).catch(fail);
    });
  }

  function refresh() {
    renderFilters();
    renderRows();
    if (state.selectedId && find(state.selectedId)) select(state.selectedId, false);
  }

  function fail(err) {
    var hint = location.protocol === 'file:'
      ? ' Browsers block <code>fetch</code> from <code>file://</code>. Serve this folder instead, for example <code>python3 -m http.server 8000</code>, then open <code>http://localhost:8000/</code>.'
      : '';
    showBanner('Could not load the workspace: ' + escapeText(err && err.message ? err.message : err) + '.' + hint);
  }

  wire();
  loadData().then(loadPanels).then(refresh).catch(fail);
})();
