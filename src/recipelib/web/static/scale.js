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
  function scale(root, factor, target) {
    target = target || (window.rlUnits ? window.rlUnits.get() : 'original');
    root.querySelectorAll('[data-q]').forEach((el) => {
      const q = parseFloat(el.dataset.q), qm = el.dataset.qmax ? parseFloat(el.dataset.qmax) : null;
      const unit = el.dataset.unit || null;
      const a = convert(q * factor, unit, target);
      let s = fmt(a.q, a.unit);
      if (qm != null) { const b = convert(qm * factor, unit, target); s += '–' + fmt(b.q, b.unit); }
      el.textContent = s;
      const u = el.parentElement && el.parentElement.querySelector('.u');
      if (u) u.textContent = a.label != null ? a.label : (u.dataset.raw || '');
    });
    root.querySelectorAll('[data-text]').forEach((el) => { el.textContent = temp(el.dataset.text, target); });
  }
  function load(id) {
    try { return JSON.parse(localStorage.getItem('rl_cook_' + id) || '{}'); } catch (e) { return {}; }
  }
  function save(id, state) {
    try { localStorage.setItem('rl_cook_' + id, JSON.stringify(state)); } catch (e) { /* private mode */ }
  }
  /* ---- unit conversion (US <-> metric) ---- */
  const UNITS = { tsp: ['vol', 4.92892], tbsp: ['vol', 14.7868], cup: ['vol', 236.588], floz: ['vol', 29.5735],
                  pint: ['vol', 473.176], quart: ['vol', 946.353], gallon: ['vol', 3785.41], ml: ['vol', 1], l: ['vol', 1000],
                  oz: ['mass', 28.3495], lb: ['mass', 453.592], g: ['mass', 1], kg: ['mass', 1000] };
  const US = { tsp: 1, tbsp: 1, cup: 1, floz: 1, pint: 1, quart: 1, gallon: 1, oz: 1, lb: 1 };
  const METRIC = { ml: 1, l: 1, g: 1, kg: 1 };
  const roundTo = (v, step) => Math.round(v / step) * step;
  const label = (unit, q) => {
    if (unit === 'cup') return q > 1 ? 'cups' : 'cup';
    if (unit === 'floz') return 'fl oz';
    if (unit === 'pint') return q > 1 ? 'pints' : 'pint';
    if (unit === 'quart') return q > 1 ? 'quarts' : 'quart';
    return unit;
  };
  /* Convert an amount to the target system ('us' | 'metric' | 'original').
   * Returns {q, unit, label} where label is null when nothing changed. */
  function convert(q, unit, target) {
    if (q == null || !unit || !UNITS[unit] || target === 'original') return { q, unit, label: null };
    if ((target === 'us' && US[unit]) || (target === 'metric' && METRIC[unit])) return { q, unit, label: null };
    const [fam, f] = UNITS[unit];
    const base = q * f;                        // ml or g
    if (target === 'metric') {
      if (fam === 'vol') return base >= 1000 ? { q: Math.round(base / 10) / 100, unit: 'l', label: 'l' }
                                             : { q: roundTo(base, base < 20 ? 1 : 5), unit: 'ml', label: 'ml' };
      return base >= 1000 ? { q: Math.round(base / 10) / 100, unit: 'kg', label: 'kg' } : { q: Math.round(base), unit: 'g', label: 'g' };
    }
    if (fam === 'vol') {
      if (base >= 59) { const c = roundTo(base / 236.588, 1 / 8); return { q: c, unit: 'cup', label: label('cup', c) }; }
      if (base >= 14) return { q: roundTo(base / 14.7868, 1 / 2), unit: 'tbsp', label: 'tbsp' };
      return { q: roundTo(base / 4.92892, 1 / 4), unit: 'tsp', label: 'tsp' };
    }
    if (base >= 454) return { q: roundTo(base / 453.592, 1 / 4), unit: 'lb', label: 'lb' };
    return { q: roundTo(base / 28.3495, 1 / 4), unit: 'oz', label: 'oz' };
  }
  /* Oven temperatures inside step text. */
  function temp(text, target) {
    if (!text || target === 'original') return text;
    // cookbook rounding: °C to the nearest 10, °F to the nearest 25 (400°F <-> 200°C, 350°F <-> 180°C)
    const toC = (f) => Math.round(((f - 32) * 5 / 9) / 10) * 10;
    const toF = (c) => Math.round((c * 9 / 5 + 32) / 25) * 25;
    if (target === 'metric') {
      return text.replace(/(\d{3})\s*(?:°|º|degrees?\s*)?F\b(?!\w)/gi, (m, d) => toC(+d) + '°C')
                 .replace(/(\d{3})\s*degrees\b(?!\s*[CF])/gi, (m, d) => +d >= 250 ? toC(+d) + '°C' : m);
    }
    return text.replace(/(\d{2,3})\s*(?:°|º|degrees?\s*)?C\b(?!\w)/gi, (m, d) => +d < 100 ? m : toF(+d) + '°F');
  }
  function unitsPref() { try { return localStorage.getItem('rl_units') || 'original'; } catch (e) { return 'original'; } }
  function setUnitsPref(v) { try { localStorage.setItem('rl_units', v); } catch (e) { /* ignore */ } }
  /* Wire a segmented control: buttons with data-u inside `box`; calls onChange(pref). */
  function unitsControl(box, onChange) {
    if (!box) return;
    const paint = () => box.querySelectorAll('button[data-u]').forEach((b) => b.classList.toggle('on', b.dataset.u === unitsPref()));
    box.querySelectorAll('button[data-u]').forEach((b) => b.addEventListener('click', () => { setUnitsPref(b.dataset.u); paint(); onChange(unitsPref()); }));
    paint();
  }

  window.rlFmt = fmt;
  window.rlScale = scale;
  window.rlConvert = convert;
  window.rlTemp = temp;
  window.rlUnits = { get: unitsPref, set: setUnitsPref, control: unitsControl };
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
      scale(document.querySelector('.recipe-body') || list, factor);
    };
    unitsControl(document.getElementById('units'), () => render());
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
