/* Shared quantity formatting + servings scaling.
 * window.rlFmt(q, unit) -> "1 ½" / "250" ; window.rlScale(root, factor) rescales
 * every [data-q] element under root. The chosen factor is remembered per recipe
 * in localStorage under rl_cook_<id> so the recipe page and cook mode agree. */
(function () {
  const GLYPH = { '1/2': '½', '1/3': '⅓', '2/3': '⅔', '1/4': '¼', '3/4': '¾', '1/8': '⅛', '3/8': '⅜', '5/8': '⅝', '7/8': '⅞' };
  function fmt(q, unit) {
    if (q == null || isNaN(q)) return '';
    const metric = unit && ['g', 'kg', 'ml', 'l'].indexOf(unit) !== -1;
    if (metric) return (Math.round(q * 10) / 10).toString();
    const whole = Math.floor(q + 1e-9);
    const rem = q - whole;
    if (rem < 0.04) return String(whole);
    let best = null, bestErr = 1;
    [[1, 8], [1, 4], [1, 3], [3, 8], [1, 2], [5, 8], [2, 3], [3, 4], [7, 8]].forEach(([n, d]) => {
      const err = Math.abs(rem - n / d);
      if (err < bestErr) { bestErr = err; best = n + '/' + d; }
    });
    if (bestErr > 0.06) return (Math.round(q * 100) / 100).toString();
    const g = GLYPH[best] || best;
    return whole ? whole + ' ' + g : g;
  }
  function scale(root, factor) {
    root.querySelectorAll('[data-q]').forEach((el) => {
      const q = parseFloat(el.dataset.q), qm = el.dataset.qmax ? parseFloat(el.dataset.qmax) : null;
      const unit = el.dataset.unit || null;
      let s = fmt(q * factor, unit);
      if (qm != null) s += '–' + fmt(qm * factor, unit);
      el.textContent = s;
    });
  }
  function load(id) {
    try { return JSON.parse(localStorage.getItem('rl_cook_' + id) || '{}'); } catch (e) { return {}; }
  }
  function save(id, state) {
    try { localStorage.setItem('rl_cook_' + id, JSON.stringify(state)); } catch (e) { /* private mode */ }
  }
  window.rlFmt = fmt;
  window.rlScale = scale;
  window.rlScaleState = { load, save };

  /* Recipe page: a stepper next to the servings, rescaling the ingredient list. */
  document.addEventListener('DOMContentLoaded', () => {
    const box = document.getElementById('scaler');
    if (!box) return;
    const id = box.dataset.recipe, base = parseFloat(box.dataset.servings) || null;
    const list = document.getElementById('ingredients');
    const label = box.querySelector('.srv');
    const shop = document.querySelector('input[name=servings]');
    const steps = [0.5, 1, 1.5, 2, 3, 4];
    let state = load(id);
    let factor = state.factor || 1;
    const render = () => {
      if (base) {
        const n = Math.round(base * factor * 10) / 10;
        label.textContent = n + ' serving' + (n === 1 ? '' : 's');
        if (shop) shop.value = n;
      } else {
        label.textContent = '×' + factor;
      }
      box.classList.toggle('scaled', factor !== 1);
      scale(list, factor);
    };
    const bump = (dir) => {
      if (base) factor = Math.max(1, Math.round(base * factor + dir)) / base;
      else { let i = steps.indexOf(factor); if (i === -1) i = 1; factor = steps[Math.max(0, Math.min(steps.length - 1, i + dir))]; }
      state = load(id); state.factor = factor; save(id, state); render();
    };
    box.querySelector('.minus').addEventListener('click', () => bump(-1));
    box.querySelector('.plus').addEventListener('click', () => bump(1));
    box.querySelector('.reset').addEventListener('click', () => { factor = 1; state = load(id); state.factor = 1; save(id, state); render(); });
    render();
  });
})();
