/* Transaction form: type switch, dependent category lists, availability hints, beneficiary + cashback panels. */
(() => {
  const { $, $$ } = window.SF;
  const form = $('#txn'); if (!form) return;
  const CATS = JSON.parse($('#cats-json').textContent), HINTS = JSON.parse($('#hints-json').textContent);
  const typeInputs = $$('input[name=type]', form), l1 = $('#cat1'), l2 = $('#cat2'), dateIn = $('#date');
  const curType = () => (typeInputs.find(r => r.checked) || { value: form.dataset.type }).value;
  const active = (c, d) => (!c.start || d >= c.start) && (!c.end || d <= c.end);
  let sel2 = l2.dataset.selected ? +l2.dataset.selected : null;

  function buildL1() {
    const t = curType(), d = dateIn.value || new Date().toISOString().slice(0, 10);
    const tops = CATS.filter(c => !c.parent && c.kind === t && active(c, d));
    const selChild = CATS.find(c => c.id === sel2);
    const want = selChild ? (selChild.parent || selChild.id) : +l1.value;
    l1.innerHTML = (t === 'transfer' ? '<option value="">No category</option>' : '<option value="" disabled>Choose…</option>') +
      tops.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
    l1.value = tops.some(c => c.id === want) ? want : (t === 'transfer' ? '' : '');
    if (!l1.value && t !== 'transfer') l1.selectedIndex = 0;
    buildL2();
  }
  function buildL2() {
    const t = curType(), d = dateIn.value || new Date().toISOString().slice(0, 10), p = CATS.find(c => c.id === +l1.value);
    if (!p) { l2.innerHTML = ''; l2.disabled = true; return; }
    l2.disabled = false;
    const kids = CATS.filter(c => c.parent === p.id && active(c, d));
    l2.innerHTML = `<option value="${p.id}">General</option>` + kids.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
    if (sel2 && (sel2 === p.id || kids.some(k => k.id === sel2))) l2.value = sel2;
    sel2 = null;
  }
  function togglePanels() {
    const t = curType();
    $$('[data-types]', form).forEach(el => el.classList.toggle('hide', !el.dataset.types.split(',').includes(t)));
    $('#acct-label').textContent = t === 'transfer' ? 'From' : (t === 'income' ? 'Into account' : 'Paid from');
    const ind = form.querySelector('input[name=scope]:checked')?.value === 'individuals';
    $('#people').classList.toggle('hide', !(ind && t === 'expense'));
    buildL1();
  }
  function hint(selId, outId) {
    const s = $('#' + selId), o = $('#' + outId); if (!s || !o) return;
    const show = () => { o.textContent = HINTS[s.value] || ''; }; s.addEventListener('change', show); show();
  }
  typeInputs.forEach(r => r.addEventListener('change', () => { sel2 = null; togglePanels(); document.title = document.title; }));
  $$('input[name=scope]', form).forEach(r => r.addEventListener('change', togglePanels));
  l1.addEventListener('change', () => { sel2 = null; buildL2(); });
  dateIn.addEventListener('change', () => { sel2 = +l2.value || null; buildL1(); });
  const acct = $('#account'), upiWrap = $('#upi-wrap');
  const showUpi = () => { const o = acct.selectedOptions[0]; upiWrap.classList.toggle('hide', !(o && o.dataset.kind === 'upi')); };
  acct.addEventListener('change', showUpi); showUpi();
  hint('account', 'hint-from'); hint('to_account', 'hint-to'); hint('cb_account', 'hint-cb');
  const amt = $('#amount');
  amt.addEventListener('blur', () => { const v = parseFloat(amt.value.replace(/,/g, '')); if (!isNaN(v) && v > 0) amt.value = v.toFixed(2); });
  togglePanels();
  if (!amt.value) setTimeout(() => amt.focus({ preventScroll: true }), 250);
})();
