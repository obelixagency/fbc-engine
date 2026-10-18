import pytest

from fbc import audio, timeline
from fbc.models import batch_from_obj
from tests.conftest import make_batch


@pytest.fixture
def batch():
    return batch_from_obj(make_batch())


def durs(item, each=1.2):
    return {l["id"]: each for l in audio.build_audio_request(item)["lines"]}


def clips(item):
    qs = item.all_questions() if hasattr(item, "all_questions") else item.questions
    return {q.id: {"name": f"{q.id}.mp4", "kind": "video", "portrait": True} for q in qs}


def short_of(batch, fmt):
    return next(s for s in batch.shorts if s.format == fmt)


def test_three_q_duration_in_range(batch):
    s = short_of(batch, "three_q")
    tl = timeline.build_short_timeline(s, durs(s), clips(s))
    assert (tl.width, tl.height, tl.fps) == (1080, 1920, 30)
    assert 22 <= tl.duration <= 35
    kinds = [seg.kind for seg in tl.segments]
    assert kinds[0] == "hook" and kinds[-1] == "outro" and kinds.count("mcq") == 3


def test_ticks_on_countdown_seconds(batch):
    s = short_of(batch, "three_q")
    tl = timeline.build_short_timeline(s, durs(s), clips(s))
    seg = next(x for x in tl.segments if x.kind == "mcq")
    cd_start, cd_len = seg.data["countdown_start"], seg.data["countdown"]
    ticks = [at for name, at in seg.sfx if name == "tick.wav"]
    assert ticks == pytest.approx([cd_start + k for k in range(int(cd_len))])
    dings = [at for name, at in seg.sfx if name == "ding.wav"]
    assert dings == pytest.approx([cd_start + cd_len])


def test_overlong_lines_shorten_countdowns(batch):
    s = short_of(batch, "three_q")
    tl = timeline.build_short_timeline(s, durs(s, 3.0), clips(s))
    assert tl.duration <= 35
    assert all(seg.data["countdown"] < 3 for seg in tl.segments if seg.kind == "mcq")


def test_way_too_long_raises(batch):
    s = short_of(batch, "three_q")
    with pytest.raises(timeline.TimelineError):
        timeline.build_short_timeline(s, durs(s, 6.0), clips(s))


def test_every_format_builds(batch):
    for s in batch.shorts:
        tl = timeline.build_short_timeline(s, durs(s), clips(s))
        lo = timeline.MIN_SHORT[s.format]
        assert lo <= tl.duration <= 35, (s.format, tl.duration)
        assert tl.loop_safe


def test_long_timeline(batch):
    item = batch.long
    tl = timeline.build_long_timeline(item, durs(item, 2.0), clips(item))
    assert (tl.width, tl.height) == (1920, 1080)
    assert 480 <= tl.duration <= 720
    assert tl.chapters[0] == "0:00 Intro"
    round_chapters = [c for c in tl.chapters if "Round" in c]
    assert len(round_chapters) == 5
    assert any(c.endswith("Bonus") for c in tl.chapters)
    starts = [timeline.parse_mmss(c.split()[0]) for c in tl.chapters]
    assert starts == sorted(starts) and starts[1] >= 10   # YouTube needs >= 10 s chapters
