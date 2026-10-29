/* Dashboard: filters -> /api/dashboard -> SVG charts. No chart library, colours come from theme tokens. */
(() => {
  const { $, $$, inr, inrShort, reduced } = window.SF;
  const root = $('#dash'); if (!root) return;
  const state = { period: 'this_month', person: 'all', kind: 'all', start: '', end: '' };
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const color = (i) => `var(--c${(i % 8) + 1})`;
  let seq = 0, shown = {};

  function tween(el, to, fmt) {                     // numbers glide to the new value
    const from = shown[el.id] ?? 0; shown[el.id] = to;
    if (reduced() || from === to) { el.textContent = fmt(to); return; }
    const t0 = performance.now(), dur = 550;
    const tick = (now) => { const p = Math.min((now - t0) / dur, 1), e = 1 - Math.pow(1 - p, 3); el.textContent = fmt(from + (to - from) * e); if (p < 1) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  }
  const empty = (msg) => `<div class="empty"><b>Nothing here yet</b><span>${msg}</span></div>`;

  function donut(el, items) {
    const total = items.reduce((a, b) => a + b.total, 0);
    if (!total) { el.innerHTML = empty('No expenses in this period.'); return; }
    const R = 42, C = 2 * Math.PI * R; let off = 0;
    const segs = items.slice(0, 8).map((it, i) => { const len = it.total / total * C, s = `<circle class="seg" cx="50" cy="50" r="${R}" stroke="${color(i)}" stroke-width="13" stroke-dasharray="0 ${C}" data-len="${Math.max(len - 1.2, .01)}" data-c="${C}" stroke-dashoffset="${-off}"/>`; off += len; return s; }).join('');
    const rest = items.slice(8).reduce((a, b) => a + b.total, 0);
    const legend = items.slice(0, 8).map((it, i) => `<div><button class="lg" data-i="${i}" aria-expanded="false"><span class="dot" style="background:${color(i)}"></span><span class="nm">${esc(it.name)}</span><span class="num muted">${Math.round(it.total / total * 100)}%</span><b class="num">${inr(it.total)}</b></button><div class="subs hide" id="sub${i}">${it.subs.slice(0, 6).map(s => `<div class="row-flex spread"><span>${esc(s.name)}</span><span class="num">${inr(s.total)}</span></div>`).join('')}</div></div>`).join('') +
      (rest ? `<div class="lg" style="cursor:default"><span class="dot" style="background:${color(7)}"></span><span class="nm">Other</span><b class="num">${inr(rest)}</b></div>` : '');
    el.innerHTML = `<div class="donut-wrap"><svg viewBox="0 0 100 100" role="img" aria-label="Expenses by category">${segs}</svg><div class="donut-mid"><span class="faint">Spent</span><b class="num">${inrShort(total)}</b></div></div><div class="legend">${legend}</div>`;
    requestAnimationFrame(() => requestAnimationFrame(() => $$('circle.seg', el).forEach(c => c.setAttribute('stroke-dasharray', `${c.dataset.len} ${c.dataset.c}`))));
    $$('.lg[data-i]', el).forEach(b => b.addEventListener('click', () => { const s = $('#sub' + b.dataset.i); const open = s.classList.toggle('hide') === false; b.setAttribute('aria-expanded', open); }));
  }
  function hbars(el, items, msg) {
    const max = Math.max(...items.map(i => i.total), 1);
    if (!items.length) { el.innerHTML = empty(msg || 'No data in this period.'); return; }
    el.innerHTML = items.slice(0, 8).map((it, i) => `<div class="hbar"><div class="top"><span>${esc(it.name)}</span><b class="num">${inr(it.total)}</b></div><div class="track"><i style="background:${color(i)}" data-w="${it.total / max * 100}"></i></div></div>`).join('');
    requestAnimationFrame(() => requestAnimationFrame(() => $$('.track i', el).forEach(i => i.style.width = i.dataset.w + '%')));
  }
  function trend(el, rows) {
    const max = Math.max(...rows.flatMap(r => [r.income, r.expense]), 1);
    el.className = 'bars'; el.innerHTML = rows.map(r => `<div class="m"><div class="pair"><div class="b" style="height:${r.income / max * 100}%;background:var(--pos)" title="Income ${inr(r.income)}"></div><div class="b" style="height:${r.expense / max * 100}%;background:var(--neg)" title="Spent ${inr(r.expense)}"></div></div><span class="lab">${r.month}</span></div>`).join('');
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('go')));
  }
  function balances(el, groups) {
    if (!groups.length) { el.innerHTML = empty('Add an account to see balances.'); return; }
    el.innerHTML = groups.map(g => `<div class="group-title">${esc(g.institution)}</div><div class="card tight"><div class="list">${g.accounts.map(a => {
      const owed = a.kind === 'credit_card' || a.kind === 'loan';
      const val = owed ? inr(-a.balance) : inr(a.balance), sub = a.kind === 'credit_card' ? 'owed' : a.kind === 'loan' ? 'outstanding' : a.kind_label;
      return `<a class="li" href="/activity?account=${a.id}"><div class="grow"><div class="t">${esc(a.name)}${a.archived ? ' <span class="badge gray">archived</span>' : ''}</div><div class="s">${sub}</div></div><div class="end ${!owed && a.balance < 0 ? 'neg' : ''}">${val}</div></a>`; }).join('')}</div></div>`).join('');
  }

  function render(d) {
    tween($('#v-net'), d.net, v => (v < 0 ? '−' : '') + inr(Math.abs(v)));
    $('#v-net').className = 'v num ' + (d.net < 0 ? 'neg' : '');
    tween($('#v-income'), d.income, inr); tween($('#v-expense'), d.expense, inr);
    tween($('#v-balance'), d.total_balance, inr); tween($('#v-worth'), d.net_worth, inr);
    $('#v-cards').textContent = d.cards; $('#d-cards').textContent = d.addon_cards ? `${d.addon_cards} add-on, shared limit` : (d.cards ? 'Active cards' : 'No cards yet');
    const u = d.utilisation; $('#v-util').textContent = u == null ? '—' : u + '%';
    $('#d-util').textContent = d.card_limit ? `${inr(d.card_owed)} of ${inr(d.card_limit)}` : 'Add a card limit';
    const bar = $('#b-util'); bar.dataset.w = Math.min(u || 0, 100); bar.style.width = bar.dataset.w + '%';
    bar.parentElement.className = 'progress ' + (u >= 70 ? 'over' : u >= 30 ? 'warn' : 'ok');
    $('#d-range').textContent = new Date(d.period.start + 'T00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) + ' – ' + new Date(d.period.end + 'T00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
    const p = $('#pending-note'); p.classList.toggle('hide', !d.pending); p.querySelector('b').textContent = d.pending + (d.pending === 1 ? ' item' : ' items');
    trend($('#c-trend'), d.trend); donut($('#c-cat'), d.by_category);
    hbars($('#c-kind'), d.by_account_type); hbars($('#c-method'), d.by_method); hbars($('#c-person'), d.by_person, 'No spending for this selection.');
    balances($('#c-bal'), d.balances);
  }

  async function load() {
    const my = ++seq; root.setAttribute('aria-busy', 'true');
    const q = new URLSearchParams(state);
    try {
      const r = await fetch('/api/dashboard?' + q, { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(r.status);
      const d = await r.json(); if (my !== seq) return;       // a newer filter change wins
      render(d);
    } catch (e) { $('#dash-err').classList.remove('hide'); }
    finally { if (my === seq) root.removeAttribute('aria-busy'); }
  }
  $$('[data-period]').forEach(c => c.addEventListener('click', () => {
    $$('[data-period]').forEach(x => x.classList.toggle('on', x === c)); state.period = c.dataset.period;
    $('#custom').classList.toggle('hide', state.period !== 'custom'); if (state.period !== 'custom') load();
  }));
  $('#f-person').addEventListener('change', e => { state.person = e.target.value; load(); });
  $('#f-kind').addEventListener('change', e => { state.kind = e.target.value; load(); });
  $('#f-start').addEventListener('change', e => { state.start = e.target.value; if (state.start && state.end) load(); });
  $('#f-end').addEventListener('change', e => { state.end = e.target.value; if (state.start && state.end) load(); });
  $('#dash-retry')?.addEventListener('click', () => { $('#dash-err').classList.add('hide'); load(); });
  load();
})();
