"""Read aloud: Piper text-to-speech for cook mode.

Ported from the 3dObject / OllamaWeb voice service (see docs/voice.md); those projects
are not imported or changed. What came across:
  - voices are Piper ``*.onnx`` + ``*.onnx.json`` pairs in one folder; a voice's id is the
    file stem (``en_US-lessac-medium``) and a name cannot step out of the folder;
  - voices load lazily and stay cached, and all synthesis runs on one thread, because
    espeak-ng (Piper's phonemizer) keeps global state and is not re-entrant;
  - pace (percent faster, +-30) divides the voice's ``length_scale``; pitch (percent
    higher, +-20) is made by synthesizing that much slower and resampling back to the
    same length, which raises pitch and formants together.
What is new here: one WAV per request instead of a WebSocket stream (a recipe step is a
sentence or two), a small cache because cooks replay steps, and `speakable()`, which
reads temperatures, fractions and unit abbreviations the way a person would.
"""
from __future__ import annotations

import io
import json
import re
import threading
import wave
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import get_settings

MAX_TEXT_CHARS = 2000
PITCH_RANGE = 20.0      # beyond this the voice turns cartoon (up) or giant (down)
PACE_RANGE = 30.0
CACHE_SIZE = 96         # WAVs kept; a recipe's steps at one voice/pitch/pace fit easily

_LANG = {"en_US": "American English", "en_GB": "British English", "en_AU": "Australian English", "de_DE": "German",
         "fr_FR": "French", "es_ES": "Spanish", "es_MX": "Mexican Spanish", "it_IT": "Italian", "nl_NL": "Dutch"}


class VoiceUnavailable(RuntimeError):
    """Read aloud cannot work here (turned off, Piper not installed, or no voices)."""


def _load_voice(path: Path):
    from piper import PiperVoice  # heavy import, deferred
    return PiperVoice.load(str(path))


class _Registry:
    def __init__(self, folder: Path):
        self.dir = Path(folder)
        self._voices: dict[str, object] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="piper")
        self._cache: OrderedDict[tuple, bytes] = OrderedDict()

    def names(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        return sorted(p.name[: -len(".onnx")] for p in self.dir.glob("*.onnx"))

    def path(self, name: str) -> Path:
        p = (self.dir / f"{name}.onnx").resolve()
        if p.parent != self.dir.resolve() or not p.is_file():
            raise FileNotFoundError(f"voice not found: {name}")
        return p

    def _get(self, name: str):
        with self._lock:
            v = self._voices.get(name)
            if v is None:
                v = _load_voice(self.path(name))
                self._voices[name] = v
            return v

    def _synthesize_sync(self, name: str, text: str, pitch: float, pace: float) -> tuple[int, bytes]:
        voice = self._get(name)
        ratio = 1 + pitch / 100
        length = ratio / (1 + pace / 100)
        syn_config = None
        if length != 1:
            from piper.config import SynthesisConfig
            c = voice.config
            syn_config = SynthesisConfig(length_scale=c.length_scale * length, noise_scale=c.noise_scale,
                                         noise_w_scale=c.noise_w_scale)
        pcm = b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(text, syn_config=syn_config))
        return voice.config.sample_rate, shift_pitch(pcm, ratio)

    def wav(self, name: str, text: str, pitch: float, pace: float) -> bytes:
        key = (name, text, pitch, pace)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        self.path(name)  # unknown names fail here, before the synthesis thread
        rate, pcm = self._executor.submit(self._synthesize_sync, name, text, pitch, pace).result()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        data = buf.getvalue()
        with self._lock:
            self._cache[key] = data
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return data


_registry: _Registry | None = None
_reg_lock = threading.Lock()


def registry() -> _Registry:
    global _registry
    folder = get_settings().voices_path
    with _reg_lock:
        if _registry is None or _registry.dir != folder:
            _registry = _Registry(folder)
        return _registry


def reset() -> None:
    """Forget loaded voices and cached audio (tests, or after adding voices)."""
    global _registry
    with _reg_lock:
        _registry = None


def piper_installed() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except ImportError:
        return False


