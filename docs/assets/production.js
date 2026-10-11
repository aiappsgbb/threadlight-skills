// Production chapter: one chapter rail (side rail on desktop, compact bar on
// mobile) with scroll-spy, focus-moving navigation and an accessible toggle.
(function initializeProductionRail() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeProductionRail, { once: true });
    return;
  }
  const rail = document.querySelector('.chapter-production .chapter-rail');
  if (!rail) return;
  const root = document.documentElement;
  const toggle = rail.querySelector('.rail-toggle');
  const current = rail.querySelector('[data-rail-current]');
  const links = [...rail.querySelectorAll('ol a')];
  const parts = links.map(link => document.getElementById(link.hash.slice(1)));
  const compact = matchMedia('(max-width: 900px)');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const masthead = document.querySelector('.masthead');

  function chromeBottom() {
    return masthead.getBoundingClientRect().bottom + (compact.matches ? rail.offsetHeight : 0);
  }

  function updateOffset() {
    root.style.setProperty('--rail-offset', compact.matches ? `${rail.offsetHeight}px` : '0px');
  }

  function setActive(index) {
    links.forEach((link, position) => {
      if (position === index) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
    const label = links[index]?.querySelector('.rail-name')?.textContent || '';
    if (current.textContent !== label) current.textContent = label;
  }

  // The current part is the last one whose top has passed a line just below the chrome.
  let frame = 0;
  function spy() {
    frame = 0;
    const line = chromeBottom() + Math.min(160, innerHeight * 0.25);
    let index = 0;
    parts.forEach((part, position) => { if (part.getBoundingClientRect().top <= line) index = position; });
    if (innerHeight + scrollY >= root.scrollHeight - 2) index = parts.length - 1;
    setActive(index);
  }
  const requestSpy = () => { if (!frame) frame = requestAnimationFrame(spy); };

  function setOpen(open) {
    rail.toggleAttribute('data-open', open);
    toggle.setAttribute('aria-expanded', String(open));
  }

  function reveal(target) {
    for (let node = target; node; node = node.parentElement) if (node.tagName === 'DETAILS') node.open = true;
  }

  toggle.hidden = false;
  toggle.addEventListener('click', () => setOpen(!rail.hasAttribute('data-open')));
  rail.addEventListener('keydown', event => {
    if (event.key === 'Escape' && rail.hasAttribute('data-open')) {
      event.preventDefault();
      setOpen(false);
      toggle.focus();
    }
  });
  document.addEventListener('click', event => {
    if (rail.hasAttribute('data-open') && !rail.contains(event.target)) setOpen(false);
  });
  // Every in-page link (rail, route, skip link) scrolls, records history and moves focus.
  document.addEventListener('click', event => {
    const link = event.target instanceof Element ? event.target.closest('a[href^="#"]') : null;
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = document.getElementById(link.hash.slice(1));
    if (!target) return;
    event.preventDefault();
    setOpen(false);
    reveal(target);
    updateOffset();
    target.scrollIntoView({ block: 'start', behavior: reducedMotion.matches ? 'instant' : 'smooth' });
    if (location.hash !== link.hash) history.pushState(null, '', link.hash);
    if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    target.focus({ preventScroll: true });
    const index = parts.indexOf(target);
    if (index >= 0) setActive(index);
  });

  function revealHash() {
    const target = location.hash && document.getElementById(location.hash.slice(1));
    if (!target) return;
    reveal(target);
    updateOffset();
    requestAnimationFrame(() => target.scrollIntoView({ block: 'start', behavior: 'instant' }));
  }

  compact.addEventListener('change', () => { setOpen(false); updateOffset(); requestSpy(); });
  new ResizeObserver(() => { updateOffset(); requestSpy(); }).observe(rail);
  addEventListener('scroll', requestSpy, { passive: true });
  addEventListener('resize', requestSpy);
  addEventListener('hashchange', revealHash);
  updateOffset();
  spy();
  revealHash();
})();
