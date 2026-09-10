(() => {
  'use strict';
  function init() {
  const picker = document.querySelector('.pr-posture-picker');
  if (!picker) return;
  const diagram = document.querySelector('.pr-architecture');
  const description = document.querySelector('[data-posture-description]');
  const postures = {
    gateway: {
      label: '01 / Gateway-fronted',
      route: 'Shared AI gateway',
      description: 'Model requests pass through a shared AI gateway. Business actions still need their own runtime controls: gateway visibility alone does not govern every tool.',
    },
    native: {
      label: '02 / Foundry-native',
      route: 'Direct model access',
      description: 'The agent reaches its model without a shared AI gateway. Selected action controls remain in the supported native host or registered tool gateway; direct model access does not remove those obligations.',
    },
    customer: {
      label: '03 / Customer-owned edge',
      route: 'Customer model gateway',
      description: 'Keep the existing customer network and model-access boundary. Assess the agent and its selected action paths separately, rather than replacing the platform team’s controls.',
    },
  };
  picker.hidden = false;
  picker.addEventListener('click', event => {
    const button = event.target.closest('button[data-posture]');
    if (!button) return;
    const posture = postures[button.dataset.posture];
    diagram.dataset.posture = button.dataset.posture;
    diagram.querySelector('[data-posture-label]').textContent = posture.label;
    diagram.querySelector('[data-model-route]').textContent = posture.route;
    description.textContent = posture.description;
    picker.querySelectorAll('button').forEach(item => {
      item.setAttribute('aria-pressed', String(item === button));
    });
  });

  const index = document.querySelector('.pr-chapter-index');
  const primaryLinks = document.querySelectorAll('.pr-chapter-links a');
  const sectionOwner = {
    'chapter-top': 'chapter-top',
    'runtime-controls': 'runtime-controls',
    why: 'runtime-controls',
    checks: 'runtime-controls',
    legs: 'runtime-controls',
    proof: 'proof',
    target: 'chapter-top',
    ship: 'ship',
    start: 'ship',
    'chapter-recap': 'ship',
  };
  function syncActive() {
    const active = index.querySelector('a.is-active');
    primaryLinks.forEach(link => {
      const current = active && sectionOwner[active.hash.slice(1)] === link.hash.slice(1);
      link.classList.toggle('is-active', Boolean(current));
      if (current) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }
  new MutationObserver(syncActive).observe(index, {
    subtree: true, attributes: true, attributeFilter: ['class'],
  });
  syncActive();

  function revealTarget(hash) {
    const target = document.getElementById(hash.slice(1));
    if (!target) return null;
    // Reveal an entirely folded section, not optional references below a visible explanation.
    const ownDetail = target.querySelector(':scope > .wrap > details.pr-detail:only-child');
    if (ownDetail) ownDetail.open = true;
    for (let ancestor = target.parentElement; ancestor; ancestor = ancestor.parentElement) {
      if (ancestor.tagName === 'DETAILS') ancestor.open = true;
    }
    return target;
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    revealTarget(link.hash);
    if (index.contains(link)) index.open = false;
    // Existing shared scrolling runs after disclosures are opened.
  }, true);
  index.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      index.open = false;
      index.querySelector('summary').focus();
    }
  });
  document.addEventListener('click', event => {
    if (!index.contains(event.target)) index.open = false;
  });
  function revealHash() {
    const target = revealTarget(location.hash);
    if (target) requestAnimationFrame(() => target.scrollIntoView({ block: 'start', behavior: 'instant' }));
  }
  window.addEventListener('hashchange', revealHash);
  revealHash();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
