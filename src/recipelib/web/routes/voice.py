"""Read aloud for cook mode: the voice list, and a WAV for a piece of text (see tts.py)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ... import tts

router = APIRouter()


@router.get("/api/tts/voices", name="tts_voices")
def voices():
    return tts.catalog()


@router.get("/api/tts", name="tts_speak")
def speak(text: str = Query("", max_length=tts.MAX_TEXT_CHARS * 2), voice: str | None = None,
          pitch: str = "0", pace: str = "0"):
    """GET so an <audio> element can play it straight from the URL, which keeps the tap
    that asked for it counting as the user's go-ahead to make sound (iOS, Android)."""
    if not text.strip():
        raise HTTPException(400, "nothing to read")
    try:
        data = tts.synthesize_wav(text, voice, pitch, pace)
    except tts.VoiceUnavailable as e:
        raise HTTPException(503, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "no such voice")
    return Response(data, media_type="audio/wav", headers={"Cache-Control": "private, max-age=86400"})
