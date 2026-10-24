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
    monkeypatch.setattr(gemini, "_last_text_call", 0.0)
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


def test_http_error_carries_safe_status_and_reason(monkeypatch):
    payload = {"error": {"code": 404, "status": "NOT_FOUND", "message": "models/x is not found"}}
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: Resp(404, payload))
    with pytest.raises(gemini.GeminiError) as e:
        gemini.generate_json("x", {})
    assert e.value.http == 404 and e.value.reason == "NOT_FOUND"


def test_cli_failure_log_includes_http_and_reason(monkeypatch, capsys):
    from fbc import cli
    err = gemini.GeminiError("secret text", http=400, reason="INVALID_ARGUMENT")
    monkeypatch.setattr(cli, "cmd_generate", lambda *a, **k: (_ for _ in ()).throw(err))
    monkeypatch.setattr(cli, "ContentRepo", lambda *a, **k: object())
    assert cli.main(["generate", "--dry-run", "--week", "2026-W45"]) == 1
    out = capsys.readouterr().out
    assert '"http": 400' in out and '"reason": "INVALID_ARGUMENT"' in out and "secret text" not in out


def _quota_body(quota_id):
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": quota_id}]}]}}


def test_daily_quota_fails_fast_without_retrying(monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    monkeypatch.setattr(gemini.requests, "post",
                        lambda *a, **k: calls.append(1) or Resp(429, _quota_body("GenerateRequestsPerDayPerProjectPerModel-FreeTier")))
    with pytest.raises(gemini.QuotaExceeded) as e:
        gemini.generate_json("x", {})
    assert len(calls) == 1 and e.value.reason == "PER_DAY" and e.value.http == 429


def test_minute_quota_waits_a_full_minute_then_succeeds(monkeypatch):
    replies = [Resp(429, _quota_body("GenerateRequestsPerMinutePerProjectPerModel-FreeTier")),
               Resp(200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})]
    sleeps = []
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: replies.pop(0))
    assert gemini.generate_json("x", {}) == {}
    assert max(sleeps) >= 60


def test_text_calls_are_paced_for_free_tier_rpm(monkeypatch):
    clock = [1000.0]
    sleeps = []
    monkeypatch.setattr(gemini.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(gemini.time, "sleep", lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)))
    ok = Resp(200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: ok)
    gemini.generate_json("a", {})
    gemini.generate_json("b", {})
    assert sleeps and abs(sum(sleeps) - gemini.TEXT_MIN_GAP_S) < 0.01


def test_model_busy_503_is_retried_then_succeeds(monkeypatch):
    """Regression: Friday's W44 generation died on one 'model is experiencing high demand' 503."""
    busy = Resp(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})
    replies = [busy, busy, Resp(200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})]
    sleeps = []
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: replies.pop(0))
    assert gemini.generate_json("x", {}) == {}
    assert len([s for s in sleeps if s >= 20]) == 2


def test_model_busy_for_too_long_still_fails_with_status(monkeypatch):
    busy = Resp(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})
    calls = []
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: calls.append(1) or busy)
    with pytest.raises(gemini.GeminiError) as e:
        gemini.generate_json("x", {})
    assert e.value.http == 503 and len(calls) == len(gemini.BUSY_WAITS_S) + 1
