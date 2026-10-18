import io
import json
import wave

import pytest

from fbc import audio
from fbc.gemini import QuotaExceeded
from fbc.models import batch_from_obj
from tests.conftest import make_batch


def wav_bytes(seconds=1.0, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


@pytest.fixture
def batch():
    return batch_from_obj(make_batch())


def test_three_q_request(batch):
    s = next(x for x in batch.shorts if x.format == "three_q")
    req = audio.build_audio_request(s)
    assert req["model"] == "gemini-3.8-flash-tts" and req["default_voice"] == "Puck"
    assert req["video_id"] == s.id
    ids = [l["id"] for l in req["lines"]]
    assert ids == ["hook", "q1", "q1_reveal", "q2", "q2_reveal", "q3", "q3_reveal", "outro"]
    reveal = req["lines"][2]
    assert reveal["style"] == audio.REVEAL_STYLE and reveal["text"].startswith("It's Horse!")
    assert all(l["language"] == "en-US" for l in req["lines"])


def test_wyr_has_no_reveal(batch):
    s = next(x for x in batch.shorts if x.format == "would_you_rather")
    ids = [l["id"] for l in audio.build_audio_request(s)["lines"]]
    assert ids == ["hook", "q1", "outro"]


def test_long_request_reveals_use_zephyr(batch):
    req = audio.build_audio_request(batch.long)
    reveals = [l for l in req["lines"] if l["id"].endswith("_reveal")]
    assert len(reveals) == 31 and all(l["voice"] == "Zephyr" for l in reveals)
    ids = [l["id"] for l in req["lines"]]
    assert ids[0] == "intro" and ids[-1] == "outro" and "r1_title" in ids and "r5_score" in ids


def test_synthesize_resumes_after_quota(tmp_path, batch):
    s = batch.shorts[0]
    req = audio.build_audio_request(s)
    calls = []

    def flaky(text, voice, style):
        calls.append(text)
        if len(calls) == 3:
            raise QuotaExceeded("429")
        return wav_bytes(1.0)
    with pytest.raises(QuotaExceeded):
        audio.synthesize(req, tmp_path, tts=flaky)
    assert len(list(tmp_path.glob("*.wav"))) == 2
    calls.clear()
    durations = audio.synthesize(req, tmp_path, tts=lambda t, v, st: (calls.append(t), wav_bytes(1.5))[1])
    assert len(calls) == len(req["lines"]) - 2
    assert set(durations) == {l["id"] for l in req["lines"]}
    assert durations["hook"] == pytest.approx(1.0) and durations["outro"] == pytest.approx(1.5)
    assert json.loads((tmp_path / "audio_request.json").read_text())["video_id"] == s.id


def test_make_sfx(tmp_path):
    audio.make_sfx(tmp_path)
    for name in ("tick.wav", "ding.wav", "whoosh.wav"):
        assert (tmp_path / name).stat().st_size > 1000
