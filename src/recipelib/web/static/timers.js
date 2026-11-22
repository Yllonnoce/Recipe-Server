/* Kitchen timers that keep running when you leave the cook page.
   A timer is saved by its end time (localStorage 'rl_timers'), so every page of the app (and
   every tab) shows the same timers and works out the time left itself. When one runs out it
   rings, over and over, until someone taps ✕ on any page. Limits (plain http on a home
   network): nothing can ring while no page of the app is open, and a page can only make
   sound after it has been tapped once; until then the strip flashes and says so. */
(function () {
  const KEY = 'rl_timers';
  const RING_FOR_MS = 5 * 60 * 1000;     // ring for five minutes after the end, then just flash
  const load = () => { try { const l = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(l) ? l : []; } catch (e) { return []; } };
  const store = (l) => { try { localStorage.setItem(KEY, JSON.stringify(l)); } catch (e) { /* private mode: this page only */ } };
  let list = load();

  const dock = document.createElement('div');
  dock.id = 'timerDock';
  dock.className = 'timer-dock';
  dock.setAttribute('role', 'region');
  dock.setAttribute('aria-label', 'Timers');
  document.body.appendChild(dock);
  const rowsBox = document.createElement('div');
  rowsBox.className = 'rows';
  const hint = document.createElement('div');
  hint.className = 'hint';
  hint.textContent = '🔇 Tap anywhere to hear the alarm';
  hint.hidden = true;
  dock.append(hint, rowsBox);   // hint above: the strip is bottom-anchored, so showing/hiding it never moves a ✕ under a finger

  // ---- sound: needs one tap on this page before the browser lets it play
  let ctx = null;
  function unlock() {
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      if (ctx.state === 'suspended') ctx.resume().then(showHint, () => {});
    } catch (e) { /* no audio */ }
    showHint();          // never rebuild the rows here: this runs on pointerdown, before the click lands
  }
  function showHint() { hint.hidden = !(ringing() && !canSound()); }
  ['pointerdown', 'keydown', 'touchstart'].forEach((ev) => document.addEventListener(ev, unlock, { capture: true, passive: true }));
  const canSound = () => !!ctx && ctx.state === 'running';
  function ring() {
    if (canSound()) {
      const t0 = ctx.currentTime;
      [0, 0.18, 0.36, 0.9, 1.08, 1.26].forEach((at, i) => {   // two bursts of three, kitchen-timer style
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'square';
        o.frequency.value = i % 3 === 1 ? 1180 : 960;
        g.gain.setValueAtTime(0.0001, t0 + at);
        g.gain.exponentialRampToValueAtTime(0.25, t0 + at + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.14);
        o.connect(g); g.connect(ctx.destination);
        o.start(t0 + at); o.stop(t0 + at + 0.15);
      });
    }
    try { if (navigator.vibrate) navigator.vibrate([250, 120, 250, 120, 250]); } catch (e) { /* needs a tap first */ }
  }

  const fmt = (ms) => {
    const s = Math.round(Math.abs(ms) / 1000);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (ms < 0 ? '+' : '') + (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(sec).padStart(2, '0');
  };
  const over = (t, now) => t.end <= now;
  const ringing = () => { const now = Date.now(); return list.some((t) => over(t, now) && now - t.end < RING_FOR_MS); };

  function render() {
    const now = Date.now();
    rowsBox.textContent = '';
    dock.hidden = !list.length;
    list.forEach((t) => {
      const row = document.createElement('div');
      row.className = 't' + (over(t, now) ? ' over' : '');
      row.dataset.id = t.id;
      const lbl = document.createElement(t.url ? 'a' : 'span');
      lbl.className = 'lbl';
      if (t.url) lbl.href = t.url;
      lbl.textContent = t.label;
      if (t.title) { const small = document.createElement('small'); small.textContent = t.title; lbl.appendChild(small); }
      const left = document.createElement('span');
      left.className = 'left';
      left.textContent = fmt(t.end - now);
      const stop = document.createElement('button');
      stop.type = 'button';
      stop.className = 'stop';
      stop.textContent = '✕';
      stop.title = over(t, now) ? 'Stop the alarm' : 'Cancel this timer';
      stop.setAttribute('aria-label', (over(t, now) ? 'Stop the alarm for ' : 'Cancel the timer for ') + t.label);
      stop.addEventListener('click', () => remove(t.id));
      row.append(lbl, left, stop);
      rowsBox.appendChild(row);
    });
    showHint();
  }

  function save() { store(list); render(); }
  function remove(id) {
    list = load().filter((t) => t.id !== id);
    spoken.delete(id);
    save();
  }
  /* start(seconds, label, {title, url}) -> id */
  function start(seconds, label, meta) {
    unlock();                                   // called from a tap: sound is allowed from now on
    const t = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), label: String(label || 'Timer'),
                end: Date.now() + Math.max(1, seconds) * 1000, title: meta && meta.title || '', url: meta && meta.url || '' };
    list = load().concat([t]);
    save();
    if (window.Notification && Notification.permission === 'default' && window.isSecureContext) Notification.requestPermission().catch(() => {});
    return t.id;
  }

  // another tab started or stopped a timer
  window.addEventListener('storage', (e) => { if (e.key === KEY) { list = load(); render(); } });

  const spoken = new Set();
  let lastRing = 0;
  function tick() {
    const now = Date.now();
    rowsBox.querySelectorAll('.t').forEach((row) => {
      const t = list.find((x) => x.id === row.dataset.id);
      if (!t) return;
      row.querySelector('.left').textContent = fmt(t.end - now);
      if (over(t, now) && !row.classList.contains('over')) render();
    });
    list.forEach((t) => {
      // just ran out while this page was open: say which one, once (cook mode has a voice)
      if (over(t, now) && now - t.end < 3000 && !spoken.has(t.id)) {
        spoken.add(t.id);
        if (document.hidden && window.Notification && Notification.permission === 'granted') {
          try { new Notification('Timer done: ' + t.label); } catch (e) { /* ignore */ }
        }
        if (window.rlSpeak && canSound()) setTimeout(() => window.rlSpeak.say(t.label + ' timer is done.'), 1600);
      }
    });
    if (ringing() && now - lastRing >= 2600) { lastRing = now; ring(); }
    showHint();
  }
  render();
  tick();
  setInterval(tick, 1000);

  window.rlTimers = { start, stop: remove, list: () => load(), ringing };
})();
