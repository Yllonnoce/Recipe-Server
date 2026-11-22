"""Read aloud: Piper voices served as WAV for cook mode."""
import io
import json
import wave

import pytest


class FakeChunk:
    def __init__(self, pcm: bytes):
        self.audio_int16_bytes = pcm


class FakeVoice:
    """Stands in for piper.PiperVoice: one 16-bit sample per character, 22050 Hz."""

    class config:
        sample_rate = 22050
        length_scale = 1.0
        noise_scale = 0.667
        noise_w_scale = 0.8

    def __init__(self):
        self.calls = []

    def synthesize(self, text, syn_config=None):
        self.calls.append((text, syn_config))
        n = len(text) * (2 if syn_config and syn_config.length_scale > 1 else 1)
        yield FakeChunk(b"\x10\x00" * n)


def _voice_files(d, name, lang="en_US", quality="medium", speaker="lessac"):
    (d / f"{name}.onnx").write_bytes(b"model")
    (d / f"{name}.onnx.json").write_text(json.dumps({"audio": {"sample_rate": 22050, "quality": quality},
                                                     "language": {"code": lang}, "dataset": speaker}))


@pytest.fixture()
def voices(library, monkeypatch):
    from recipelib import tts
    d = library.voices_path
    d.mkdir(parents=True, exist_ok=True)
    _voice_files(d, "en_US-lessac-medium")
    _voice_files(d, "en_GB-cori-high", lang="en_GB", quality="high", speaker="cori")
    fake = FakeVoice()
    monkeypatch.setattr(tts, "_load_voice", lambda path: fake)
    tts.reset()
    yield fake
    tts.reset()


def test_the_voice_list_has_readable_names(client, voices):
    r = client.get("/api/tts/voices").json()
    assert r["enabled"] and r["default"] == "en_US-lessac-medium"
    assert {v["id"]: v["label"] for v in r["voices"]} == {
        "en_GB-cori-high": "Cori · British English · high",
        "en_US-lessac-medium": "Lessac · American English · medium",
    }


