from fbc.publish import CTA, captions


def m(fmt, short=True):
    return {"title": "T", "description": "D", "is_short": short, "format": fmt}


def test_social_and_youtube_captions_carry_the_format_cta():
    for fmt, line in CTA.items():
        c = captions(m(fmt))
        assert line in c["social"] and line in c["youtube"]
        assert c["social"].index(line) < c["social"].index("D")      # the ask comes before the description
        assert c["youtube"].startswith(line)                          # first line shows above "more" on Shorts


def test_unknown_format_and_long_episode_still_get_a_cta():
    assert "👇" in captions(m(None))["social"]
    assert "final score" in captions(m(None, short=False))["social"]
