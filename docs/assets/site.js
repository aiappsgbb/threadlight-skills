/* ============================================================
   THREADLIGHT EXPERIENCE — vanilla ES2020+, no framework, no CDN
   ============================================================ */
(function () {
  'use strict';

  // ---------------------------------------------------------------
  // 0 · hero particles — small DOM allocation, lazy random positions
  // ---------------------------------------------------------------
  function spawnParticles() {
    const stage = document.getElementById('hero-particles');
    if (!stage) return;
    const n = 24;
    for (let i = 0; i < n; i++) {
      const p = document.createElement('span');
      p.style.left = (Math.random() * 100) + '%';
      p.style.bottom = (-10 - Math.random() * 20) + 'px';
      p.style.animationDelay = (Math.random() * 9) + 's';
      p.style.animationDuration = (8 + Math.random() * 7) + 's';
      stage.appendChild(p);
    }
  }

  // ---------------------------------------------------------------
  // 1 · hero headline word-by-word reveal
  // ---------------------------------------------------------------
  function revealHero() {
    const words = document.querySelectorAll('.hero-headline .word');
    words.forEach((w, i) => {
      setTimeout(() => w.classList.add('in'), 220 + i * 110);
    });
  }

  // ---------------------------------------------------------------
  // 2 · IntersectionObserver-based reveal animations
  // ---------------------------------------------------------------
  function wireReveal() {
    // Flag <html> with .js BEFORE we run any animation — this makes the
    // CSS lift its visible-default and hide reveal items so they can fade in.
    // Without JS the default stays visible, so broken observers never strand
    // content as opacity:0.
    document.documentElement.classList.add('js');
    const els = document.querySelectorAll('.reveal');
    if (!('IntersectionObserver' in window)) {
      els.forEach(el => el.classList.add('in'));
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add('in');
          io.unobserve(e.target);
        }
      });
    }, { rootMargin: '0px 0px 0px 0px', threshold: 0.01 });
    els.forEach(el => io.observe(el));
    // Safety net: anything still not revealed after 1.2s gets shown, so a
    // mis-firing observer (e.g. zero-height container, deep scroll position)
    // never leaves a section blank.
    setTimeout(() => {
      document.querySelectorAll('.reveal:not(.in)').forEach(el => el.classList.add('in'));
    }, 1200);
  }

  // ---------------------------------------------------------------
  // 2b · terminal cards: start the typing animation when in view
  // ---------------------------------------------------------------
  function wireTerminalTyping() {
    const els = document.querySelectorAll('.terminal-card[data-typing]');
    if (!els.length) return;
    if (isReducedMotion() || !('IntersectionObserver' in window)) {
      els.forEach(el => el.classList.add('is-typing'));
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add('is-typing');
          io.unobserve(e.target);
        }
      });
    }, { rootMargin: '0px 0px -15% 0px', threshold: 0.25 });
    els.forEach(el => io.observe(el));
  }

  // ---------------------------------------------------------------
  // 3 · animated counters for KPI / proof tiles
  //    `data-to` is the target integer; `data-suffix` is optional.
  // ---------------------------------------------------------------
  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

  function animateCounter(el) {
    const to     = parseInt(el.dataset.to, 10) || 0;
    const suffix = el.dataset.suffix || '';
    const dur    = 1400;
    const t0     = performance.now();
    function tick(now) {
      const t = Math.min(1, (now - t0) / dur);
      const v = Math.round(easeOutCubic(t) * to);
      el.textContent = v + suffix;
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function wireCounters() {
    const els = document.querySelectorAll('[data-counter]');
    if (!('IntersectionObserver' in window)) {
      els.forEach(animateCounter);
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          animateCounter(e.target);
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.4 });
    els.forEach(el => io.observe(el));
  }

  // ---------------------------------------------------------------
  // 3b · cost-counter: decimal-aware $ animator for scene-cost
  //      Honours prefers-reduced-motion (paints final value once).
  // ---------------------------------------------------------------
  function animateCostCounter(el) {
    const to     = parseFloat(el.getAttribute('data-to') || '0');
    const prefix = el.getAttribute('data-prefix') || '';
    const suffix = el.getAttribute('data-suffix') || '';
    const fmt    = (v) => prefix + v.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + suffix;
    if (isReducedMotion()) { el.textContent = fmt(to); return; }
    const dur  = 1100;
    const start = performance.now();
    function tick(now) {
      const t = Math.min(1, (now - start) / dur);
      // ease-out cubic
      const e = 1 - Math.pow(1 - t, 3);
      el.textContent = fmt(to * e);
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function wireCostCounters() {
    const els = document.querySelectorAll('[data-cost-counter]');
    if (!els.length) return;
    if (!('IntersectionObserver' in window)) {
      els.forEach(animateCostCounter);
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          animateCostCounter(e.target);
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.4 });
    els.forEach(el => io.observe(el));
  }

  // ---------------------------------------------------------------
  // 4 · chain spine progress — fills as you scroll past skills
  //    Uses the chain-rail's bounding rect; updates a CSS var.
  // ---------------------------------------------------------------
  function wireChainProgress() {
    const rail  = document.getElementById('chain-rail');
    const cards = rail ? rail.querySelectorAll('.skill-card') : [];
    if (!rail || !cards.length) return;

    let raf = 0;
    function update() {
      raf = 0;
      const rect = rail.getBoundingClientRect();
      const vh   = window.innerHeight || document.documentElement.clientHeight;
      // 0 = rail bottom hasn't entered viewport yet
      // 1 = rail top has scrolled above viewport top
      const start = vh * 0.55;
      const end   = -rect.height + vh * 0.45;
      const span  = (start - end) || 1;
      const t     = Math.max(0, Math.min(1, (start - rect.top) / span));
      rail.style.setProperty('--chain-progress', (t * 100) + '%');

      // light up the skill nodes whose top has crossed 60% of viewport
      cards.forEach((c) => {
        const r = c.getBoundingClientRect();
        if (r.top < vh * 0.6) c.classList.add('is-on');
        else c.classList.remove('is-on');
      });
    }

    function onScroll() {
      if (raf) return;
      raf = requestAnimationFrame(update);
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    update();
  }

  // ---------------------------------------------------------------
  // 5 · smooth-scroll: skip jumpy default on the nav links
  //    (CSS handles it via scroll-behavior, this is a fallback)
  // ---------------------------------------------------------------
  function wireSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach((a) => {
      a.addEventListener('click', (e) => {
        const id = a.getAttribute('href');
        if (id.length < 2) return;
        const target = document.querySelector(id);
        if (!target) return;
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
  }

  // ---------------------------------------------------------------
  // 6 · respect reduced motion
  // ---------------------------------------------------------------
  function isReducedMotion() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  // ---------------------------------------------------------------
  // 7 · floating table-of-contents — auto-build + scrollspy
  //    Opt-in: only runs if <aside class="floating-toc"> exists.
  //    Reads data-toc-id + data-toc-label attrs on target sections.
  // ---------------------------------------------------------------
  function wireFloatingToc() {
    const toc = document.querySelector('.floating-toc');
    if (!toc) return;

    const targets = Array.from(document.querySelectorAll('[data-toc-id]'));
    if (!targets.length) return;

    const list = document.createElement('ol');
    const linksById = new Map();
    targets.forEach((sec) => {
      const id    = sec.getAttribute('data-toc-id');
      const label = sec.getAttribute('data-toc-label') || sec.id || id;
      if (!sec.id) sec.id = id;
      const li = document.createElement('li');
      const a  = document.createElement('a');
      a.href        = '#' + id;
      a.textContent = label;
      a.setAttribute('data-toc-link', id);
      li.appendChild(a);
      list.appendChild(li);
      linksById.set(id, a);
    });

    const existingList = toc.querySelector('ol');
    if (existingList) existingList.replaceWith(list);
    else toc.appendChild(list);

    toc.classList.add('is-ready');

    if (!('IntersectionObserver' in window)) {
      const first = linksById.values().next().value;
      if (first) first.classList.add('is-active');
      return;
    }

    const visible = new Map();
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        const id = e.target.getAttribute('data-toc-id');
        if (e.isIntersecting) visible.set(id, e.intersectionRatio);
        else visible.delete(id);
      });
      let bestId = null;
      let bestRatio = -1;
      visible.forEach((ratio, id) => {
        if (ratio > bestRatio) { bestRatio = ratio; bestId = id; }
      });
      linksById.forEach((a) => a.classList.remove('is-active'));
      if (bestId && linksById.has(bestId)) linksById.get(bestId).classList.add('is-active');
    }, {
      rootMargin: '-30% 0px -55% 0px',
      threshold: [0, 0.25, 0.5, 0.75, 1]
    });
    targets.forEach((sec) => io.observe(sec));
  }

  // ---------------------------------------------------------------
  // boot
  // ---------------------------------------------------------------
  function init() {
    spawnParticles();
    wireReveal();
    wireCounters();
    wireCostCounters();
    wireChainProgress();
    wireSmoothScroll();
    wireFloatingToc();
    wireTerminalTyping();
    if (isReducedMotion()) {
      // Skip word-by-word hero animation; reveal immediately
      document.querySelectorAll('.hero-headline .word').forEach(w => w.classList.add('in'));
    } else {
      revealHero();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
