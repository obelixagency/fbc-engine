import json
import subprocess
from pathlib import Path

import pytest


from tests.render_fixture import build


def test_write_project_short(tmp_path):
    proj, tl = build(tmp_path)
    html = (proj / "index.html").read_text()
    assert 'data-width="1080" data-height="1920"' in html
    assert f'data-duration="{round(tl.duration, 3)}"' in html
    assert "&#34;" not in html.split("const ACTIONS =")[1][:200]
    assert (proj / "assets" / "gsap.min.js").exists() and (proj / "assets" / "fonts" / "ArchivoBlack-400.woff2").exists()
    meta = json.loads((proj / "qa.json").read_text())
    assert meta["kind"] == "short" and len(meta["countdowns"]) == 3


def test_every_audio_has_id_and_file(tmp_path):
    proj, _ = build(tmp_path)
    html = (proj / "index.html").read_text()
    import re
    srcs = re.findall(r'<audio id="au\d+" src="assets/([^"]+)"', html)
    assert srcs and all((proj / "assets" / s).exists() for s in srcs)


@pytest.mark.slow
def test_render_fixture_short(tmp_path):
    from fbc import qa
    from fbc.render import render_mp4
    proj, tl = build(tmp_path)
    out = render_mp4(proj, tmp_path / "short.mp4")
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
                                      capture_output=True, text=True).stdout)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920) and v["r_frame_rate"] == "30/1"
    assert qa.check_video(out, "short", json.loads((proj / "qa.json").read_text())) == []
