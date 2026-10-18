"""Gemini REST client: JSON text generation (optionally grounded with Google Search) and TTS.
TTS follows the Developer Interactions API contract used by the media-use skill for
gemini-3.8-flash-tts: style goes in a speech_metadata annotation, the reply is a full WAV."""
import base64
import json
import re
import time

import requests

from .config import env, TTS_MODEL
from .errors import ResumeLater

API = "https://generativelanguage.googleapis.com/v1beta"


class GeminiError(RuntimeError):
    """`http` and `reason` (Google's error status, e.g. NOT_FOUND) are safe to print in public logs."""

    def __init__(self, msg: str = "", http: int | None = None, reason: str | None = None):
        super().__init__(msg)
        self.http, self.reason = http, reason


class QuotaExceeded(ResumeLater, GeminiError):
    pass


RETRIES = 4


def _headers() -> dict:
    return {"Content-Type": "application/json", "x-goog-api-key": env("GEMINI_API_KEY")}


def _post(url: str, body: dict, timeout: int = 180) -> dict:
    key = env("GEMINI_API_KEY")
    for attempt in range(RETRIES):
        r = requests.post(url, headers=_headers(), json=body, timeout=timeout)
        if r.status_code != 429:
            break
        if attempt == RETRIES - 1:
            raise QuotaExceeded("Gemini quota exceeded (429)")
        try:
            wait = float(getattr(r, "headers", {}).get("Retry-After", 0))
        except (TypeError, ValueError):
            wait = 0
        time.sleep(min(max(wait, 5 * 2 ** attempt), 60))
    if r.status_code >= 400:
        try:
            reason = r.json().get("error", {}).get("status")
        except (ValueError, AttributeError):
            reason = None
        reason = reason if isinstance(reason, str) and re.fullmatch(r"[A-Z_]{2,40}", reason) else None
        raise GeminiError(f"Gemini HTTP {r.status_code}: {r.text[:300]}".replace(key, "[redacted]"),
                          http=r.status_code, reason=reason)
    return r.json()


def _extract_json(text: str):
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start = min([i for i in (candidate.find("["), candidate.find("{")) if i >= 0], default=-1)
    if start < 0:
        raise GeminiError("no JSON in model reply")
    return json.JSONDecoder().raw_decode(candidate[start:])[0]


def generate_json(prompt: str, schema: dict, grounded: bool = False, temperature: float = 0.9):
    model = env("GEMINI_TEXT_MODEL")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature}}
    if grounded:
        body["tools"] = [{"google_search": {}}]
    else:
        body["generationConfig"].update(responseMimeType="application/json", responseSchema=schema)
    data = _post(f"{API}/models/{model}:generateContent", body)
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise GeminiError("Gemini returned no candidates")
    return json.loads(text) if not grounded else _extract_json(text)


def _pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return buf.getvalue()


def tts_wav(text: str, voice: str, style: str, model: str = TTS_MODEL) -> bytes:
    """3.8 models take style as a speech_metadata annotation and return WAV; older ones take it as
    a text prefix and return 16-bit PCM (audio/L16;rate=...), wrapped here into WAV."""
    modern = model.startswith("gemini-3.8-")
    content = {"type": "text", "text": text if modern or not style else
               f"Read the following text with this delivery: {style}\n\n{text}"}
    if style and modern:
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    body = {"model": model, "input": [{"type": "user_input", "content": [content]}],
            "response_format": {"type": "audio", "mime_type": "audio/wav"} if modern else {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice}]}, "store": False}
    data = _post(f"{API}/interactions", body, timeout=120)
    if data.get("status") != "completed":
        raise GeminiError(f"TTS did not complete ({data.get('status')})")
    parts = [c for s in data.get("steps", []) if s.get("type") == "model_output"
             for c in s.get("content", []) if c.get("type") == "audio"]
    if len(parts) != 1:
        raise GeminiError("TTS returned no single audio block")
    wav = base64.b64decode(parts[0]["data"])
    mime = parts[0].get("mime_type", "")
    if mime.lower().startswith("audio/l16"):
        m = re.search(r"rate=(\d+)", mime)
        wav = _pcm_to_wav(wav, int(m.group(1)) if m else 24000)
    elif mime != "audio/wav":
        raise GeminiError("TTS returned an unsupported audio format")
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise GeminiError("TTS returned invalid WAV")
    return wav
