(function initializeProductionTopics() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeProductionTopics, { once: true });
    return;
  }
  const explorer = document.getElementById('topic-explorer');
  const tabs = [...explorer.querySelectorAll('[data-topic-tab]')];
  const tabTargets = tabs.map(tab => document.getElementById(tab.getAttribute('href').slice(1)));
  const panels = [...explorer.querySelectorAll('[data-topic-panel]')];
  const areaNavigation = [...explorer.querySelectorAll('[data-area-navigation]')];
  const tablist = explorer.querySelector('.topic-tabs');
  const compact = matchMedia('(max-width: 900px)');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let sharedTargetSelected = false;
  let scrollTarget = null;
  let scrollFrame = 0;

  function alignScrollTarget(instant = false) {
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = 0;
      if (!scrollTarget) return;
      updateOffset();
      scrollTarget.scrollIntoView({
        block: 'start', behavior: instant || reducedMotion.matches ? 'instant' : 'smooth',
      });
    });
  }

  function isSharedTarget(target) {
    return Boolean(target?.closest('#production-domains, #production-review, #chapter-top'));
  }

  function ownerOf(target) {
    return target?.closest('[data-topic-panel]')?.id;
  }

  function revealSelectedTab() {
    if (!compact.matches) return;
    const selected = tabs.find(tab => tab.getAttribute('aria-selected') === 'true');
    if (!selected) return;
    const rect = selected.getBoundingClientRect();
    const rail = tablist.getBoundingClientRect();
    tablist.scrollTo({
      left: tablist.scrollLeft + rect.left - rail.left - (rail.width - rect.width) / 2,
      behavior: 'instant',
    });
  }

  function selectTopic(id) {
    for (const panel of panels) panel.hidden = panel.id !== id;
    for (const navigation of areaNavigation) navigation.hidden = navigation.dataset.areaNavigation !== id;
    for (const [index, tab] of tabs.entries()) {
      const selected = ownerOf(tabTargets[index]) === id;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
    updateOffset();
  }

  function showTarget(target, { scroll = true, instant = false } = {}) {
    const owner = ownerOf(target);
    if (!owner && !isSharedTarget(target)) return false;
    sharedTargetSelected = !owner;
    if (owner) selectTopic(owner);
    else updateOffset();
    const links = [...explorer.querySelectorAll('[data-area-navigation] a')];
    const current = links.filter(link => {
      const destination = document.getElementById(link.getAttribute('href').slice(1));
      return owner && ownerOf(destination) === owner && (destination === target
        || Boolean(destination?.compareDocumentPosition(target) & Node.DOCUMENT_POSITION_FOLLOWING));
    }).at(-1);
    for (const link of links) {
      if (link === current) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }
    if (scroll) {
      scrollTarget = target;
      alignScrollTarget(instant);
    } else scrollTarget = null;
    return true;
  }

  function navigate(target, options) {
    if (location.hash !== `#${target.id}`) history.pushState(null, '', `#${target.id}`);
    showTarget(target, options);
  }

  tablist.setAttribute('role', 'tablist');
  tablist.setAttribute('aria-label', 'Production topics');
  const orientTabs = () => tablist.setAttribute('aria-orientation', compact.matches ? 'horizontal' : 'vertical');
  compact.addEventListener('change', orientTabs);
  orientTabs();
  for (const [index, tab] of tabs.entries()) {
    const panel = document.getElementById(ownerOf(tabTargets[index]));
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', panel.id);
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', tab.id);
    panel.tabIndex = 0;
    tab.addEventListener('keydown', event => {
      const previous = compact.matches ? 'ArrowLeft' : 'ArrowUp';
      const next = compact.matches ? 'ArrowRight' : 'ArrowDown';
      let destination;
      if (event.key === previous) destination = (index + tabs.length - 1) % tabs.length;
      else if (event.key === next) destination = (index + 1) % tabs.length;
      else if (event.key === 'Home') destination = 0;
      else if (event.key === 'End') destination = tabs.length - 1;
      else if (event.key === ' ') destination = index;
      else return;
      event.preventDefault();
      navigate(tabTargets[destination], { scroll: false });
      tabs[destination].focus();
    });
  }

  // Reveal a topic before the shared site's normal fragment scrolling can run.
  document.addEventListener('click', event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target instanceof Element ? event.target.closest('a[href^="#"]') : null;
    if (!link) return;
    const target = document.getElementById(link.getAttribute('href').slice(1));
    if (!ownerOf(target) && !isSharedTarget(target)) return;
    event.preventDefault();
    event.stopPropagation();
    navigate(target);
  }, true);

  const masthead = document.querySelector('.masthead');
  const navigation = explorer.querySelector('.topic-navigation');
  const updateOffset = () => {
    const headerHeight = masthead.getBoundingClientRect().height;
    const navigationHeight = compact.matches && !sharedTargetSelected ? navigation.getBoundingClientRect().height : 0;
    document.documentElement.style.setProperty('--production-header-offset', `${headerHeight}px`);
    document.documentElement.style.setProperty('--production-scroll-offset', `${headerHeight + navigationHeight + 16}px`);
    revealSelectedTab();
  };
  const observer = new ResizeObserver(() => {
    updateOffset();
    alignScrollTarget(true);
  });
  observer.observe(masthead);
  observer.observe(navigation);
  observer.observe(document.querySelector('main'));
  document.fonts.ready.then(() => alignScrollTarget(true));
  const releaseScrollTarget = () => { scrollTarget = null; };
  for (const name of ['wheel', 'touchmove', 'pointerdown']) {
    document.addEventListener(name, releaseScrollTarget, { passive: true, capture: true });
  }
  document.addEventListener('keydown', event => {
    if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) {
      releaseScrollTarget();
    }
  }, true);

  const restoreFragment = () => {
    const target = document.getElementById(location.hash.slice(1));
    if (!showTarget(target, { instant: true })) selectTopic(panels[0].id);
  };
  window.addEventListener('hashchange', restoreFragment);
  explorer.classList.add('topics-enhanced');
  selectTopic(panels[0].id);
  updateOffset();
  restoreFragment();
})();