def _describe(path: Path) -> dict:
    name = path.name[: -len(".onnx")]
    lang, _, rest = name.partition("-")
    speaker, _, quality = rest.rpartition("-")
    try:
        meta = json.loads(path.with_name(path.name + ".json").read_text(encoding="utf-8"))
        lang = (meta.get("language") or {}).get("code") or lang
        quality = (meta.get("audio") or {}).get("quality") or quality
    except (OSError, ValueError):
        pass
    who = speaker.replace("_", " ").title() if speaker else name
    return {"id": name, "label": f"{who} · {_LANG.get(lang, lang)} · {quality}", "lang": lang, "quality": quality}


def catalog() -> dict:
    """What cook mode needs to offer voices, or to fall back to the browser's own speech."""
    cfg = get_settings()
    reg = registry()
    voices = [_describe(reg.dir / f"{n}.onnx") for n in reg.names()]
    reason = None
    if not cfg.tts_enabled:
        reason = "read aloud is turned off on the server (tts_enabled = false)"
    elif not piper_installed():
        reason = "Piper is not installed on the server (pip install 'recipelib[voice]')"
    elif not voices:
        reason = f"no Piper voices in {reg.dir}"
    names = [v["id"] for v in voices]
    default = cfg.tts_voice if cfg.tts_voice in names else (names[0] if names else None)
    return {"enabled": reason is None, "reason": reason, "voices": voices if reason is None else [], "default": default}


def clamp(value, limit: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN
        return 0.0
    return max(-limit, min(limit, v))


def synthesize_wav(text: str, voice: str | None = None, pitch=0, pace=0) -> bytes:
    """A WAV of `text` read by `voice`. Raises VoiceUnavailable or FileNotFoundError."""
    cat = catalog()
    if not cat["enabled"]:
        raise VoiceUnavailable(cat["reason"])
    text = speakable(text)[:MAX_TEXT_CHARS]
    return registry().wav(voice or cat["default"], text, round(clamp(pitch, PITCH_RANGE), 1), round(clamp(pace, PACE_RANGE), 1))


def shift_pitch(pcm: bytes, ratio: float) -> bytes:
    """16-bit mono PCM played `ratio` times as fast at the same sample rate: higher and shorter by that much."""
    import numpy as np
    src = np.frombuffer(pcm, dtype="<i2")
    if ratio == 1 or len(src) < 2:
        return pcm
    at = np.arange(max(1, round(len(src) / ratio))) * ratio
    return np.interp(at, np.arange(len(src)), src).round().astype("<i2").tobytes()


# ---- recipe text that reads well aloud ------------------------------------

_FRACTIONS = {"1/2": "a half", "1/3": "a third", "2/3": "two thirds", "1/4": "a quarter", "3/4": "three quarters",
              "1/8": "an eighth", "3/8": "three eighths", "5/8": "five eighths", "7/8": "seven eighths"}
_ALONE = {"1/2": "half", "1/3": "one third", "2/3": "two thirds", "1/4": "one quarter", "3/4": "three quarters",
          "1/8": "one eighth", "3/8": "three eighths", "5/8": "five eighths", "7/8": "seven eighths"}
_VULGAR = {"½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"}
# abbreviation -> (singular, plural); only after a number
_UNITS = {"tbsp": ("tablespoon", "tablespoons"), "tbs": ("tablespoon", "tablespoons"), "tsp": ("teaspoon", "teaspoons"),
          "g": ("gram", "grams"), "kg": ("kilogram", "kilograms"), "mg": ("milligram", "milligrams"),
          "ml": ("milliliter", "milliliters"), "l": ("liter", "liters"), "oz": ("ounce", "ounces"),
          "lb": ("pound", "pounds"), "lbs": ("pound", "pounds"), "min": ("minute", "minutes"),
          "mins": ("minute", "minutes"), "hr": ("hour", "hours"), "hrs": ("hour", "hours"),
          "cm": ("centimeter", "centimeters"), "mm": ("millimeter", "millimeters"), "qt": ("quart", "quarts"),
          "pt": ("pint", "pints"), "sec": ("second", "seconds"), "secs": ("second", "seconds")}
_UNIT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")\b(?=[\s,;:.)!?]|$)", re.I)


