// Production chapter: one continuous reading path. The shared chapter index
// (site.js + chapter-experience.js) handles scroll-spy, fragments and focus;
// this script only names the current part in the compact mobile index.
(function initializeProductionIndex() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeProductionIndex, { once: true });
    return;
  }
  const index = document.querySelector('.chapter-production .floating-toc');
  const current = index?.querySelector('[data-index-current]');
  if (!current) return;
  const short = Object.fromEntries([...index.querySelectorAll('.cx-chapter-links a')]
    .map(link => [link.hash, link.textContent.trim()]));
  const sync = () => {
    const active = index.querySelector('.cx-chapter-index a.is-active');
    const label = active ? short[active.hash] || '' : '';
    if (current.textContent !== label) current.textContent = label;
  };
  const list = index.querySelector('.cx-chapter-index');
  new MutationObserver(sync).observe(list, { subtree: true, attributes: true, attributeFilter: ['class'], childList: true });
  sync();
})();
