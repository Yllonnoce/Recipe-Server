/* Cook mode: scaling, check-offs (localStorage), step timers, keep-awake, read aloud (tts.js). */
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
      row.innerHTML = '<span class="box">' + (state.ing[i.id] ? '✓' : '') + '</span><span>' + ingHtml(i) + '</span>';
      row.addEventListener('click', () => { state.ing[i.id] = !state.ing[i.id]; save(); renderIngredients(); });
      box.appendChild(row);
    });
  }

  /* One ingredient at the chosen servings and units: the same in the list and under a step. */
  function ingAmount(i) {
    const target = window.rlUnits.get();
    const a = i.quantity != null ? window.rlConvert(i.quantity * state.factor, i.unit, target) : { q: null, label: null };
    const q = a.q != null ? fmt(a.q, a.unit) : '';
    let qm = '';
    if (i.quantity_max != null) { const b = window.rlConvert(i.quantity_max * state.factor, i.unit, target); qm = '–' + fmt(b.q, b.unit); }
    // as written, else the parsed unit (some ingredients have no written form saved)
    const unit = a.label != null ? a.label : (i.unit_raw || (i.quantity != null ? i.unit : '') || '');
    return (q + qm + (unit ? ' ' + unit : '')).trim();
  }
  /* brief: under a step, keep only short notes ("chopped"); long ones stay in the main list */
  function ingHtml(i, brief) {
    const prep = i.preparation && (!brief || i.preparation.length <= 30) ? i.preparation : '';
    return '<span class="q">' + escapeHtml(ingAmount(i)) + '</span> ' + escapeHtml(i.name) +
      (prep ? '<span class="prep">, ' + escapeHtml(prep) + '</span>' : '') + (i.optional ? ' <em>(optional)</em>' : '');
  }
  const ingById = Object.fromEntries(data.ingredients.map((i) => [i.id, i]));
  const stepIngs = (s) => (s.ingredients || []).map((id) => ingById[id]).filter(Boolean);
  const ingOpen = {};          // step id -> open/closed as the cook left it (this visit only)

  function renderSteps() {
    const box = $('stepList');
    box.innerHTML = '';
    let group = null, n = 0, currentSet = false;
    data.steps.forEach((s) => {
      if (s.group !== group) { group = s.group; if (group) { const h = document.createElement('h3'); h.textContent = group; box.appendChild(h); } }
      n += 1;
      const row = document.createElement('div');
      const done = !!state.steps[s.id];
      const isCurrent = !done && !currentSet;
      row.className = 'step' + (done ? ' done' : '') + (isCurrent ? ' current' : '');
      if (!done) currentSet = true;
      const num = n;
      row.dataset.step = s.id;
      row.innerHTML = '<span class="n">' + n + '</span><div class="body">' + escapeHtml(window.rlTemp(s.text, window.rlUnits.get())) +
        stepIngsHtml(s, isCurrent) +
        '<br><button class="read" type="button" title="Read this step aloud">🔊 Read</button>' +
        (s.minutes ? ' <button class="timer" type="button">⏱ Start ' + s.minutes + ' min timer</button>' : '') + '</div>';
      const det = row.querySelector('details.step-ing');
      if (det) det.addEventListener('toggle', () => { ingOpen[s.id] = det.open; });
      row.addEventListener('click', (e) => {
        if (e.target.closest('details.step-ing')) { e.stopPropagation(); return; }   // opening the list is not ticking the step
        if (e.target.classList.contains('timer')) { e.stopPropagation(); startTimer(s.minutes * 60, 'Step ' + num); return; }
        if (e.target.classList.contains('read')) { e.stopPropagation(); readStep(s, num); return; }
        state.steps[s.id] = !state.steps[s.id]; save(); renderSteps();
        // hands-free: ticking a step off reads the next one
        if (state.steps[s.id] && speak && speak.prefs.auto) readNext();
      });
      box.appendChild(row);
    });
  }

  // ---- read aloud (tts.js) ----
  const speak = window.rlSpeak;
  const stepText = (s) => window.rlTemp(s.text, window.rlUnits.get());
  function readStep(s, num) {
    if (!speak) return;
    const row = document.querySelector('.step[data-step="' + s.id + '"]');
    document.querySelectorAll('.step.speaking').forEach((r) => r.classList.remove('speaking'));
    if (row) { row.classList.add('speaking'); row.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    const need = stepIngs(s).map((i) => (ingAmount(i) + ' ' + i.name).trim()).join(', ');
    speak.say('Step ' + num + '. ' + stepText(s) + (need ? ' You will need: ' + need + '.' : ''), () => { if (row) row.classList.remove('speaking'); });
  }
  function readNext() {
    const i = data.steps.findIndex((s) => !state.steps[s.id]);
    if (i === -1) { speak.say('That was the last step. Enjoy!'); return; }
    readStep(data.steps[i], i + 1);
  }
  function ingredientsText() {
    return Array.from(document.querySelectorAll('#ingList .item:not(.done)')).map((r) => r.textContent.replace(/^✓/, '').trim()).join('. ');
  }
  if (speak) {
    const btn = $('btnSpeak'), vbtn = $('btnVoice'), vp = $('voicePanel');
    const show = () => { btn.setAttribute('aria-pressed', String(!!speak.prefs.auto)); btn.classList.toggle('on', !!speak.prefs.auto); };
    show();
    btn.addEventListener('click', () => {
      speak.prefs.auto = !speak.prefs.auto; speak.save(); show();
      if (speak.prefs.auto) {                 // switch on: read the current step now, and the next as each is ticked
        document.querySelector('.tabs button[data-tab="steps"]').click();
        readNext();
      } else speak.stop();
    });
    vbtn.addEventListener('click', () => { vp.hidden = !vp.hidden; vbtn.setAttribute('aria-expanded', String(!vp.hidden)); });
    speak.panel(vp);
    $('readIng').addEventListener('click', () => {
      const t = ingredientsText();
      speak.say(t ? 'You will need: ' + t + '.' : 'Everything on the list is ticked off.');
    });
  } else {
    ['btnSpeak', 'btnVoice', 'readIng'].forEach((id) => { const el = $(id); if (el) el.hidden = true; });
  }

  function stepIngsHtml(s, isCurrent) {
    const list = stepIngs(s);
    if (!list.length) return '';
    const open = s.id in ingOpen ? ingOpen[s.id] : isCurrent;
    return '<details class="step-ing"' + (open ? ' open' : '') + '><summary>Ingredients for this step <span class="n">' + list.length + '</span></summary><ul>' +
      list.map((i) => '<li>' + ingHtml(i, true) + '</li>').join('') + '</ul></details>';
  }

  function escapeHtml(s) { return String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

  // ---- timers: timers.js keeps them across pages and rings until stopped ----
  function startTimer(seconds, label) {
    if (window.rlTimers) window.rlTimers.start(seconds, label, { title: data.title, url: location.pathname });
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
  window.rlUnits.control($('units'), () => render());
  function applyFont() { document.body.dataset.font = String(state.font || 1); }
  function render() { $('srvLabel').textContent = servingsLabel(); renderIngredients(); renderSteps(); }
  applyFont();
  render();

  // 2-second black 16x16 VP8 webm (generated with ffmpeg), ~1 KB
  const WEBM = document.body.dataset.webm || '';
})();
