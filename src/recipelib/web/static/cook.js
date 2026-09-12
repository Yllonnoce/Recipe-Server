/* Cook mode: scaling, check-offs (localStorage), step timers, keep-awake. */
(function () {
  const data = JSON.parse(document.getElementById('recipe-data').textContent);
  const key = 'rl_cook_' + data.id;
  let state = { factor: 1, ing: {}, steps: {}, font: 1 };
  try { state = Object.assign(state, JSON.parse(localStorage.getItem(key) || '{}')); } catch (e) { /* private mode */ }
  const save = () => { try { localStorage.setItem(key, JSON.stringify(state)); } catch (e) { /* ignore */ } };
  const $ = (id) => document.getElementById(id);
  const base = data.servings || null;

  const fmt = window.rlFmt;

  function servingsLabel() {
    if (base) {
      const n = base * state.factor;
      return (Math.round(n * 10) / 10) + ' serving' + (n === 1 ? '' : 's');
    }
    return '×' + state.factor;
  }

  function renderIngredients() {
    const box = $('ingList');
    box.innerHTML = '';
    let group = null;
    data.ingredients.forEach((i) => {
      if (i.group !== group) { group = i.group; if (group) { const h = document.createElement('h3'); h.textContent = group; box.appendChild(h); } }
      const row = document.createElement('div');
      row.className = 'item' + (state.ing[i.id] ? ' done' : '');
      const q = i.quantity != null ? fmt(i.quantity * state.factor, i.unit) : '';
      const qm = i.quantity_max != null ? '–' + fmt(i.quantity_max * state.factor, i.unit) : '';
      const unit = i.unit_raw || '';
      row.innerHTML = '<span class="box">' + (state.ing[i.id] ? '✓' : '') + '</span><span><span class="q">' + q + qm + (unit ? ' ' + unit : '') + '</span>' +
        escapeHtml(i.name) + (i.preparation ? '<span class="prep">, ' + escapeHtml(i.preparation) + '</span>' : '') + (i.optional ? ' <em>(optional)</em>' : '') + '</span>';
      row.addEventListener('click', () => { state.ing[i.id] = !state.ing[i.id]; save(); renderIngredients(); });
      box.appendChild(row);
    });
  }

  function renderSteps() {
    const box = $('stepList');
    box.innerHTML = '';
    let group = null, n = 0, currentSet = false;
    data.steps.forEach((s) => {
      if (s.group !== group) { group = s.group; if (group) { const h = document.createElement('h3'); h.textContent = group; box.appendChild(h); } }
      n += 1;
      const row = document.createElement('div');
      const done = !!state.steps[s.id];
      row.className = 'step' + (done ? ' done' : '') + (!done && !currentSet ? ' current' : '');
      if (!done) currentSet = true;
      row.innerHTML = '<span class="n">' + n + '</span><div class="body">' + escapeHtml(s.text) +
        (s.minutes ? '<br><button class="timer" type="button">⏱ Start ' + s.minutes + ' min timer</button>' : '') + '</div>';
      row.addEventListener('click', (e) => {
        if (e.target.classList.contains('timer')) { e.stopPropagation(); startTimer(s.minutes * 60, 'Step ' + n); return; }
        state.steps[s.id] = !state.steps[s.id]; save(); renderSteps();
      });
      box.appendChild(row);
    });
  }

  function escapeHtml(s) { return String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

  // ---- timers ----
  let audioCtx = null;
  function beep() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      for (let i = 0; i < 3; i++) {
        const o = audioCtx.createOscillator(), g = audioCtx.createGain();
        o.connect(g); g.connect(audioCtx.destination);
        o.frequency.value = 880; g.gain.value = 0.2;
        o.start(audioCtx.currentTime + i * 0.4); o.stop(audioCtx.currentTime + i * 0.4 + 0.25);
      }
    } catch (e) { /* no audio */ }
    if (navigator.vibrate) navigator.vibrate([300, 100, 300]);
  }
  function startTimer(seconds, label) {
    const el = document.createElement('div');
    el.className = 't';
    const end = Date.now() + seconds * 1000;
    let iv = null;
    const stop = () => { clearInterval(iv); el.remove(); };
    el.innerHTML = '<span class="lbl">' + escapeHtml(label) + '</span><span class="left"></span><button type="button">✕</button>';
    el.querySelector('button').addEventListener('click', stop);
    $('timers').appendChild(el);
    const tick = () => {
      const left = Math.round((end - Date.now()) / 1000);
      const a = Math.abs(left);
      el.querySelector('.left').textContent = (left < 0 ? '+' : '') + Math.floor(a / 60) + ':' + String(a % 60).padStart(2, '0');
      if (left === 0) { el.classList.add('over'); beep(); if (document.hidden && window.Notification && Notification.permission === 'granted') new Notification('Timer done: ' + label); }
    };
    tick();
    iv = setInterval(tick, 1000);
    if (window.Notification && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
    if (audioCtx === null) { try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { /* ignore */ } }
  }

  // ---- keep the screen awake ----
  let lock = null;
  async function awake() {
    if ('wakeLock' in navigator) {
      try { lock = await navigator.wakeLock.request('screen'); $('awake').classList.add('on'); return; } catch (e) { /* not allowed (http) */ }
    }
    // fallback for plain-http tablets: a silent looping video keeps most browsers from sleeping
    const v = $('keepawake');
    if (v && !v.src) {
      v.src = 'data:video/webm;base64,' + WEBM;
      v.hidden = false; v.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0.01;pointer-events:none';
    }
    if (v) v.play().then(() => $('awake').classList.add('on')).catch(() => {});
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) awake(); });
  document.addEventListener('click', function once() { awake(); document.removeEventListener('click', once); });
  awake();

  // ---- controls ----
  const steps = [0.5, 1, 1.5, 2, 3, 4];
  const bump = (dir) => {
    if (base) {
      const cur = base * state.factor;
      const next = Math.max(1, Math.round(cur + dir));
      state.factor = next / base;
    } else {
      let i = steps.indexOf(state.factor); if (i === -1) i = 1;
      i = Math.max(0, Math.min(steps.length - 1, i + dir));
      state.factor = steps[i];
    }
    save(); render();
  };
  $('srvMinus').onclick = () => bump(-1);
  $('srvPlus').onclick = () => bump(1);
  $('fontPlus').onclick = () => { state.font = Math.min(3, (state.font || 1) + 1); save(); applyFont(); };
  $('fontMinus').onclick = () => { state.font = Math.max(0, (state.font || 1) - 1); save(); applyFont(); };
  $('btnReset').onclick = () => { state.ing = {}; state.steps = {}; save(); render(); };
  const fullscreen = () => {
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    const el = document.documentElement;
    if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
  };
  $('btnFull').onclick = fullscreen;
  document.addEventListener('keydown', (e) => {
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
    if (e.key === 'f' || e.key === 'F') fullscreen();
  });
  document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('on', x === b));
    document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('on', p.id === b.dataset.tab));
  }));
  function applyFont() { document.body.dataset.font = String(state.font || 1); }
  function render() { $('srvLabel').textContent = servingsLabel(); renderIngredients(); renderSteps(); }
  applyFont();
  render();

  // 2-second black 16x16 VP8 webm (generated with ffmpeg), ~1 KB
  const WEBM = document.body.dataset.webm || '';
})();
