/* Small helpers for every page. Plain JavaScript; each page works without it too. */
document.addEventListener('DOMContentLoaded', () => {
  // ---- the menu: a ☰ button on phones opens and closes it
  const toggle = document.querySelector('.nav-toggle');
  const menu = document.getElementById('menu');
  if (toggle && menu) {
    const close = () => { menu.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); };
    toggle.addEventListener('click', () => {
      const open = menu.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
      if (open) { const first = menu.querySelector('a, summary'); if (first) first.focus(); }
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && menu.classList.contains('open')) { close(); toggle.focus(); }
    });
  }

  // ---- drop-down menus (Records, Shop, Add record, Reports): one open at a time; Escape or a click outside closes
  const menus = [...document.querySelectorAll('details.menu')];
  menus.forEach(d => d.addEventListener('toggle', () => {
    if (d.open) menus.forEach(o => { if (o !== d) o.open = false; });
  }));
  document.addEventListener('click', e => menus.forEach(d => { if (d.open && !d.contains(e.target)) d.open = false; }));
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    menus.forEach(d => { if (d.open) { d.open = false; d.querySelector('summary').focus(); } });
  });

  // ---- "← Back": the page above this one; for a page the app doesn't place, the browser's history
  document.querySelectorAll('a.back[data-history]').forEach(a => a.addEventListener('click', e => {
    if (history.length > 1 && document.referrer.startsWith(location.origin)) { e.preventDefault(); history.back(); }
  }));

  // ---- messages: put in again after loading, so screen readers announce them
  document.querySelectorAll('.flashes').forEach(box => {
    const html = box.innerHTML.trim();
    if (!html) return;
    box.innerHTML = '';
    setTimeout(() => { box.innerHTML = html; }, 150);
  });

  // ---- "Back to top", once the page has been scrolled a screen down
  const top = document.querySelector('.to-top');
  if (top) {
    const show = () => { top.hidden = window.scrollY < window.innerHeight * 0.8; };
    window.addEventListener('scroll', show, { passive: true });
    show();
    top.addEventListener('click', e => {
      e.preventDefault();
      window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      const skip = document.querySelector('.skip');
      if (skip) skip.focus({ preventScroll: true });
    });
  }

  // ---- tabs (the animal profile): one panel at a time; arrow keys move between tabs; the address keeps the tab
  document.querySelectorAll('[role=tablist]').forEach(list => {
    const tabs = [...list.querySelectorAll('[role=tab]')];
    const panel = t => document.getElementById(t.getAttribute('aria-controls'));
    const select = (tab, focus) => {
      tabs.forEach(t => {
        const on = t === tab;
        t.setAttribute('aria-selected', String(on));
        t.tabIndex = on ? 0 : -1;
        panel(t).hidden = !on;
      });
      if (focus) tab.focus();
    };
    list.classList.add('ready');
    tabs.forEach((t, i) => {
      t.addEventListener('click', e => {
        e.preventDefault();
        select(t, false);
        history.replaceState(null, '', '#' + t.getAttribute('aria-controls'));
      });
      t.addEventListener('keydown', e => {
        const moves = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 };
        if (!(e.key in moves)) return;
        e.preventDefault();
        const next = tabs[(moves[e.key] + tabs.length) % tabs.length];
        select(next, true);
        history.replaceState(null, '', '#' + next.getAttribute('aria-controls'));
      });
    });
    const fromHash = () => tabs.find(t => '#' + t.getAttribute('aria-controls') === location.hash);
    select(fromHash() || tabs[0], false);
    window.addEventListener('hashchange', () => { const t = fromHash(); if (t) select(t, false); });
  });
});
