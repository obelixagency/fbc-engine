"""Read-only connection check (`fbc check`): proves every secret works without posting anything.
Public logs get only the service name, ok/fail, the HTTP status and a short error code."""
from __future__ import annotations

import re
import subprocess
from datetime import date, timedelta

import requests

from .config import CONTENT_REPO, MEDIA_REPO, env
from .log import safe

YT = "https://www.googleapis.com/youtube/v3"
YTA = "https://youtubeanalytics.googleapis.com/v2/reports"
GRAPH = "https://graph.facebook.com/v26.0"
PEXELS = "https://api.pexels.com/videos/search"


class CheckFailed(RuntimeError):
    def __init__(self, http: int | None = None, reason: str | None = None):
        super().__init__(reason or "check failed")
        self.http = http
        self.reason = reason if isinstance(reason, str) and re.fullmatch(r"[A-Za-z0-9_.]{2,40}", reason) else None


def _json(r) -> dict:
    try:
        return r.json()
    except ValueError:
        return {}


def _need_ok(r, reason_of=lambda d: None) -> dict:
    data = _json(r)
    if r.status_code >= 400:
        raise CheckFailed(r.status_code, reason_of(data))
    return data


def _google_reason(d: dict):
    e = d.get("error")
    if isinstance(e, dict):
        errs = e.get("errors") or [{}]
        return errs[0].get("reason") or e.get("status")
    return e if isinstance(e, str) else None


def _meta_reason(d: dict):
    e = d.get("error")
    return f"code_{e.get('code')}" if isinstance(e, dict) and e.get("code") is not None else None


def _yt_token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
        "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=30)
    data = _need_ok(r, _google_reason)
    if "access_token" not in data:
        raise CheckFailed(r.status_code, _google_reason(data))
    return data["access_token"]


def check_youtube() -> None:
    token = _yt_token()
    h = {"Authorization": f"Bearer {token}"}
    data = _need_ok(requests.get(f"{YT}/channels", params={"part": "id", "mine": "true"}, headers=h, timeout=30),
                    _google_reason)
    if not data.get("items"):
        raise CheckFailed(200, "no_channel_on_this_account")
    end = date.today()
    _need_ok(requests.get(YTA, params={"ids": "channel==MINE", "startDate": str(end - timedelta(days=7)),
                                       "endDate": str(end), "metrics": "views"}, headers=h, timeout=30), _google_reason)


def check_facebook() -> None:
    page_id, token = env("FB_PAGE_ID"), env("FB_PAGE_TOKEN")
    data = _need_ok(requests.get(f"{GRAPH}/me", params={"fields": "id", "access_token": token}, timeout=30),
                    _meta_reason)
    if str(data.get("id")) != str(page_id):
        raise CheckFailed(200, "token_is_not_the_page_token")


def check_instagram() -> None:
    data = _need_ok(requests.get(f"{GRAPH}/{env('IG_USER_ID')}", params={
        "fields": "id", "access_token": env("FB_PAGE_TOKEN")}, timeout=30), _meta_reason)
    if not data.get("id"):
        raise CheckFailed(200, "no_instagram_account")


def check_pexels() -> None:
    _need_ok(requests.get(PEXELS, headers={"Authorization": env("PEXELS_API_KEY")},
                          params={"query": "quiz", "per_page": 1}, timeout=30))


def check_github(run=subprocess.run) -> None:
    for repo in (CONTENT_REPO, MEDIA_REPO):
        p = run(["gh", "api", f"repos/{repo}", "--jq", ".permissions.push"], capture_output=True, text=True)
        if p.returncode != 0:
            raise CheckFailed(None, "repo_not_reachable")
        if p.stdout.strip() != "true":
            raise CheckFailed(None, "no_write_access")


CHECKS = {"youtube": check_youtube, "facebook": check_facebook, "instagram": check_instagram,
          "pexels": check_pexels, "github": check_github}


def run_checks(checks: dict = CHECKS) -> int:
    failed = 0
    for name, fn in checks.items():
        try:
            fn()
            safe("check", platform=name, status="ok")
        except Exception as e:  # noqa: BLE001 — public logs: name, status code and short reason only
            failed += 1
            safe("check", platform=name, status="fail", error_type=type(e).__name__,
                 http=getattr(e, "http", None), reason=getattr(e, "reason", None))
    return failed
