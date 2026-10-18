from fbc import log


def test_safe_drops_text(capsys):
    log.safe("x", id="q1", question="secret", count=3)
    out = capsys.readouterr().out
    assert "q1" in out and "3" in out
    assert "secret" not in out
