import json
import subprocess
from pathlib import Path

from fbc import qa


def make(path: Path, w, h, seconds, silent_from=None):
    a = f"sine=frequency=440:duration={seconds}"
    af = []
    if silent_from is not None:
        af = ["-af", f"volume=enable='gte(t,{silent_from})':volume=0"]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=navy:s={w}x{h}:r=30:d={seconds}",
                    "-f", "lavfi", "-i", a, *af, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_good_short(tmp_path):
    p = make(tmp_path / "ok.mp4", 1080, 1920, 23)
    assert qa.check_video(p, "short", {"voice": [[0, 5]], "min": 22}) == []


def test_too_long_short(tmp_path):
    p = make(tmp_path / "long.mp4", 1080, 1920, 40)
    assert any("duration" in x for x in qa.check_video(p, "short", {"voice": []}))


def test_wrong_size(tmp_path):
    p = make(tmp_path / "wide.mp4", 1920, 1080, 23)
    assert any("resolution" in x for x in qa.check_video(p, "short", {"voice": []}))


def test_silence_inside_voice_window(tmp_path):
    p = make(tmp_path / "silent.mp4", 1080, 1920, 23, silent_from=4)
    assert any("silence" in x for x in qa.check_video(p, "short", {"voice": [[5, 9]]}))
    assert qa.check_video(p, "short", {"voice": [[0, 3]]}) == []
