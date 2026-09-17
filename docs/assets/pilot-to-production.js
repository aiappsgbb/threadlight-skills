(() => {
  'use strict';
  function init() {
    const root = document.querySelector('[data-pilot-guide]');
    if (!root) return;
    const panels = [...root.querySelectorAll('[data-guide-step]')];
    const links = [...root.querySelectorAll('[data-guide-index]')];
    const controls = root.querySelector('[data-guide-controls]');
    const previous = root.querySelector('[data-guide-previous]');
    const next = root.querySelector('[data-guide-next]');
    const counter = root.querySelector('[data-guide-counter]');
    const status = root.querySelector('[data-guide-status]');
    if (panels.length !== 5 || links.length !== panels.length || !controls || !previous || !next || !counter || !status) {
      console.error('Pilot guide controls are incomplete; all static steps remain readable.');
      return;
    }
    let current = 0;
    function show(index, updateHistory = false) {
      current = index;
      panels.forEach((panel, position) => {
        if (position !== index && panel.contains(document.activeElement)) document.activeElement.blur();
        panel.hidden = position !== index;
        panel.classList.toggle('pg-selected', position === index);
      });
      links.forEach((link, position) => {
        if (position === index) link.setAttribute('aria-current', 'step');
        else link.removeAttribute('aria-current');
      });
      root.querySelectorAll('[data-guide-visual]').forEach((node, position) => {
        node.dataset.current = String(position === index);
      });
      previous.disabled = index === 0;
      next.disabled = index === panels.length - 1;
      counter.textContent = `${index + 1} / ${panels.length}`;
      status.textContent = `Step ${index + 1} of ${panels.length}: guided requests — guidance, not cloud execution. Next never runs Azure or marks checks as passed.`;
      if (updateHistory && location.hash !== `#${panels[index].id}`) history.pushState(null, '', `#${panels[index].id}`);
    }
    function activate(index) {
      show(index, true);
      const heading = panels[index].querySelector('h2');
      heading.setAttribute('tabindex', '-1');
      heading.focus({ preventScroll: true });
      // Keep focus and viewport together before another key or click can interrupt scrolling.
      panels[index].scrollIntoView({ block: 'start', behavior: 'instant' });
    }
    function restore() {
      const index = panels.findIndex(panel => `#${panel.id}` === location.hash);
      show(index < 0 ? 0 : index);
    }
    root.addEventListener('click', event => {
      const link = event.target.closest('[data-guide-index]');
      if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      event.stopPropagation();
      activate(Number(link.dataset.guideIndex));
    }, true);
    previous.addEventListener('click', () => activate(Math.max(0, current - 1)));
    next.addEventListener('click', () => activate(Math.min(panels.length - 1, current + 1)));
    panels.forEach(panel => {
      const button = panel.querySelector('[data-copy-prompt]');
      const prompt = panel.querySelector('[data-guide-prompt]');
      const message = panel.querySelector('[data-copy-status]');
      button.hidden = false;
      button.addEventListener('click', async () => {
        button.disabled = true;
        message.textContent = '';
        try {
          if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
          await navigator.clipboard.writeText(prompt.textContent.trim());
          message.textContent = 'Prompt copied. Run it in your coding-agent session.';
        } catch {
          message.textContent = 'Clipboard unavailable. Select the prompt text and copy it manually.';
        } finally {
          button.disabled = false;
        }
      });
    });
    window.addEventListener('popstate', restore);
    window.addEventListener('hashchange', restore);
    root.setAttribute('data-guide-enhanced', '');
    controls.hidden = false;
    restore();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
