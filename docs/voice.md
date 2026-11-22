# Read aloud (Piper voices)

Cook mode can read a recipe aloud: each step, or the ingredient list, in a natural voice
made on the server by [Piper](https://github.com/OHF-Voice/piper1-gpl). It runs on the CPU
(no graphics card needed) and needs no internet once a voice is downloaded.

## Using it

In cook mode:

- **🔊** in the top bar switches on hands-free reading. The current step is read straight
  away, and each time you tick a step off, the next one is read.
- **🔊 Read** on a step reads just that step; **🔊 Read** next to *Ingredients* reads what
  is still unticked.
- **Voice** (⚙ on a phone) opens the voice choice, **pitch** (±20 %) and **pace** (±30 %),
  with **▶ Try it**. These are remembered on each device separately, so the kitchen tablet
  and a phone can have different voices.

Sound has to start from a tap (a browser rule), which is why reading starts when you tap
🔊, Read or a step, never by itself.

If the server cannot read aloud (Piper not installed, no voices, or turned off), the page
uses the device's own built-in voice instead, with the same pitch and pace.

## Voices

Voices are pairs of files, `NAME.onnx` and `NAME.onnx.json`, in `RecipeLibrary/voices`
(change it with `voices_dir` in the config). A voice's name says its language, speaker and
quality: `en_US-lessac-medium`, `en_GB-cori-high`. *Medium* and *high* sound best; *low*
voices are smaller and a little rougher.

```bash
recipes voices list                                   # what this server has (* = default)
recipes voices add en_US-lessac-medium en_GB-cori-high
```

`add` downloads from Piper's voice collection on Hugging Face (about 60 MB per medium
voice). Listen to them all at <https://rhasspy.github.io/piper-samples>.

### Getting the same voices on every server (GitHub updates)

The voice files are too big for git, so git carries the **list** instead:
[`src/recipelib/voices.txt`](../src/recipelib/voices.txt). Every server downloads any
voice on that list it does not have, in the background each time it starts. Because
**Settings → Update now** pulls from GitHub, installs, and restarts, an update brings:

1. Piper itself: the updater installs the `voice` extra, and carries on without it if
   Piper will not install on that machine;
2. the standard voices: fetched after the restart, without blocking anything.

To put a new voice on every instance, add its name to `voices.txt`, commit and push; each
server picks it up at its next update. Removing a name does not delete the voice anywhere.
`recipes voices sync` does the same download by hand (the installers run it), and
`recipes config set tts_fetch_voices false` stops a machine from downloading at all.
Voices added by hand with `recipes voices add` stay on that machine only.

Config keys (`recipes config set KEY VALUE`):

| key | default | |
|---|---|---|
| `tts_enabled` | `true` | `false` makes every device use its own voice |
| `tts_voice` | `en_US-lessac-medium` | the voice a device uses until it picks one |
| `voices_dir` | `RecipeLibrary/voices` | where the voices are |
| `tts_fetch_voices` | `true` | download missing `voices.txt` voices at startup |

`recipes doctor` (and Settings) shows whether read aloud is working and how many voices
there are.

## How it works

- `src/recipelib/tts.py`: voices, synthesis, and `speakable()`.
- `src/recipelib/web/routes/voice.py`: `GET /api/tts/voices` (the list, plus whether
  the server can read at all) and `GET /api/tts?text=&voice=&pitch=&pace=` (a WAV).
- `src/recipelib/web/static/tts.js`: playback, the device's choices (`localStorage`
  key `rl_tts`), and the fallback to the browser's `speechSynthesis`.
- `src/recipelib/web/static/cook.js`: the buttons and hands-free reading.

Details worth knowing:

- **One synthesis thread.** Piper uses espeak-ng to turn words into sounds, and espeak-ng
  keeps global state, so every request is queued onto one worker thread. A step of one or
  two sentences takes about a second the first time a voice is used (loading it), well
  under a second after that.
- **Cache.** The last 96 WAVs are kept in memory, so re-reading a step is instant.
- **Pace** divides the voice's `length_scale`. **Pitch** is made by synthesizing that much
  slower and then resampling back to the original length, which raises pitch and voice
  together like a smaller or larger speaker. Beyond about ±15 % it starts to sound odd.
- **`speakable()`** rewrites recipe text for speaking: `350°F` → "350 degrees
  Fahrenheit", `1 1/2` → "1 and a half", `½ cup` → "half cup", `2 tbsp` → "2 tablespoons",
  `10-12 min` → "10 to 12 minutes", and strips links and markdown.
- **GET, not a WebSocket.** A recipe step is short, so one WAV per request is simpler, and
  an `<audio>` element playing the URL keeps the tap that asked for it counting as
  permission to make sound on iOS and Android.

## Where it came from

The approach was taken from the voice feature of the **3dObject** project
(`dev/3dObject/voice/`), which uses the Piper engine from **OllamaWeb**
(`dev/OllamaWeb/ollamaweb/tts.py`). Neither project was changed, and this project does not
import from them: the code was rewritten here.

| Here | Taken from |
|---|---|
| `_Registry` (lazy voice cache, single Piper thread, a name cannot leave the voices folder) | OllamaWeb `ollamaweb/tts.py` `VoiceRegistry` |
| pitch / pace in `_synthesize_sync`, `shift_pitch` | 3dObject `voice/viewer_voice/voices.py` (`MoodVoices._synthesize_mood_sync`, `shift_pitch`), mood removed |
| ±20 % pitch, ±30 % pace limits, `clamp` | 3dObject `voice/viewer_voice/session.py` (`PITCH_RANGE`, `PACE_RANGE`, `_percent`) |
| `speakable()` | inspired by OllamaWeb `text_clean.py` `clean_for_tts`, rewritten because that one deletes `°` and `/`, which recipes need |
| 40 voices in `RecipeLibrary/voices` on the main server | copied from `3dObject/voice/voices` and `OllamaWeb/voices` |

What 3dObject has that is **not** used here: the WebSocket stream of sentence clips
(`voice.js` `ClipQueue`), speech-to-text (Whisper), turn-taking, moods, and per-character
voice settings in `*.persona.json`.

Both 3dObject and this project pin `piper-tts==1.8.0` (onnxruntime and numpy come with it).

## Licence

Piper (`piper-tts`, from piper1-gpl) is licensed GPL-3.0. Recipe Library installs it as a
separate package through the optional `voice` extra and does not copy its code. If you ever
ship Recipe Library bundled with Piper, check what the GPL requires for that. Each voice
has its own licence in its `MODEL_CARD` on Hugging Face.
