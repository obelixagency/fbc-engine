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
    from .publish.meta import MetaError, page_token
    page_id = env("FB_PAGE_ID")
    try:
        token = page_token()   # a system-user token is turned into the Page token here
    except MetaError:
        raise CheckFailed(400, "token_invalid_or_no_page_access")
    data = _need_ok(requests.get(f"{GRAPH}/me", params={"fields": "id", "access_token": token}, timeout=30),
                    _meta_reason)
    if str(data.get("id")) != str(page_id):
        raise CheckFailed(200, "token_is_not_the_page_token")


def _ig_token() -> str:
    from .publish.meta import MetaError, page_token
    try:
        return page_token()
    except MetaError:
        raise CheckFailed(400, "token_invalid_or_no_page_access")


def check_instagram() -> None:
    data = _need_ok(requests.get(f"{GRAPH}/{env('IG_USER_ID')}", params={
        "fields": "id", "access_token": _ig_token()}, timeout=30), _meta_reason)
    if not data.get("id"):
        raise CheckFailed(200, "no_instagram_account")


def check_ig_insights() -> None:
    """Reach/views of the latest Reel: needs the instagram_manage_insights permission on the token."""
    token = _ig_token()
    media = _need_ok(requests.get(f"{GRAPH}/{env('IG_USER_ID')}/media", params={
        "fields": "id", "limit": 1, "access_token": token}, timeout=30), _meta_reason).get("data") or []
    if not media:
        raise CheckFailed(200, "no_media_yet")
    _need_ok(requests.get(f"{GRAPH}/{media[0]['id']}/insights", params={
        "metric": "views,reach", "access_token": token}, timeout=30), _meta_reason)


def check_fb_insights() -> None:
    """Page reach: needs the read_insights permission on the token. page_impressions_unique was removed by Meta
    in June 2026 (invalid metric, code 100); page_total_media_view_unique is its replacement."""
    _need_ok(requests.get(f"{GRAPH}/{env('FB_PAGE_ID')}/insights", params={
        "metric": "page_total_media_view_unique", "period": "day", "access_token": _ig_token()}, timeout=30),
        _meta_reason)


def check_fb_reel_insights() -> None:
    """Plays of the latest Reel on the Page (what analytics collects for Facebook)."""
    token = _ig_token()
    vids = _need_ok(requests.get(f"{GRAPH}/{env('FB_PAGE_ID')}/video_reels", params={
        "fields": "id", "limit": 1, "access_token": token}, timeout=30), _meta_reason).get("data") or []
    if not vids:
        raise CheckFailed(200, "no_reels_yet")
    r = requests.get(f"{GRAPH}/{vids[0]['id']}/video_insights", params={
        "metric": "blue_reels_play_count,post_video_avg_time_watched", "access_token": token}, timeout=30)
    d = r.json()
    if r.status_code >= 400 or "error" in d:   # Meta's message names the bad metric or missing permission
        e = d.get("error") or {}
        raise CheckFailed(r.status_code, f"code_{e.get('code')}: {str(e.get('message', ''))[:120]}")
    if not d.get("data"):
        raise CheckFailed(r.status_code, "no_metrics_returned")
    for vid in (vids[0]["id"], "1403290442876570"):   # temporary: latest Reel vs. the id the publisher stored
        rr = requests.get(f"{GRAPH}/{vid}/video_insights", params={
            "metric": "blue_reels_play_count,post_video_avg_time_watched", "access_token": token}, timeout=30)
        dd = rr.json()
        vals = {m["name"]: (m.get("values") or [{}])[0].get("value") for m in dd.get("data") or []}
        safe("fb_reel_probe", id=vid, http=rr.status_code, reason=_meta_reason(dd),
             count=vals.get("blue_reels_play_count"), status=",".join(f"{k}={type(v).__name__}" for k, v in vals.items()))


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
          "instagram_insights": check_ig_insights, "facebook_insights": check_fb_insights,
          "facebook_reel_insights": check_fb_reel_insights,
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
