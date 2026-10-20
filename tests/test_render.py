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


def test_short_cover_html_uses_the_hook_and_format_label(tmp_path):
    from fbc import render
    html = render.short_cover_html("Only 3% of adults <get> this!", "three_q", "bg.jpg")
    assert "ONLY 3% OF ADULTS &lt;GET&gt; THIS!" in html and "3 QUESTIONS" in html
    assert 'data-width="1080" data-height="1920"' in html


def test_prepend_cover_makes_the_designed_cover_the_first_second(tmp_path):
    import subprocess as sp
    from fbc import render
    v, png = tmp_path / "v.mp4", tmp_path / "c.png"
    sp.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=f=440:d=3:sample_rate=48000", "-shortest", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(v)], check=True)
    sp.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0xFFD23F:s=1080x1920", "-frames:v", "1",
            str(png)], check=True)
    out = render.prepend_cover(v, png, tmp_path / "out.mp4", seconds=1.0)
    dur = float(sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout)
    assert 3.9 < dur < 4.2
    first = tmp_path / "f.png"
    sp.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.3", "-i", str(out), "-frames:v", "1", "-vf", "scale=1:1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", str(first)], check=True)
    r, g, b = first.read_bytes()[:3]
    assert r > 200 and g > 170 and b < 120          # the yellow cover, not the clip


def test_scene_and_background_visibility_is_driven_by_the_timeline(tmp_path):
    """On GitHub's browser the hook scene sometimes stayed on screen over question 1, so every scene and
    background run is shown/hidden explicitly by the GSAP timeline, not only by clip timing."""
    from fbc import render
    proj, tl = build(tmp_path)
    bgs = render._backgrounds(tl)
    actions = render.build_actions(tl, bgs)
    vis = [a for a in actions if a["m"] == "set" and "autoAlpha" in a["to"]]
    hook_end = round(tl.segments[0].end, 3)
    assert {"m": "set", "sel": "#sc0", "t": hook_end, "to": {"autoAlpha": 0}} in vis
    s1 = tl.segments[1]
    assert {"m": "set", "sel": "#sc1", "t": 0, "to": {"autoAlpha": 0}} in vis
    assert {"m": "set", "sel": "#sc1", "t": round(s1.start, 3), "to": {"autoAlpha": 1}} in vis
    assert any(a["sel"] == "#bgw1" and a["to"] == {"autoAlpha": 0} and a["t"] == 0 for a in vis)
