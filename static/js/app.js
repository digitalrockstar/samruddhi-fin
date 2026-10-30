/* Samruddhi UI behaviour: springs, sheets, confirm dialog, theme preview.
   Motion follows the Apple fluid-interface rules: respond on press, track 1:1, hand velocity to a spring,
   project momentum, stay interruptible, and fall back to cross-fades under reduced motion. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const wide = () => matchMedia('(min-width: 56rem)').matches;
  const csrf = () => document.querySelector('meta[name=csrf]')?.content || '';
  window.SF = { $, $$, csrf, reduced };

  /* ---------- money ---------- */
  window.SF.inr = (paise) => {
    paise = Math.round(paise || 0);
    const neg = paise < 0, abs = Math.abs(paise);
    let r = String(Math.floor(abs / 100)), p = String(abs % 100).padStart(2, '0');
    if (r.length > 3) { let h = r.slice(0, -3), t = r.slice(-3), parts = []; while (h.length > 2) { parts.unshift(h.slice(-2)); h = h.slice(0, -2); } if (h) parts.unshift(h); r = parts.join(',') + ',' + t; }
    return (neg ? '−' : '') + '₹' + r + '.' + p;
  };
  window.SF.inrShort = (paise) => {
    const v = Math.abs(paise) / 100, s = paise < 0 ? '−' : '';
    if (v >= 1e7) return s + '₹' + (v / 1e7).toFixed(2).replace(/\.?0+$/, '') + ' Cr';
    if (v >= 1e5) return s + '₹' + (v / 1e5).toFixed(2).replace(/\.?0+$/, '') + ' L';
    if (v >= 1e3) return s + '₹' + (v / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
    return s + '₹' + Math.round(v);
  };

  /* ---------- spring: damping ratio + response (seconds), carries velocity ---------- */
  function spring({ from, to, velocity = 0, response = 0.35, damping = 1, onUpdate, onDone }) {
    if (reduced()) { onUpdate(to, 0); onDone && onDone(); return { cancel() {}, x: to }; }
    const w = 2 * Math.PI / response, k = w * w, c = 2 * damping * w;
    let x = from, v = velocity, last = performance.now(), raf = 0, dead = false;
    const api = { cancel() { dead = true; cancelAnimationFrame(raf); }, get x() { return x; }, get v() { return v; } };
    const step = (now) => {
      if (dead) return;
      const dt = Math.min((now - last) / 1000, 0.032); last = now;
      const n = 4, h = dt / n;
      for (let i = 0; i < n; i++) { v += (-k * (x - to) - c * v) * h; x += v * h; }
      if (Math.abs(x - to) < 0.4 && Math.abs(v) < 8) { x = to; onUpdate(x, 0); onDone && onDone(); return; }
      onUpdate(x, v); raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return api;
  }
  const project = (v, d = 0.998) => (v / 1000) * d / (1 - d);              // where a flick would come to rest
  const rubber = (over, dim, k = 0.55) => (over * dim * k) / (dim + k * Math.abs(over));
  window.SF.spring = spring;

  /* ---------- bottom sheets ---------- */
  const scrim = $('#scrim');
  let current = null;
  function openSheet(el) {
    if (current && current !== el) closeSheet(current, true);
    current = el; el.classList.add('open'); scrim?.classList.add('on');
    document.body.style.overflow = 'hidden';
    if (wide()) { el.focus({ preventScroll: true }); return; }
    const h = el.offsetHeight;
    animateSheet(el, el._y ?? h * 1.05, 0, 0, 1);
    el.focus({ preventScroll: true });
  }
  function closeSheet(el, instant) {
    if (!el) return;
    scrim?.classList.remove('on'); document.body.style.overflow = '';
    if (current === el) current = null;
    const done = () => { el.classList.remove('open'); };
    if (wide() || instant) { el.style.transform = ''; el._y = el.offsetHeight * 1.05; return done(); }
    animateSheet(el, el._y || 0, el.offsetHeight * 1.05, el._v || 0, 1, done);
  }
  function animateSheet(el, from, to, velocity, damping, done) {
    el._anim?.cancel();
    el._anim = spring({ from, to, velocity, response: 0.34, damping, onUpdate: (y, v) => { el._y = y; el._v = v; el.style.transform = `translateY(${y}px)`; }, onDone: done });
  }
  function bindSheet(el) {
    el.tabIndex = -1;
    const handle = $$('.grab, .sheet-head', el);
    let id = null, startY = 0, startOffset = 0, hist = [];
    handle.forEach(h => {
      h.addEventListener('pointerdown', (e) => {
        if (wide() || e.target.closest('button, a, input, select')) return;
        id = e.pointerId; h.setPointerCapture(id);
        el._anim?.cancel();                                   // interruptible: grab it mid-flight
        startY = e.clientY; startOffset = el._y || 0; hist = [[e.timeStamp, e.clientY]];
      });
      h.addEventListener('pointermove', (e) => {
        if (e.pointerId !== id) return;
        hist.push([e.timeStamp, e.clientY]); if (hist.length > 6) hist.shift();
        let y = startOffset + (e.clientY - startY);            // 1:1 with the finger
        if (y < 0) y = -rubber(-y, el.offsetHeight);           // resist past the top edge
        el._y = y; el.style.transform = `translateY(${y}px)`;
      });
      const end = (e) => {
        if (e.pointerId !== id) return; id = null;
        const a = hist[0], b = hist[hist.length - 1];
        const v = b && a && b[0] > a[0] ? (b[1] - a[1]) / (b[0] - a[0]) * 1000 : 0;
        const projected = (el._y || 0) + project(v);
        if (projected > el.offsetHeight * 0.5 && v > -300) { scrim?.classList.remove('on'); document.body.style.overflow = ''; if (current === el) current = null;
          animateSheet(el, el._y, el.offsetHeight * 1.05, v, 1, () => el.classList.remove('open')); }
        else animateSheet(el, el._y, 0, v, Math.abs(v) > 400 ? 0.82 : 1);   // a little bounce only when a flick carried momentum
      };
      h.addEventListener('pointerup', end); h.addEventListener('pointercancel', end);
    });
  }
  $$('.sheet').forEach(bindSheet);
  document.addEventListener('click', (e) => {
    const o = e.target.closest('[data-sheet]');
    if (o) { e.preventDefault(); openSheet($('#' + o.dataset.sheet)); return; }
    if (e.target.closest('[data-close]') || e.target === scrim) closeSheet(current);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && current) closeSheet(current); });
  if (location.hash === '#filters' && $('#filters')) openSheet($('#filters'));

  /* ---------- confirm dialog for destructive actions ---------- */
  const dlg = $('#confirm');
  document.addEventListener('submit', (e) => {
    const f = e.target, msg = (e.submitter && e.submitter.dataset.confirm) || f.dataset.confirm;
    if (!msg || f._ok || !dlg) return;
    e.preventDefault();
    $('#confirm-msg').textContent = msg;
    const ok = $('#confirm-ok'); ok.textContent = (e.submitter && e.submitter.dataset.confirmLabel) || f.dataset.confirmLabel || 'Confirm';
    const sub = e.submitter;
    dlg.showModal();
    dlg.onclose = () => { if (dlg.returnValue === 'ok') { f._ok = true; sub ? f.requestSubmit(sub) : f.requestSubmit(); } dlg.returnValue = ''; };
  });

  /* ---------- theme: preview instantly, saved on submit ---------- */
  $$('.theme-opt input').forEach(r => r.addEventListener('change', () => {
    document.documentElement.dataset.theme = r.value;
    setTimeout(syncMeta, 50);
  }));

  const syncMeta = () => $('meta[name=theme-color]')?.setAttribute('content', getComputedStyle(document.body).backgroundColor);
  syncMeta();
  $$('input[name=mode]').forEach(r => r.addEventListener('change', () => {
    $$('.theme-opt').forEach(t => { t.hidden = r.value !== 'all' && t.dataset.mode !== r.value; });
    $$('[data-grp]').forEach(g => { g.hidden = !$$('.theme-opt:not([hidden])', g).length; });
  }));

  /* ---------- progress bars fill after paint ---------- */
  requestAnimationFrame(() => requestAnimationFrame(() => $$('.progress > i[data-w]').forEach(i => i.style.width = i.dataset.w + '%')));

  /* ---------- generic fetch helper for POST json ---------- */
  window.SF.post = (url, data) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF': csrf() }, body: new URLSearchParams(data) });

  /* ---------- service worker (installable + offline shell) ---------- */
  if ('serviceWorker' in navigator && location.protocol.startsWith('http')) navigator.serviceWorker.register('/sw.js').catch(() => {});
})();
