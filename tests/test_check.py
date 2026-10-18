import json

from fbc import check


class R:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p


def env(monkeypatch):
    for k, v in {"YT_CLIENT_ID": "cid", "YT_CLIENT_SECRET": "sec-xyz", "YT_REFRESH_TOKEN": "rt-xyz",
                 "FB_PAGE_ID": "123", "FB_PAGE_TOKEN": "pt-xyz", "IG_USER_ID": "456", "PEXELS_API_KEY": "px"}.items():
        monkeypatch.setenv(k, v)


def test_youtube_invalid_grant_reports_reason_without_secrets(monkeypatch, capsys):
    env(monkeypatch)
    monkeypatch.setattr(check.requests, "post", lambda *a, **k: R(400, {"error": "invalid_grant"}))
    assert check.run_checks({"youtube": check.check_youtube}) == 1
    out = capsys.readouterr().out
    assert '"reason": "invalid_grant"' in out and '"http": 400' in out
    assert "rt-xyz" not in out and "sec-xyz" not in out


def test_youtube_ok_needs_a_channel_and_analytics(monkeypatch, capsys):
    env(monkeypatch)
    monkeypatch.setattr(check.requests, "post", lambda *a, **k: R(200, {"access_token": "at"}))
    gets = iter([R(200, {"items": [{"id": "UCx"}]}), R(200, {"rows": []})])
    monkeypatch.setattr(check.requests, "get", lambda *a, **k: next(gets))
    assert check.run_checks({"youtube": check.check_youtube}) == 0
    assert '"status": "ok"' in capsys.readouterr().out


def test_facebook_user_token_instead_of_page_token_fails(monkeypatch):
    env(monkeypatch)
    monkeypatch.setattr(check.requests, "get", lambda *a, **k: R(200, {"id": "999"}))
    assert check.run_checks({"facebook": check.check_facebook}) == 1


def test_meta_error_code_is_reported(monkeypatch, capsys):
    env(monkeypatch)
    monkeypatch.setattr(check.requests, "get", lambda *a, **k: R(400, {"error": {"code": 190, "message": "expired pt-xyz"}}))
    assert check.run_checks({"instagram": check.check_instagram}) == 1
    out = capsys.readouterr().out
    assert '"reason": "code_190"' in out and "pt-xyz" not in out


def test_github_needs_push_on_both_repos():
    class P:
        def __init__(self, out):
            self.returncode, self.stdout = 0, out
    outs = iter(["true\n", "false\n"])
    try:
        check.check_github(run=lambda *a, **k: P(next(outs)))
        assert False, "should fail"
    except check.CheckFailed as e:
        assert e.reason == "no_write_access"
