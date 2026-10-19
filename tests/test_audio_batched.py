"""TTS free tier is ~10 requests/day per model, so lines are voiced in a few batched calls
and split back into per-line WAVs at the pauses."""
import io
import wave

import pytest

from fbc import audio
from fbc.gemini import QuotaExceeded
from fbc.models import batch_from_obj
from tests.conftest import make_batch

RATE = 24000


def tone_wav(segments: int, seg_s=0.8, gap_s=1.4, continuous=False):
    import math
    frames = bytearray()
    def add_tone(sec):
        for i in range(int(RATE * sec)):
            v = int(8000 * math.sin(2 * math.pi * 300 * i / RATE))
            frames.extend(v.to_bytes(2, "little", signed=True))
    for k in range(segments):
        add_tone(seg_s if not continuous else seg_s + gap_s)
        if k < segments - 1 and not continuous:
            frames.extend(b"\x00\x00" * int(RATE * gap_s))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(bytes(frames))
    return buf.getvalue()


def n_lines(text):
    return len([p for p in text.split(audio.SEP) if p.strip()])


@pytest.fixture
def batch():
    return batch_from_obj(make_batch())


def test_short_is_voiced_in_one_call_and_split(tmp_path, batch):
    req = audio.build_audio_request(batch.shorts[0])
    calls = []

    def tts(text, voice, style, model=None):
        calls.append((voice, n_lines(text)))
        return tone_wav(n_lines(text))
    d = audio.synthesize(req, tmp_path, tts=tts, spacing_s=0)
    assert len(calls) == 1 and calls[0] == ("Puck", 8)
    assert set(d) == {l["id"] for l in req["lines"]}
    assert all(0.5 < v < 1.3 for v in d.values())


def test_long_groups_by_voice_in_chunks(tmp_path, batch):
    req = audio.build_audio_request(batch.long)
    calls = []

    def tts(text, voice, style, model=None):
        calls.append((voice, n_lines(text)))
        return tone_wav(n_lines(text))
    d = audio.synthesize(req, tmp_path, tts=tts, spacing_s=0)
    assert len(d) == len(req["lines"])
    assert {v for v, _ in calls} == {"Puck", "Zephyr"}
    assert all(n <= audio.CHUNK for _, n in calls)
    assert len(calls) <= 8


def test_unsplittable_audio_falls_back_to_per_line(tmp_path, batch):
    req = audio.build_audio_request(batch.shorts[0])
    calls = []

    def tts(text, voice, style, model=None):
        calls.append(n_lines(text))
        return tone_wav(n_lines(text), continuous=True)
    d = audio.synthesize(req, tmp_path, tts=tts, spacing_s=0)
    assert calls[0] == 8 and calls[1:] == [1] * 8
    assert len(d) == 8


def test_fallback_model_on_quota(tmp_path, batch):
    req = audio.build_audio_request(batch.shorts[0])
    seen = []

    def tts(text, voice, style, model=None):
        seen.append(model)
        if model == audio.TTS_MODELS[0]:
            raise QuotaExceeded("429")
        return tone_wav(n_lines(text))
    audio.synthesize(req, tmp_path, tts=tts, spacing_s=0)
    assert seen == list(audio.TTS_MODELS)


def test_quota_on_all_models_keeps_progress(tmp_path, batch):
    req = audio.build_audio_request(batch.long)
    state = {"n": 0}

    def tts(text, voice, style, model=None):
        state["n"] += 1
        if state["n"] > 2:
            raise QuotaExceeded("429")
        return tone_wav(n_lines(text))
    with pytest.raises(QuotaExceeded):
        audio.synthesize(req, tmp_path, tts=tts, spacing_s=0)
    done = len(list(tmp_path.glob("*.wav")))
    assert done > 0
    calls = []
    audio.synthesize(req, tmp_path, tts=lambda t, v, s, model=None: calls.append(1) or tone_wav(n_lines(t)), spacing_s=0)
    assert len(list(tmp_path.glob("*.wav"))) == len(req["lines"])


def test_long_pause_inside_a_line_is_shortened(tmp_path):
    import subprocess
    from fbc import audio, qa
    p = tmp_path / "line.wav"
    # 1 s tone, 1.6 s silence, 1 s tone: QA would flag the inner pause
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=1",
                    "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-f", "lavfi", "-i", "sine=f=440:d=1",
                    "-filter_complex", "[0]aresample=24000[a];[1]atrim=0:1.6[b];[2]aresample=24000[c];[a][b][c]concat=n=3:v=0:a=1",
                    "-ac", "1", str(p)], check=True)
    assert qa._silences(p)
    audio.tighten(p)
    assert not qa._silences(p)
    assert 2.0 < audio.wav_duration(p) < 3.0
    before = audio.wav_duration(p)
    audio.tighten(p)                      # idempotent: cached lines are tightened again on every tick
    assert abs(audio.wav_duration(p) - before) < 0.05
