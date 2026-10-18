"""Gemini REST client: JSON text generation (optionally grounded with Google Search) and TTS.
TTS follows the Developer Interactions API contract used by the media-use skill for
gemini-3.8-flash-tts: style goes in a speech_metadata annotation, the reply is a full WAV."""
import base64
import json
import re

import requests

from .config import env, TTS_MODEL

API = "https://generativelanguage.googleapis.com/v1beta"


class GeminiError(RuntimeError):
    pass


class QuotaExceeded(GeminiError):
    pass


def _headers() -> dict:
    return {"Content-Type": "application/json", "x-goog-api-key": env("GEMINI_API_KEY")}


def _post(url: str, body: dict, timeout: int = 180) -> dict:
    key = env("GEMINI_API_KEY")
    r = requests.post(url, headers=_headers(), json=body, timeout=timeout)
    if r.status_code == 429:
        raise QuotaExceeded("Gemini quota exceeded (429)")
    if r.status_code >= 400:
        raise GeminiError(f"Gemini HTTP {r.status_code}: {r.text[:300]}".replace(key, "[redacted]"))
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


def tts_wav(text: str, voice: str, style: str) -> bytes:
    content = {"type": "text", "text": text}
    if style:
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    body = {"model": TTS_MODEL, "input": [{"type": "user_input", "content": [content]}],
            "response_format": {"type": "audio", "mime_type": "audio/wav"},
            "generation_config": {"speech_config": [{"voice": voice}]}, "store": False}
    data = _post(f"{API}/interactions", body, timeout=120)
    if data.get("status") != "completed":
        raise GeminiError(f"TTS did not complete ({data.get('status')})")
    parts = [c for s in data.get("steps", []) if s.get("type") == "model_output"
             for c in s.get("content", []) if c.get("type") == "audio"]
    if len(parts) != 1 or parts[0].get("mime_type") != "audio/wav":
        raise GeminiError("TTS returned no single WAV block")
    wav = base64.b64decode(parts[0]["data"])
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise GeminiError("TTS returned invalid WAV")
    return wav