def speakable(text: str) -> str:
    t = text or ""
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)                    # markdown links
    t = re.sub(r"https?://\S+|www\.\S+", "link", t)
    t = re.sub(r"[*_#>`|~]", " ", t)
    for ch, frac in _VULGAR.items():                                   # ½ -> 1/2, 1½ -> 1 1/2
        t = re.sub(r"(\d)\s*" + ch, r"\1 " + frac, t).replace(ch, frac)
    t = re.sub(r"(\d)\s*[°º]\s*F\b", r"\1 degrees Fahrenheit", t)
    t = re.sub(r"(\d)\s*[°º]\s*C\b", r"\1 degrees Celsius", t)
    t = re.sub(r"(\d)\s*[°º]", r"\1 degrees", t)
    t = re.sub(r"\b(\d+)\s+(\d)/(\d)\b", lambda m: f"{m[1]} and {_FRACTIONS.get(m[2] + '/' + m[3], m[2] + ' over ' + m[3])}", t)
    t = re.sub(r"(?<![\d/])(\d)/(\d)(?![\d/])", lambda m: _ALONE.get(m[1] + "/" + m[2], f"{m[1]} over {m[2]}"), t)
    t = re.sub(r"\b(\d+)\s*[-–]\s*(\d+)\b", r"\1 to \2", t)            # 10-12 -> 10 to 12
    t = _UNIT_RE.sub(lambda m: f"{m[1]} {_UNITS[m[2].lower()][0 if m[1] in ('1', '1.0') else 1]}", t)
    t = re.sub(r"\s*(?<!\d)[:;](?!\d)\s*", ", ", t)                     # colons/semicolons pause (not 10:30)
    t = re.sub(r"\s*[–—]\s*", ", ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([.,!?])", r"\1", t)
    t = re.sub(r",(\s*,)+", ",", t)
    t = re.sub(r"[.,]\s*,", ".", t)
    return re.sub(r"^[,\s]+|[,\s]+$", "", t)


# ---- getting voices ----------------------------------------------------------

VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def voice_url(name: str) -> str:
    """Where Piper's own voice collection keeps a voice: en_US-lessac-medium ->
    .../en/en_US/lessac/medium/en_US-lessac-medium.onnx"""
    lang, _, rest = name.partition("-")
    speaker, _, quality = rest.rpartition("-")
    if not (re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}", lang) and speaker and quality):
        raise ValueError(f"not a Piper voice name: {name!r} (like en_US-lessac-medium)")
    return f"{VOICES_URL}/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{name}.onnx"


def download_voice(name: str, folder: Path | None = None, say=print) -> Path:
    """Fetch a voice (.onnx and .onnx.json) into the voices folder; a voice already there is kept."""
    import httpx
    folder = Path(folder or get_settings().voices_path)
    folder.mkdir(parents=True, exist_ok=True)
    url = voice_url(name)
    dest = folder / f"{name}.onnx"
    for src, out in ((url + ".json", folder / f"{name}.onnx.json"), (url, dest)):
        if out.is_file() and out.stat().st_size > 0:
            continue
        part = out.with_name(out.name + ".part")
        with httpx.stream("GET", src, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            got = 0
            with part.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    got += len(chunk)
            say(f"  {out.name}: {got // 1048576} MB" + (f" of {total // 1048576} MB" if total else ""))
        part.replace(out)
    reset()
    return dest


def standard_voices() -> list[str]:
    """The voices every server should have: voices.txt, shipped with the app (and with GitHub updates)."""
    from importlib import resources
    text = (resources.files("recipelib") / "voices.txt").read_text(encoding="utf-8")
    return list(dict.fromkeys(line.split("#")[0].strip() for line in text.splitlines() if line.split("#")[0].strip()))


def ensure_voices(say=None) -> list[str]:
    """Download the standard voices this server is missing; returns the ones it got.
    A voice that fails to download is skipped (and tried again next time)."""
    import logging
    log = logging.getLogger(__name__)
    say = say or log.info
    cfg = get_settings()
    if not (cfg.tts_enabled and cfg.tts_fetch_voices and piper_installed()):
        return []
    have = set(registry().names())
    got = []
    for name in standard_voices():
        if name in have:
            continue
        say(f"downloading read-aloud voice {name}")
        try:
            download_voice(name, say=say)
            got.append(name)
        except Exception as e:  # noqa: BLE001
            log.warning("voice %s not downloaded: %s: %s", name, type(e).__name__, e)
    return got
