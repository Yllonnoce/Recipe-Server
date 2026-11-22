/* Read aloud in cook mode.
   Uses the server's Piper voices (/api/tts returns a WAV per piece of text). When the server
   has none (Piper not installed, no voices, turned off) it falls back to this device's own
   speech (speechSynthesis), with the same pitch and pace. Voice, pitch, pace and "read each
   step as I go" are kept per device. Sound must start from a tap, so every say() here is
   called from a click handler. */
(function () {
  const KEY = 'rl_tts';
  const prefs = { voice: null, pitch: 0, pace: 0, auto: false };
  try { Object.assign(prefs, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch (e) { /* private mode */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (e) { /* ignore */ } };

  let cat = { enabled: false, voices: [], default: null, reason: 'still loading the voice list' };
  const ready = fetch('/api/tts/voices', { cache: 'no-store' }).then((r) => r.json())
    .then((c) => { cat = c; }).catch(() => { cat = { enabled: false, voices: [], reason: 'the server could not be reached' }; });

  const audio = new Audio();
  audio.preload = 'auto';
  let done = null, lastText = '';
  const finish = (ok) => { const f = done; done = null; if (f) f(ok); };

  function browserSay(text) {
    if (!('speechSynthesis' in window)) { finish(false); return; }
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1 + prefs.pace / 100;
    u.pitch = 1 + prefs.pitch / 100;
    u.onend = () => finish(true);
    u.onerror = () => finish(false);
    speechSynthesis.speak(u);
  }

  function stop() {
    audio.pause();
    audio.removeAttribute('src');
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    finish(false);
  }

  /* Read `text`; onEnd(true) when it finished, onEnd(false) if stopped or it failed. */
  function say(text, onEnd) {
    stop();
    done = onEnd || null;
    lastText = text;
    if (!cat.enabled || !cat.voices.length) { browserSay(text); return; }
    const voice = cat.voices.some((v) => v.id === prefs.voice) ? prefs.voice : cat.default;
    audio.src = '/api/tts?' + new URLSearchParams({ text, voice, pitch: prefs.pitch, pace: prefs.pace });
    audio.play().catch(() => { if (audio.getAttribute('src')) { audio.removeAttribute('src'); browserSay(text); } });
  }
  audio.addEventListener('ended', () => finish(true));
  audio.addEventListener('error', () => {            // the server failed this one: say it anyway
    if (!audio.getAttribute('src')) return;
    audio.removeAttribute('src');
    browserSay(lastText);
  });

  /* The voice panel in the cook bar. */
  function panel(root) {
    if (!root) return;
    const sel = root.querySelector('#voiceSel'), pitch = root.querySelector('#voicePitch'), pace = root.querySelector('#voicePace');
    const note = root.querySelector('#voiceNote');
    const pv = root.querySelector('#pitchVal'), sv = root.querySelector('#paceVal');
    const sign = (n) => (n > 0 ? '+' : '') + n + '%';
    pitch.value = prefs.pitch; pace.value = prefs.pace;
    pv.textContent = sign(prefs.pitch); sv.textContent = sign(prefs.pace);
    pitch.addEventListener('input', () => { prefs.pitch = +pitch.value; pv.textContent = sign(prefs.pitch); save(); });
    pace.addEventListener('input', () => { prefs.pace = +pace.value; sv.textContent = sign(prefs.pace); save(); });
    sel.addEventListener('change', () => { prefs.voice = sel.value; save(); });
    root.querySelector('#voiceTest').addEventListener('click', () => say('Preheat the oven to 350°F. Stir in 1 1/2 cups of flour.'));
    ready.then(() => {
      sel.innerHTML = '';
      if (cat.enabled && cat.voices.length) {
        cat.voices.forEach((v) => { const o = document.createElement('option'); o.value = v.id; o.textContent = v.label; sel.appendChild(o); });
        sel.value = cat.voices.some((v) => v.id === prefs.voice) ? prefs.voice : cat.default;
        note.textContent = cat.voices.length + ' voices on the server.';
      } else {
        const o = document.createElement('option'); o.textContent = "This device's voice"; sel.appendChild(o); sel.disabled = true;
        note.textContent = 'Using this device’s own voice: ' + (cat.reason || 'no server voices') + '.';
      }
    });
  }

  window.rlSpeak = { say, stop, prefs, save, ready, panel, catalog: () => cat };
})();
