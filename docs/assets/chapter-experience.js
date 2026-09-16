(() => {
  'use strict';
  function init() {
    const root = document.querySelector('.chapter-experience');
    if (!root) return;
    const masthead = root.querySelector('.masthead');
    const siteMap = window.ThreadlightSiteMap;
    const slug = location.pathname.split('/').pop().replace(/\.html$/, '') || 'index';
    const owner = siteMap.groups.find(group => group.pages.some(page => page.slug === slug));
    const menu = masthead.querySelector('.nav');
    for (const link of menu.querySelectorAll('a')) {
      const target = siteMap.primary.find(page => link.getAttribute('href') === `./${page.slug}.html`);
      link.removeAttribute('aria-current');
      if (target?.slug === slug) link.setAttribute('aria-current', 'page');
      else if (target?.slug === owner?.entry) link.setAttribute('aria-current', 'location');
    }
    const directory = document.createElement('details');
    directory.className = 'cx-directory';
    const summary = document.createElement('summary');
    summary.textContent = 'Explore';
    summary.setAttribute('aria-label', 'Explore all Threadlight chapters');
    directory.appendChild(summary);
    const panel = document.createElement('nav');
    panel.className = 'cx-directory-panel';
    panel.setAttribute('aria-label', 'All Threadlight chapters');
    for (const group of siteMap.groups) {
      const section = document.createElement('section');
      section.className = 'cx-directory-group';
      section.dataset.siteGroup = group.id;
      if (group === owner) section.setAttribute('data-current-group', '');
      const heading = document.createElement('h2');
      heading.id = `cx-group-${group.id}`;
      heading.textContent = group.title;
      section.setAttribute('aria-labelledby', heading.id);
      section.appendChild(heading);
      for (const page of group.pages) {
        const link = document.createElement('a');
        link.href = `./${page.slug}.html`;
        if (page.slug === slug) link.setAttribute('aria-current', 'page');
        const title = document.createElement('strong');
        title.textContent = page.title;
        const detail = document.createElement('span');
        detail.textContent = page.description;
        link.append(title, detail);
        section.appendChild(link);
      }
      panel.appendChild(section);
    }
    directory.appendChild(panel);
    masthead.appendChild(directory);
    directory.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        directory.open = false;
        summary.focus();
      }
    });
    document.addEventListener('click', (event) => {
      if (!directory.contains(event.target)) directory.open = false;
    });
    function closeMenu() {
      menu.removeAttribute('data-mobile-open');
      masthead.querySelector('[data-mobile-nav-toggle]')?.setAttribute('aria-expanded', 'false');
    }
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape' && menu.hasAttribute('data-mobile-open')) {
        event.preventDefault();
        closeMenu();
        masthead.querySelector('[data-mobile-nav-toggle]')?.focus();
      }
    });
    directory.addEventListener('toggle', () => {
      if (!directory.open) return;
      closeMenu();
    });
    const resize = new ResizeObserver(() => {
      root.toggleAttribute('data-cx-compact', masthead.clientWidth <= 720);
      root.style.setProperty('--cx-header-height', `${masthead.offsetHeight}px`);
      root.style.setProperty('--cx-index-height', `${root.querySelector('.floating-toc')?.offsetHeight || 0}px`);
    });
    resize.observe(masthead);
    const index = root.querySelector('.floating-toc');
    if (index) resize.observe(index);
    const industryFilters = root.querySelector('#ind-pills');
    if (industryFilters) {
      industryFilters.addEventListener('click', event => {
        const button = event.target.closest('button[data-ind]');
        if (!button) return;
        const label = button.cloneNode(true);
        label.querySelectorAll('span').forEach(node => node.remove());
        root.querySelector('[data-selected-industry]').textContent = label.textContent.trim();
      });
    }
    const chapterIndex = root.querySelector('.cx-chapter-index');
    if (chapterIndex) {
      const mainLinks = [...root.querySelectorAll('.cx-chapter-links a')];
      const targets = [...root.querySelectorAll('[data-toc-id]')];
      function syncActive() {
        const active = chapterIndex.querySelector('a.is-active');
        let owner = mainLinks[0];
        const activePosition = targets.findIndex(target => target.id === active?.hash.slice(1));
        for (const link of mainLinks) {
          const position = targets.findIndex(target => target.id === link.hash.slice(1));
          if (position >= 0 && position <= activePosition) owner = link;
        }
        mainLinks.forEach(link => {
          link.classList.toggle('is-active', link === owner);
          if (link === owner) link.setAttribute('aria-current', 'location');
          else link.removeAttribute('aria-current');
        });
      }
      new MutationObserver(syncActive).observe(chapterIndex, {
        subtree: true, attributes: true, attributeFilter: ['class'],
      });
      syncActive();
      function revealTarget(hash) {
        const target = document.getElementById(hash.slice(1));
        if (!target) return null;
        for (let ancestor = target; ancestor; ancestor = ancestor.parentElement) {
          if (ancestor.tagName === 'DETAILS') ancestor.open = true;
        }
        return target;
      }
      document.addEventListener('click', event => {
        const link = event.target.closest('a[href^="#"]');
        if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const target = revealTarget(link.hash);
        if (!target) return;
        event.preventDefault();
        event.stopPropagation();
        chapterIndex.open = false;
        const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        target.scrollIntoView({ block: 'start', behavior: reduced ? 'instant' : 'smooth' });
        if (location.hash !== link.hash) history.pushState(null, '', link.hash);
        if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
        target.focus({ preventScroll: true });
      }, true);
      chapterIndex.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
          chapterIndex.open = false;
          chapterIndex.querySelector('summary').focus();
        }
      });
      document.addEventListener('click', event => {
        if (!chapterIndex.contains(event.target)) chapterIndex.open = false;
      });
      function revealHash() {
        const target = revealTarget(location.hash);
        if (target) requestAnimationFrame(() => target.scrollIntoView({ block: 'start', behavior: 'instant' }));
      }
      window.addEventListener('hashchange', revealHash);
      revealHash();
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
