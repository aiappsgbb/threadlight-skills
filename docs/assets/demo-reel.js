/* ============================================================
   THREADLIGHT DEMO REEL — a self-driving "video" player.
   Vanilla ES2020+, no framework, no CDN. Sibling to site.js.

   Model: a fixed-duration timeline of N beats. A rAF ticker
   advances `elapsed`; the active beat is derived from elapsed and
   written to data-active-beat on .reel-stage. The page CSS keys
   every per-beat animation off the beat becoming display:block,
   so seeking/replaying re-triggers cleanly with no JS bookkeeping.

   Degradation: prefers-reduced-motion (or absent JS) => the reel
   renders every beat stacked and static; the transport is hidden.
   ============================================================ */
(function () {
  'use strict';

  var reel = document.getElementById('reel');
  if (!reel) return;

  var stage = reel.querySelector('.reel-stage');
  var beats = Array.prototype.slice.call(reel.querySelectorAll('.beat'));
  if (!stage || !beats.length) return;

  // Per-beat durations (ms), in DOM order. Synced to the voiceover:
  // each value is the beat's narration clip length + a ~1.3s tail so
  // the artefact reveal settles before the next beat begins.
  var DURATIONS = [12892, 17284, 13132, 14476, 14716, 19564];
  var N = beats.length;
  while (DURATIONS.length < N) DURATIONS.push(9000);
  DURATIONS = DURATIONS.slice(0, N);

  var TOTAL = DURATIONS.reduce(function (a, b) { return a + b; }, 0);
  // Cumulative end-time of each beat, for elapsed -> beat lookup.
  var ENDS = [];
  (function () { var acc = 0; for (var i = 0; i < N; i++) { acc += DURATIONS[i]; ENDS.push(acc); } })();
  // Cumulative start-time of each beat (STARTS[i] === ENDS[i-1]).
  var STARTS = [0];
  for (var si = 1; si < N; si++) STARTS.push(ENDS[si - 1]);

  function isReducedMotion() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function setCounter(el, val) {
    // el is `<span ...>0<span class="den">…</span></span>` — replace the
    // leading text node only, leaving the denominator markup intact.
    if (el.firstChild && el.firstChild.nodeType === 3) el.firstChild.nodeValue = String(val);
    else el.insertBefore(document.createTextNode(String(val)), el.firstChild);
  }

  // ---- reduced-motion / static storyboard -------------------------
  if (isReducedMotion()) {
    reel.classList.add('is-static');
    reel.querySelectorAll('[data-reel-counter]').forEach(function (el) {
      setCounter(el, parseInt(el.getAttribute('data-to'), 10) || 0);
    });
    return;
  }

  reel.classList.add('is-enhanced');

  // ---- element handles -------------------------------------------
  var q = function (sel) { return reel.querySelector(sel); };
  var playBtn   = q('[data-reel="play"]');
  var replayBtn = q('[data-reel="replay"]');
  var seek      = q('[data-reel="seek"]');
  var timeEl    = q('[data-reel="time"]');
  var totalEl   = q('[data-reel="total"]');
  var chips     = Array.prototype.slice.call(reel.querySelectorAll('.beat-chip'));
  var subtitle  = q('#reel-subtitle');
  var soundBtn  = q('[data-reel="sound"]');

  // ---- voiceover audio (one element, src swapped per beat) --------
  var AUDIO_BASE = 'assets/audio/';
  var audio = new Audio();
  audio.preload = 'none';
  var soundOn = false;
  var audioBeat = -1;

  // ---- state ------------------------------------------------------
  var elapsed = 0;       // ms into the timeline
  var playing = false;
  var rafId = 0;
  var lastTs = 0;
  var currentBeat = -1;  // 1-based; -1 forces first paint
  var autoStarted = false;
  var inView = false;

  // ---- helpers ----------------------------------------------------
  function fmt(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m + ':' + (r < 10 ? '0' : '') + r;
  }

  function beatFromElapsed(ms) {
    for (var i = 0; i < N; i++) { if (ms < ENDS[i]) return i + 1; }
    return N;
  }

  function setActiveBeat(b) {
    if (b === currentBeat) return;
    currentBeat = b;
    stage.setAttribute('data-active-beat', String(b));
    chips.forEach(function (c) {
      var cb = parseInt(c.getAttribute('data-beat'), 10);
      c.classList.toggle('is-active', cb === b);
      c.classList.toggle('is-done', cb < b);
    });
    updateSubtitle(b);
    if (soundOn && playing) playBeatAudio(b, elapsed - STARTS[b - 1]);
    // Re-run the score counter whenever the final beat is shown.
    var beatEl = beats[b - 1];
    var counter = beatEl && beatEl.querySelector('[data-reel-counter]');
    if (counter) animateCounter(counter);
  }

  function animateCounter(el) {
    var to = parseInt(el.getAttribute('data-to'), 10) || 0;
    var dur = 1100, t0 = performance.now();
    function step(now) {
      var t = Math.min(1, (now - t0) / dur);
      var e = 1 - Math.pow(1 - t, 3);
      setCounter(el, Math.round(e * to));
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  // ---- narrative subtitle + voiceover -----------------------------
  function updateSubtitle(b) {
    if (!subtitle) return;
    var cap = beats[b - 1] && beats[b - 1].querySelector('.beat-caption');
    while (subtitle.firstChild) subtitle.removeChild(subtitle.firstChild);
    var p = document.createElement('p');
    p.textContent = cap ? cap.textContent : '';
    subtitle.appendChild(p); // fresh node re-triggers the fade-in
  }

  function loadBeatAudio(b) {
    if (audioBeat === b) return false;
    audioBeat = b;
    audio.src = AUDIO_BASE + 'beat-' + b + '.mp3';
    return true;
  }

  function playBeatAudio(b, offsetMs) {
    if (!soundOn) return;
    loadBeatAudio(b);
    var off = Math.max(0, (offsetMs || 0) / 1000);
    // Apply the in-beat offset once metadata is known; never before play().
    var applyOffset = function () {
      try {
        if (off > 0.1 && isFinite(audio.duration)) {
          audio.currentTime = Math.min(off, audio.duration - 0.05);
        }
      } catch (e) {}
    };
    if (audio.readyState >= 1) applyOffset();
    else audio.addEventListener('loadedmetadata', function once() {
      audio.removeEventListener('loadedmetadata', once); applyOffset();
    });
    // CRITICAL: call play() synchronously so it stays inside the user
    // gesture that enabled sound — deferring it past the click loses the
    // gesture and Chromium/Edge silently rejects the first playback.
    var pr = audio.play();
    if (pr && pr.catch) pr.catch(function () {});
  }

  function setSound(on) {
    soundOn = on;
    reel.classList.toggle('is-sound', on);
    if (soundBtn) {
      soundBtn.setAttribute('aria-pressed', String(on));
      soundBtn.setAttribute('aria-label', on ? 'Turn off voiceover' : 'Turn on voiceover');
      soundBtn.classList.remove('is-hint');
    }
    if (on) {
      var cb = currentBeat < 1 ? 1 : currentBeat;
      // Enabling sound is a clear intent to hear it: if the reel is
      // paused (or finished), start it now so the click's gesture also
      // unlocks audio. play() will kick off playBeatAudio synchronously.
      if (playing) playBeatAudio(cb, elapsed - STARTS[cb - 1]);
      else play();
    } else {
      audio.pause();
    }
  }
  function render() {
    var pct = TOTAL ? (elapsed / TOTAL) : 0;
    if (seek) {
      seek.value = String(Math.round(pct * 1000));
      seek.style.setProperty('--seek', (pct * 100) + '%');
    }
    if (timeEl) timeEl.textContent = fmt(elapsed);
    setActiveBeat(beatFromElapsed(elapsed));
  }

  // ---- transport --------------------------------------------------
  function tick(ts) {
    if (!playing) return;
    if (!lastTs) lastTs = ts;
    var dt = ts - lastTs;
    lastTs = ts;
    elapsed += dt;
    if (elapsed >= TOTAL) { elapsed = TOTAL; render(); pause(); return; }
    render();
    rafId = requestAnimationFrame(tick);
  }

  function play() {
    if (playing) return;
    if (elapsed >= TOTAL) elapsed = 0;   // replay from start if at the end
    playing = true;
    lastTs = 0;
    reel.classList.add('is-playing');
    if (playBtn) playBtn.setAttribute('aria-label', 'Pause');
    if (soundOn) {
      var cb = currentBeat < 1 ? 1 : currentBeat;
      playBeatAudio(cb, elapsed - STARTS[cb - 1]);
    }
    rafId = requestAnimationFrame(tick);
  }

  function pause() {
    playing = false;
    reel.classList.remove('is-playing');
    if (playBtn) playBtn.setAttribute('aria-label', 'Play');
    if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
    audio.pause();
  }

  function toggle() { playing ? pause() : play(); }

  function replay() { elapsed = 0; render(); pause(); play(); }

  function seekTo(ms, keepPlaying) {
    elapsed = Math.max(0, Math.min(TOTAL, ms));
    lastTs = 0;
    render();
    if (!keepPlaying) pause();
  }

  function seekToBeat(b) {
    // Jump to the START of beat b so its animations play in full.
    var start = b > 1 ? ENDS[b - 2] : 0;
    seekTo(start, playing);
  }

  // ---- wiring -----------------------------------------------------
  if (playBtn)   playBtn.addEventListener('click', toggle);
  if (replayBtn) replayBtn.addEventListener('click', replay);
  if (soundBtn)  soundBtn.addEventListener('click', function () { setSound(!soundOn); });

  if (seek) {
    seek.addEventListener('input', function () {
      var frac = (parseInt(seek.value, 10) || 0) / 1000;
      seekTo(frac * TOTAL, false); // scrubbing pauses, like a video
    });
  }

  chips.forEach(function (c) {
    c.addEventListener('click', function () {
      seekToBeat(parseInt(c.getAttribute('data-beat'), 10) || 1);
    });
  });

  // Keyboard: only when the reel is on screen, and never while a text
  // field is focused. Space toggles, arrows step beats, R replays.
  document.addEventListener('keydown', function (e) {
    if (!inView) return;
    var tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target.isContentEditable) {
      if (e.target !== seek) return; // allow arrows on the slider itself
    }
    if (e.key === ' ' || e.key === 'Spacebar') { e.preventDefault(); toggle(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); seekToBeat(Math.min(N, currentBeat + 1)); }
    else if (e.key === 'ArrowLeft')  { e.preventDefault(); seekToBeat(Math.max(1, currentBeat - 1)); }
    else if (e.key === 'r' || e.key === 'R') { replay(); }
    else if (e.key === 'm' || e.key === 'M') { setSound(!soundOn); }
  });

  // Autoplay the first time the reel scrolls into view; track inView
  // for keyboard scoping. Pause when it leaves the viewport.
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        inView = en.isIntersecting && en.intersectionRatio >= 0.35;
        if (inView && !autoStarted) { autoStarted = true; play(); }
        else if (!en.isIntersecting && playing) { pause(); }
      });
    }, { threshold: [0, 0.35, 0.6] });
    io.observe(reel);
  } else {
    inView = true;
  }

  // ---- boot -------------------------------------------------------
  if (totalEl) totalEl.textContent = fmt(TOTAL);
  if (soundBtn) soundBtn.classList.add('is-hint'); // nudge toward voiceover
  render();
})();