def test_a_step_comes_back_as_wav(client, voices):
    r = client.get("/api/tts", params={"text": "Stir the soup.", "voice": "en_GB-cori-high"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 22050 and w.getsampwidth() == 2 and w.getnchannels() == 1
        assert w.getnframes() == len("Stir the soup.")


def test_the_same_text_is_only_synthesized_once(client, voices):
    for _ in range(3):
        client.get("/api/tts", params={"text": "Preheat the oven."})
    assert len(voices.calls) == 1


def test_pace_and_pitch(client, voices):
    from recipelib.tts import shift_pitch
    client.get("/api/tts", params={"text": "abcd", "pace": "-20"})
    cfg = voices.calls[-1][1]
    assert cfg is not None and cfg.length_scale == pytest.approx(1 / 0.8)
    # higher pitch: the same sound played faster, so fewer samples
    assert len(shift_pitch(b"\x01\x00" * 1000, 1.2)) // 2 == round(1000 / 1.2)
    # out-of-range values are held to the limits instead of failing
    assert client.get("/api/tts", params={"text": "x", "pitch": "500", "pace": "nonsense"}).status_code == 200


def test_unknown_or_sneaky_voice_names_are_refused(client, voices):
    assert client.get("/api/tts", params={"text": "hi", "voice": "nobody"}).status_code == 404
    assert client.get("/api/tts", params={"text": "hi", "voice": "../../etc/passwd"}).status_code == 404


def test_without_voices_the_page_is_told_to_use_the_browser(client, library):
    from recipelib import tts
    tts.reset()
    r = client.get("/api/tts/voices").json()
    assert r["voices"] == [] and r["enabled"] is False and r["reason"]
    assert client.get("/api/tts", params={"text": "hi"}).status_code == 503


@pytest.mark.parametrize("raw, spoken", [
    ("Bake at 350°F for 1 1/2 hours.", "Bake at 350 degrees Fahrenheit for 1 and a half hours."),
    ("Heat to 180 °C.", "Heat to 180 degrees Celsius."),
    ("Add 2 tbsp butter and ½ cup milk.", "Add 2 tablespoons butter and half cup milk."),
    ("Stir in 1 tsp salt, then 3/4 cup flour.", "Stir in 1 teaspoon salt, then three quarters cup flour."),
    ("Simmer 10-12 min.", "Simmer 10 to 12 minutes."),
    ("Add 500 g beef and 250 ml stock; cook 1 hr.", "Add 500 grams beef and 250 milliliters stock, cook 1 hour."),
    ("**Note:** see https://example.com", "Note, see link"),
])
def test_recipe_text_is_made_speakable(raw, spoken):
    from recipelib.tts import speakable
    assert speakable(raw) == spoken


def test_cook_mode_has_read_aloud(client, library):
    from recipelib.db.engine import session_scope
    from recipelib.db.models import Recipe, Step
    with session_scope() as s:
        r = Recipe(title="Soup", status="ready")
        r.steps.append(Step(position=0, text="Chop the onion."))
        s.add(r)
        s.flush()
        rid = r.id
    html = client.get(f"/recipes/{rid}/cook").text
    assert 'id="btnSpeak"' in html and 'id="voicePanel"' in html and "tts.js" in html


def test_voice_download_addresses():
    from recipelib.tts import VOICES_URL, voice_url
    assert voice_url("en_US-lessac-medium") == f"{VOICES_URL}/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
    assert voice_url("en_GB-northern_english_male-medium").endswith("/en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium.onnx")
    with pytest.raises(ValueError):
        voice_url("../../etc/passwd")


def test_the_standard_voices_list_ships_with_the_app():
    from recipelib.tts import standard_voices, voice_url
    names = standard_voices()
    assert "en_US-lessac-medium" in names and len(names) == len(set(names))
    for n in names:
        voice_url(n)          # every entry is a real Piper voice name


def test_missing_standard_voices_are_fetched_and_present_ones_kept(library, monkeypatch):
    from recipelib import tts
    library.tts_fetch_voices = True     # tests turn fetching off by default (conftest)
    monkeypatch.setattr(tts, "piper_installed", lambda: True)
    monkeypatch.setattr(tts, "standard_voices", lambda: ["en_US-lessac-medium", "en_GB-cori-high"])
    d = library.voices_path
    d.mkdir(parents=True, exist_ok=True)
    _voice_files(d, "en_US-lessac-medium")
    fetched = []
    monkeypatch.setattr(tts, "download_voice", lambda name, folder=None, say=print: fetched.append(name))
    assert tts.ensure_voices() == ["en_GB-cori-high"]
    assert fetched == ["en_GB-cori-high"]


def test_no_fetching_when_turned_off_or_without_piper(library, monkeypatch):
    from recipelib import tts
    fetched = []
    monkeypatch.setattr(tts, "download_voice", lambda name, folder=None, say=print: fetched.append(name))
    monkeypatch.setattr(tts, "piper_installed", lambda: False)
    assert tts.ensure_voices() == []
    monkeypatch.setattr(tts, "piper_installed", lambda: True)
    library.tts_fetch_voices = False
    assert tts.ensure_voices() == []
    assert fetched == []


def test_a_failed_download_does_not_stop_the_others(library, monkeypatch):
    from recipelib import tts
    library.tts_fetch_voices = True     # tests turn fetching off by default (conftest)
    monkeypatch.setattr(tts, "piper_installed", lambda: True)
    monkeypatch.setattr(tts, "standard_voices", lambda: ["en_US-amy-medium", "en_GB-alan-medium"])

    def flaky(name, folder=None, say=print):
        if name == "en_US-amy-medium":
            raise OSError("network down")
    monkeypatch.setattr(tts, "download_voice", flaky)
    assert tts.ensure_voices() == ["en_GB-alan-medium"]
