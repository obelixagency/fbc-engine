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


@pytest.mark.slow
def test_render_teaser(tmp_path):
    from fbc import qa
    from fbc.render import render_mp4
    proj, tl = build(tmp_path, "teaser")
    out = render_mp4(proj, tmp_path / "teaser.mp4")
    assert qa.check_video(out, "short", json.loads((proj / "qa.json").read_text())) == []
    assert "YouTube" in (proj / "index.html").read_text()


def test_background_is_blurred_until_the_answer_is_revealed(tmp_path):
    """The clip behind a question shows its answer (a clock behind 'what has hands and a face?'), so a
    pre-blurred copy (made with ffmpeg, no CSS filter) covers it until the reveal, then fades out."""
    from fbc import render
    proj, tl = build(tmp_path)
    html = (proj / "index.html").read_text()
    assert "filter" not in html.split("const ACTIONS =")[1]
    bgs = render._backgrounds(tl)
    actions = render.build_actions(tl, bgs)
    q1 = next(s for s in tl.segments if s.data.get("reveal_at"))
    run = next(i for i, b in enumerate(bgs) if q1 in b["segs"])
    assert (proj / "assets" / bgs[run]["blur_name"]).exists()
    assert f'id="bgb{run}"' in html
    fade = [a for a in actions if a["sel"] == f"#bgb{run}"]
    assert any(a["m"] == "to" and a["to"]["opacity"] == 0 and a["t"] == round(q1.data["reveal_at"], 3) for a in fade)
