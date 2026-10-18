import base64
import json
import pytest
from fbc import gemini


class Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload
        self.text = json.dumps(payload)

    def json(self):
        return self._p


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k-test")
    monkeypatch.setenv("GEMINI_TEXT_MODEL", "gemini-test")


def test_generate_json_sends_schema_and_parses(monkeypatch):
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return Resp(200, {"candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}]})
    monkeypatch.setattr(gemini.requests, "post", post)
    out = gemini.generate_json("hi", {"type": "object"})
    assert out == {"a": 1}
    assert "gemini-test:generateContent" in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "k-test"
    assert seen["body"]["generationConfig"]["responseSchema"] == {"type": "object"}


def test_generate_json_grounded_parses_fenced_block(monkeypatch):
    def post(url, headers=None, json=None, timeout=None):
        assert json["tools"] == [{"google_search": {}}] and "responseSchema" not in json.get("generationConfig", {})
        return Resp(200, {"candidates": [{"content": {"parts": [{"text": 'Here:\n```json\n[{"id": "q1"}]\n```'}]}}]})
    monkeypatch.setattr(gemini.requests, "post", post)
    assert gemini.generate_json("x", {}, grounded=True) == [{"id": "q1"}]


def test_quota(monkeypatch):
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: Resp(429, {"error": {"code": 429}}))
    with pytest.raises(gemini.QuotaExceeded):
        gemini.generate_json("x", {})
    with pytest.raises(gemini.QuotaExceeded):
        gemini.tts_wav("hello", "Puck", "style")


def test_tts_wav(monkeypatch):
    wav = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 40
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, body=json)
        return Resp(200, {"status": "completed", "steps": [{"type": "model_output", "content": [
            {"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(wav).decode()}]}]})
    monkeypatch.setattr(gemini.requests, "post", post)
    out = gemini.tts_wav("Hello there", "Puck", "Energetic")
    assert out.startswith(b"RIFF")
    assert seen["url"].endswith("/v1beta/interactions")
    assert seen["body"]["model"] == "gemini-3.8-flash-tts"
    assert seen["body"]["generation_config"]["speech_config"] == [{"voice": "Puck"}]
    content = seen["body"]["input"][0]["content"][0]
    assert content["text"] == "Hello there"
    assert content["annotations"] == [{"type": "speech_metadata", "style": "Energetic"}]


def test_errors_never_echo_key(monkeypatch):
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: Resp(400, {"error": "bad key k-test"}))
    with pytest.raises(gemini.GeminiError) as e:
        gemini.generate_json("x", {})
    assert "k-test" not in str(e.value)
