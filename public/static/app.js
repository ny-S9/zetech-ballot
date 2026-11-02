/* Zetech Ballot - small interactive helpers (no libraries). */
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- one reusable pop-up (used for manifestos and for the "check your votes" step) ---------- */
  let dlg;
  function sheet() {
    if (dlg) return dlg;
    dlg = el('dialog', 'sheet');
    dlg.setAttribute('aria-labelledby', 'sheetTitle');
    dlg.addEventListener('click', e => { if (e.target === dlg) close(); });
    document.body.append(dlg);
    return dlg;
  }
  function close() { if (dlg && dlg.open) dlg.close(); else if (dlg) dlg.removeAttribute('open'); }
  function show(d) { if (typeof d.showModal === 'function') { if (!d.open) d.showModal(); } else d.setAttribute('open', ''); }
  function open(opts) {   // {title, sub, photo, bodyNode|text, buttons:[{label, cls, onClick}]}
    const d = sheet(); d.replaceChildren();
    const head = el('header');
    if (opts.photo) { const a = el('span', 'avatar lg'); const im = document.createElement('img'); im.src = opts.photo; im.alt = ''; a.append(im); head.append(a); }
    const t = el('div'); const h = el('h2', '', opts.title); h.id = 'sheetTitle'; t.append(h); if (opts.sub) t.append(el('p', '', opts.sub)); head.append(t);
    const body = el('div', 'body'); if (opts.bodyNode) body.append(opts.bodyNode); else body.textContent = opts.text || '';
    const foot = el('footer');
    (opts.buttons || [{ label: 'Close' }]).forEach(b => {
      const x = el('button', 'btn ' + (b.cls || 'ghost'), b.label); x.type = 'button';
      x.addEventListener('click', () => { close(); if (b.onClick) b.onClick(); }); foot.append(x);
    });
    d.append(head, body, foot); show(d); body.scrollTop = 0;
  }

  /* ---------- read the full manifesto ---------- */
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-read]');
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    open({ title: b.dataset.name, sub: b.dataset.role, photo: b.dataset.photo || '', text: b.dataset.text });
  });

  /* ---------- messages can be closed, and fade away by themselves ---------- */
  $$('.flashes .flash, .box > .flash').forEach(f => {
    const x = el('button', 'x', '\u00d7'); x.type = 'button'; x.setAttribute('aria-label', 'Dismiss');
    const gone = () => { f.classList.add('gone'); setTimeout(() => f.remove(), 450); };
    x.addEventListener('click', gone); f.append(x);
    if (!f.classList.contains('err')) setTimeout(gone, 9000);
  });

  /* ---------- character counters ---------- */
  $$('textarea[maxlength]').forEach(t => {
    const max = +t.getAttribute('maxlength'), c = el('div', 'counter');
    const upd = () => { const n = t.value.length; c.textContent = n + ' / ' + max; c.className = 'counter' + (n >= max ? ' max' : n > max * .9 ? ' warn' : ''); };
    t.after(c); t.addEventListener('input', upd); upd();
  });

  /* ---------- show / hide password ---------- */
  $$('input[type=password]').forEach(i => {
    const w = el('div', 'pw'); i.before(w); w.append(i);
    const b = el('button', 'show', 'Show'); b.type = 'button'; b.setAttribute('aria-label', 'Show or hide password');
    b.addEventListener('click', () => { const s = i.type === 'password'; i.type = s ? 'text' : 'password'; b.textContent = s ? 'Hide' : 'Show'; });
    w.append(b);
  });

  /* ---------- search + status chips for tables ---------- */
  $$('[data-filter]').forEach(box => {
    const table = $(box.dataset.filter); if (!table) return;
    const input = $('input[type=search]', box), chips = $$('.chip-btn', box), count = $('.count', box);
    let status = 'all';
    const run = () => {   // rows are looked up every time, so rows that arrive during a live refresh are filtered too
      const rows = $$('tbody tr[data-status]', table), q = (input ? input.value : '').trim().toLowerCase(); let n = 0;
      rows.forEach(r => {
        if (!r.dataset.t) r.dataset.t = r.textContent.toLowerCase();
        const ok = (status === 'all' || r.dataset.status === status) && (!q || r.dataset.t.includes(q));
        r.classList.toggle('hide', !ok); if (ok) n++;
      });
      if (count) count.textContent = 'Showing ' + n + ' of ' + rows.length;
    };
    chips.forEach(c => c.addEventListener('click', () => { chips.forEach(x => x.classList.remove('on')); c.classList.add('on'); status = c.dataset.chip; run(); }));
    if (input) input.addEventListener('input', run);
    box.addEventListener('zb:refilter', run); run();
  });

  /* ---------- numbers count up ---------- */
  if (!reduce) $$('.stat b').forEach(b => {
    const m = /^(\d+)(%?)$/.exec(b.textContent.trim()); if (!m || +m[1] < 2) return;
    const end = +m[1], t0 = performance.now();
    const tick = t => { const p = Math.min(1, (t - t0) / 700); b.textContent = Math.round(end * (1 - Math.pow(1 - p, 3))) + m[2]; if (p < 1) requestAnimationFrame(tick); };
    b.textContent = '0' + m[2]; requestAnimationFrame(tick);
  });

  /* ---------- ballot: choose first, then cast all votes with one button ---------- */
  const batch = $('#batchForm');
  if (batch) {
    const sections = $$('.pos[data-pos]', batch);
    const bar = $('#castBar'), fill = $('#castFill'), label = $('#castLabel'), btn = $('#castAll');
    const picked = () => sections.filter(s => $('input[type=radio]:checked', s));
    const update = () => {
      const n = picked().length, total = sections.length;
      sections.forEach(s => s.classList.toggle('chosen', !!$('input[type=radio]:checked', s)));
      fill.style.width = (total ? 100 * n / total : 0) + '%'; fill.classList.toggle('green', n === total && total > 0);
      label.textContent = n + ' of ' + total + ' positions chosen';
      btn.disabled = n === 0;
    };
    batch.addEventListener('change', update); update();
    batch.addEventListener('submit', e => {
      e.preventDefault();
      const chosen = picked(), missing = sections.filter(s => !chosen.includes(s));
      const ul = el('ul');
      chosen.forEach(s => { const r = $('input[type=radio]:checked', s); const li = el('li'); li.append(el('span', '', s.dataset.pos), el('b', '', r.closest('.cand').dataset.name)); ul.append(li); });
      const wrap = el('div'); wrap.append(ul);
      wrap.append(el('p', 'note', missing.length
        ? 'You have not chosen anyone for: ' + missing.map(s => s.dataset.pos).join(', ') + '. You can vote for those later, but a vote cannot be changed once it is cast.'
        : 'A vote cannot be changed once it is cast.'));
      open({ title: 'Check your choices', sub: 'These votes will be recorded', bodyNode: wrap, buttons: [
        { label: 'Go back and change' }, { label: 'Cast my votes', cls: 'red', onClick: () => HTMLFormElement.prototype.submit.call(batch) }] });
    });
  }

  /* ---------- single vote (delegates): same check step ---------- */
  $$('form.vote-form').forEach(f => f.addEventListener('submit', e => {
    e.preventDefault();
    const r = $('input[type=radio]:checked', f); if (!r) return;
    const c = r.closest('.cand'), ul = el('ul'), li = el('li');
    li.append(el('span', '', f.dataset.pos || 'Your choice'), el('b', '', c.dataset.name)); ul.append(li);
    const wrap = el('div'); wrap.append(ul, el('p', 'note', 'A vote cannot be changed once it is cast.'));
    open({ title: 'Check your choice', sub: 'This vote will be recorded', bodyNode: wrap, buttons: [
      { label: 'Go back' }, { label: 'Cast my vote', cls: 'red', onClick: () => HTMLFormElement.prototype.submit.call(f) }] });
  }));

  /* ---------- "send a new code" counts down ---------- */
  const resend = $('#resendBtn');
  if (resend) {
    let left = +(resend.dataset.wait || 30); const base = resend.textContent; resend.disabled = true;
    const t = setInterval(() => { left--; if (left <= 0) { clearInterval(t); resend.disabled = false; resend.textContent = base; } else resend.textContent = base + ' (' + left + 's)'; }, 1000);
    resend.textContent = base + ' (' + left + 's)';
  }

  /* ---------- contact form: anonymous toggle ---------- */
  const anon = $('#anonBox');
  if (anon) {
    const fields = $('#idFields'), note = $('#privacyNote');
    const sync = () => {
      fields.hidden = anon.checked;
      note.className = 'privacy' + (anon.checked ? '' : ' named');
      note.textContent = anon.checked
        ? 'Anonymous: we save only your message. No name, no admission number and no login details are kept, and the time is rounded to the hour.'
        : 'With your details: the Dean\u2019s office will see the name and admission number you type below. Both are optional.';
    };
    anon.addEventListener('change', sync); sync();
    const me = $('#useMine'); if (me) me.addEventListener('click', () => { $('#senderName').value = me.dataset.name; $('#senderAdm').value = me.dataset.adm; });
  }

  /* ---------- admin: live refresh that only changes what changed (no page reload, no blinking) ---------- */
  const auto = $('#autoRefresh');
  if (auto) {
    const box = auto.closest('.refresh');
    const swap = (sel, doc) => {
      const a = $(sel), b = $(sel, doc);
      if (!a || !b || a.innerHTML === b.innerHTML) return;
      if (a.querySelector('details[open]') || a.contains(document.activeElement)) return;   // do not disturb someone reading or clicking
      a.innerHTML = b.innerHTML;
    };
    async function softRefresh() {
      try {
        const r = await fetch(location.pathname, { credentials: 'same-origin', cache: 'no-store' });
        if (r.redirected || !r.ok || !/text\/html/.test(r.headers.get('content-type') || '')) { location.reload(); return; }   // session ended
        const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
        const now = $$('.stat b'), next = $$('.stat b', doc);
        if (now.length !== next.length) { location.reload(); return; }
        now.forEach((n, i) => { if (n.textContent !== next[i].textContent) n.textContent = next[i].textContent; });
        const lab = $$('.stat span'), lab2 = $$('.stat span', doc);
        lab.forEach((n, i) => { if (lab2[i] && n.textContent !== lab2[i].textContent) n.textContent = lab2[i].textContent; });
        ['.subnav', '#tblDelegates tbody', '#tblCands tbody', '#tblVoters tbody', '#tblFeedback tbody', '#audit tbody'].forEach(s => swap(s, doc));
        $$('[data-filter]').forEach(b => b.dispatchEvent(new Event('zb:refilter')));
        const stamp = $('#lastSync'); if (stamp) stamp.textContent = 'Updated ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      } catch (e) { /* offline for a moment: try again next time */ }
    }
    window.zbSoftRefresh = softRefresh;
    let timer;
    const apply = () => {
      clearInterval(timer); box.classList.toggle('live', auto.checked);
      try { localStorage.setItem('zbAuto', auto.checked ? '1' : '0'); } catch (e) {}
      if (auto.checked) timer = setInterval(() => { if (!(dlg && dlg.open) && !document.hidden) softRefresh(); }, 15000);
    };
    try { auto.checked = localStorage.getItem('zbAuto') !== '0'; } catch (e) { auto.checked = true; }
    auto.addEventListener('change', apply); apply();
    const now = $('#refreshNow'); if (now) now.addEventListener('click', softRefresh);
  }
})();
