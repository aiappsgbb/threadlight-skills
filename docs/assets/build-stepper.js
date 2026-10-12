(() => {
  'use strict';
  // Build chapter: show one lifecycle phase at a time. Without JavaScript every phase stays visible.
  const root = document.querySelector('[data-build-stepper]');
  if (!root) return;
  const panels = [...root.querySelectorAll('[data-build-phase]')];
  const rail = root.querySelector('#scene-funnel');
  const toggle = root.querySelector('[data-show-all]');
  if (!panels.length || !rail) return;
  const links = [...rail.querySelectorAll('a[href^="#"]')];

  const panelFor = (id) => {
    if (!id) return null;
    const target = document.getElementById(id);
    return target ? target.closest('[data-build-phase]') : null;
  };

  function select(panel) {
    for (const item of panels) item.classList.toggle('is-active', item === panel);
    for (const link of links) {
      if (link.getAttribute('href') === `#${panel.id}`) link.setAttribute('aria-current', 'step');
      else link.removeAttribute('aria-current');
    }
  }

  const fromHash = () => {
    try { return panelFor(decodeURIComponent(location.hash.slice(1))); } catch { return null; }
  };

  root.classList.add('is-stepped');
  select(fromHash() || panels[0]);
  if (toggle) {
    toggle.hidden = false;
    toggle.addEventListener('click', () => {
      const all = !root.classList.contains('show-all');
      root.classList.toggle('show-all', all);
      toggle.setAttribute('aria-pressed', String(all));
      toggle.textContent = all ? 'Show one phase at a time' : 'Show all six phases';
    });
  }

  // On narrow screens the two routes start closed; the hero choice opens the one asked for.
  const routes = [...document.querySelectorAll('[data-build-route]')];
  const narrow = matchMedia('(max-width: 760px)');
  const openRoute = (id) => {
    const route = routes.find(item => item.id === id);
    if (route) route.open = true;
    return route;
  };
  if (narrow.matches) for (const route of routes) route.open = false;
  try { openRoute(decodeURIComponent(location.hash.slice(1))); } catch { /* ignore malformed hash */ }
  for (const route of routes) {
    route.querySelector('summary').addEventListener('click', (event) => {
      if (!narrow.matches) event.preventDefault();
    });
  }

  // Select before the browser scrolls, so in-page links land on a visible phase.
  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[href^="#"]');
    if (!link) return;
    let id;
    try { id = decodeURIComponent(link.getAttribute('href').slice(1)); } catch { return; }
    openRoute(id);
    const panel = panelFor(id);
    if (panel) select(panel);
  }, true);
  addEventListener('hashchange', () => {
    const panel = fromHash();
    if (panel) select(panel);
  });
})();
