"""Command line entry points used by the GitHub Actions workflows.

    python -m fbc.cli generate  [--week 2026-W45] [--dry-run]
    python -m fbc.cli tick      [--dry-run]            # produce what is due within 48 h, publish what is due, escalate
    python -m fbc.cli produce   [--horizon-hours 48] [--dry-run]
    python -m fbc.cli publish   [--dry-run]
    python -m fbc.cli analytics [--dry-run]
    python -m fbc.cli remind                           # Sunday nudge if this week's batch PR is still open
    python -m fbc.cli auth-youtube                     # one-time local helper that prints a refresh token

Logs are public (public repo), so this module only prints ids, counts and statuses via log.safe().
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from . import analytics as an
from .audio import build_audio_request, make_sfx, synthesize
from .config import CONTENT_REPO, LIMITS
from .dedupe import fingerprint
from .errors import ResumeLater
from .generate import build_batch
from .log import safe
from .models import Batch, BatchError, LongItem
from .schedule import due_items, late_items, next_week, to_produce
from .store import ContentRepo, plan_queue
from .weights import default_weights

WORK = Path("work")
MAX_PUBLISH_PER_RUN = 2


# ---------------------------------------------------------------- generate
def pr_body(batch: Batch) -> str:
    def row(q, item_id, fmt):
        flags = " ".join(q.flags) or ("✅" if q.verified else "")
        src = f"[source]({q.source_url})" if q.source_url else ""
        opts = " / ".join(q.options) if q.options else "—"
        return f"| {item_id} | {fmt} | {q.question} | {opts} | **{q.answer or '—'}** | {flags} | {src} |"
    head = "| item | format | question | options | answer | check | source |\n|---|---|---|---|---|---|---|"
    lines = [f"## Family Brain Clash — batch {batch.week}",
             "Review the questions below. **Edit `batch.json` in this PR to fix anything, then Merge to approve.** "
             "Nothing is published until this PR is merged.", "",
             "Rows marked ⚠️ could not be verified automatically — fix or delete them.", "", "### Shorts", head]
    for s in batch.shorts:
        for q in s.questions:
            lines.append(row(q, s.id, s.format))
    lines += ["", "**Titles** (change `chosen_title_index` to pick another):"]
    for s in batch.shorts:
        lines.append(f"- {s.id}: " + " · ".join(f"[{i}] {t}" for i, t in enumerate(s.titles)))
    for lv in batch.longs:
        lines += ["", f"### Long video — Family Quiz Night #{lv.episode} (`{lv.id}`)", head]
        for r in lv.rounds:
            for q in r["questions"]:
                lines.append(row(q, r["name"], "long"))
        lines.append(row(lv.bonus, "Bonus", "long"))
        lines += ["", "Titles: " + " · ".join(f"[{i}] {t}" for i, t in enumerate(lv.titles))]
    lines += ["", "### Last week's performance", batch.report_md or "_First week: no data yet._"]
    return "\n".join(lines)


def cmd_generate(repo: ContentRepo, week: str, dry_run: bool, today: date, gen=None) -> Batch:
    weights = repo.weights(default_weights())
    used = repo.used()
    episode = repo.episode_count() + 1
    report = (repo.path / "analytics" / "last_report.md").read_text() if (repo.path / "analytics" / "last_report.md").exists() else ""
    kw = {"gen": gen} if gen else {}
    try:
        batch = build_batch(week, weights, used, episode, random.Random(week), today=today, report_md=report, **kw)
    except Exception as e:   # private repo: the full reasons are safe there, never in public logs
        reasons = e.problems if isinstance(e, BatchError) else [
            f"{type(e).__name__} (http={getattr(e, 'http', None)}, reason={getattr(e, 'reason', None)}): {str(e)[:500]}"]
        repo.report_issue(f"{'[dry run] ' if dry_run else ''}Batch {week} generation failed",
                          "Generation stopped before opening a PR. Reasons (first ones):\n\n"
                          + "\n".join(f"- {p}" for p in reasons)
                          + "\n\nRe-run **Generate weekly batch** after fixing, or wait for the next Friday run.")
        raise
    body = pr_body(batch)
    if dry_run:
        out = WORK / "dry"; out.mkdir(parents=True, exist_ok=True)
        (out / "batch.json").write_text(batch.to_json()); (out / "pr.md").write_text(body)
    else:
        repo.open_batch_pr(week, batch.to_json(), body)
    safe("generated", week=week, count=len(batch.shorts))
    return batch


# ---------------------------------------------------------------- queue intake
def _item_meta(batch: Batch, item) -> dict:
    if isinstance(item, LongItem):
        return {"format": "long", "category": "mixed", "hook_id": "", "title": item.title,
                "description": item.description, "first_comment": item.first_comment}
    return {"format": item.format, "category": item.questions[0].category, "hook_id": item.hook_id,
            "title": item.title, "description": item.description, "first_comment": item.first_comment}


def _batch_issue(repo: ContentRepo, week: str, problems: list[str]) -> None:
    repo.open_issue(f"Batch {week} cannot be produced",
                    "The merged `batch.json` has problems. Fix them in `fbc-content` and the next run picks it up:\n\n"
                    + "\n".join(f"- {p}" for p in problems))
    safe("batch_invalid", week=week, count=len(problems))


def intake(repo: ContentRepo, today: date, now_: datetime | None = None) -> int:
    """Queue every merged batch that is not queued yet. A broken batch opens an Issue instead."""
    queue = repo.queue()
    queued = {e["week"] for e in queue}
    added = 0
    for week in repo.merged_weeks():
        if week in queued:
            continue
        try:
            batch = repo.load_batch(week)
            if batch.week != week:
                raise BatchError([f'"week" is {batch.week!r} but the folder is {week!r}'])
            entries = plan_queue(batch)
        except BatchError as e:
            _batch_issue(repo, week, e.problems)
            continue
        except Exception as e:  # noqa: BLE001 — any malformed edit becomes an Issue, never a stuck tick
            _batch_issue(repo, week, [f"{type(e).__name__}: {e}"])
            continue
        items = {s.id: s for s in batch.shorts} | {lv.id: lv for lv in batch.longs}
        now = datetime.now(timezone.utc) if now_ is None else now_
        for entry in entries:
            entry.update(_item_meta(batch, items[entry["id"]]))
            if datetime.fromisoformat(entry["slot_utc"]) < now:
                entry["status"] = "skipped"   # batch merged after this slot: never dump late videos at once
            queue.append(entry)
            added += 1
        repo.append_used([{"question": q.question, "answer": q.answer, "fingerprint": fingerprint(q.question, q.answer),
                           "date": today.isoformat(), "week": week} for q in batch.all_questions()])
    if added:
        repo.save_queue(queue)
    return added


# ---------------------------------------------------------------- produce
def _make_video(item, kind: str, work: Path, repo: ContentRepo, today: date, exclude: set) -> tuple:
    """Voice, visuals, render and QA for one item. Returns (mp4, timeline, clips, used clip ids)."""
    from . import pexels, qa, render, timeline
    voice_dir = work / "voice"
    durations = synthesize(build_audio_request(item), voice_dir)
    make_sfx(work / "sfx")
    portrait = kind == "short"
    w, h = (1080, 1920) if portrait else (1920, 1080)
    clips, assets, used_ids = {}, {}, []
    questions = item.all_questions() if isinstance(item, LongItem) else item.questions
    for q in questions:
        clip = pexels.find_clip(q.visual_query, "portrait" if portrait else "landscape", exclude | set(used_ids),
                                subject_required=getattr(item, "format", "") == "zoom_reveal")
        used_ids.append(clip.pexels_id)
        raw = pexels.download(clip, work / "raw" / f"{q.id}{'.mp4' if clip.kind == 'video' else '.jpg'}")
        name = f"{q.id}{'.mp4' if clip.kind == 'video' else '.jpg'}"
        render.prepare_clip(raw, work / "clips" / name, w, h, 20.0, clip.kind)
        clips[q.id] = {"name": name, "kind": clip.kind, "portrait": portrait}
        assets[name] = work / "clips" / name
    tl = (timeline.build_long_timeline(item, durations, clips) if isinstance(item, LongItem)
          else timeline.build_short_timeline(item, durations, clips))
    for seg in tl.segments:
        for name, _ in seg.audio:
            assets[name] = voice_dir / name
    for name in ("tick.wav", "ding.wav", "whoosh.wav"):
        assets[name] = work / "sfx" / name
    project = render.write_project(tl, durations, assets, work / "project", kind)
    out = work / f"{item.id}.mp4"
    render.render_mp4(project, out)
    problems = qa.check_video(out, kind, json.loads((project / "qa.json").read_text()))
    if problems:
        raise RuntimeError("QA failed: " + "; ".join(problems))
    shutil.rmtree(work / "raw", ignore_errors=True)
    return out, tl, clips, used_ids


def produce_entry(entry: dict, batch: Batch, repo: ContentRepo, today: date) -> dict:
    """Voice, visuals, render, QA, upload as a private release asset. Raises on failure.
    The long episode also gets a vertical teaser Reel for Facebook and Instagram."""
    from . import render
    from .teaser import teaser_item
    item = next(x for x in [*batch.longs, *batch.shorts] if x.id == entry["id"])
    work = WORK / entry["id"]
    exclude = repo.recent_clip_ids(LIMITS["clip_reuse_days"], today)
    out, tl, clips, used_ids = _make_video(item, entry["kind"], work, repo, today, exclude)
    questions = item.all_questions() if isinstance(item, LongItem) else item.questions
    entry["duration"] = tl.duration
    entry["chapters"] = tl.chapters
    if isinstance(item, LongItem):
        frames = []
        for k, q in enumerate(questions[::10][:3]):
            src = work / "clips" / clips[q.id]["name"]
            img = work / f"thumbsrc{k}.jpg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(src), "-frames:v", "1", str(img)]
                           if src.suffix == ".mp4" else ["cp", str(src), str(img)], check=True)
            frames.append(img)
        heads = [t.split(":")[-1].strip() for t in item.titles][:3]
        thumbs = render.render_thumbnails(heads, frames, item.episode, work / "thumbs")
        entry["thumbnails"] = [repo.upload_asset(entry["week"], p) for p in thumbs]
        if not entry.get("teaser_asset"):
            t_out, _, _, t_ids = _make_video(teaser_item(item), "short", work / "teaser", repo, today,
                                             exclude | set(used_ids))
            entry["teaser_asset"] = repo.upload_asset(entry["week"], t_out)
            used_ids += t_ids
    else:
        try:   # designed cover from the hook: the auto-picked frame can show the answer
            first = clips[questions[0].id]
            src = work / "clips" / first["name"]
            bgimg = work / "cover_bg.jpg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(src), "-frames:v", "1", str(bgimg)]
                           if src.suffix == ".mp4" else ["cp", str(src), str(bgimg)], check=True)
            cover = render.render_short_cover(item.hook, item.format, bgimg, work / "cover")
            named = cover.with_name(f"{entry['id']}_cover.png")
            shutil.copy2(cover, named)
            # the cover is also the Short's first second, so it can be picked as the thumbnail from the video
            final = render.prepend_cover(out, named, work / f"{entry['id']}_final.mp4")
            out = final.replace(out)
            entry["duration"] = tl.duration + 1.0
            entry["thumbnails"] = [repo.upload_asset(entry["week"], named)]
        except Exception as e:  # noqa: BLE001 — a missing cover never blocks the Short
            safe("cover_failed", id=entry["id"], error_type=type(e).__name__)
    entry["asset"] = repo.upload_asset(entry["week"], out)
    repo.add_clip_ids(used_ids, today)
    entry["status"] = "produced"
    return entry


MAX_PRODUCE_ATTEMPTS = 3
PRODUCE_BUDGET_S = 40 * 60   # stop starting new renders after 40 min so the next tick can publish on time


def produce(repo: ContentRepo, now: datetime, horizon_h: float, dry_run: bool, producer=produce_entry) -> int:
    import time as _time
    started = _time.monotonic()
    today = now.date()
    intake(repo, today, now)
    queue = repo.queue()
    batches: dict[str, Batch] = {}
    done = 0
    for entry in to_produce(queue, now, horizon_h):
        if dry_run:
            safe("would_produce", id=entry["id"], kind=entry["kind"])
            done += 1
            continue
        if _time.monotonic() - started > PRODUCE_BUDGET_S:
            safe("produce_budget_stop", count=done)
            break
        try:
            if entry["week"] not in batches:
                batches[entry["week"]] = repo.load_batch(entry["week"])
            producer(entry, batches[entry["week"]], repo, today)
            entry.pop("error", None)          # a retry that succeeds leaves no stale error behind
            done += 1
            safe("produced", id=entry["id"], kind=entry["kind"])
        except ResumeLater:
            safe("quota_stop", id=entry["id"])
            break
        except Exception as e:  # noqa: BLE001 — recorded privately, logged by type only
            entry["produce_attempts"] = entry.get("produce_attempts", 0) + 1
            entry["error"] = f"{type(e).__name__}: {str(e)[:500]}"
            if entry["produce_attempts"] >= MAX_PRODUCE_ATTEMPTS:
                entry["status"] = "failed"
                repo.open_issue(f"Production failed: {entry['id']}", f"`{entry['error']}`\n\nSet the entry's status back to "
                                "`planned` (and `produce_attempts` to 0) in `schedule/queue.json` to retry.")
            safe("produce_failed", id=entry["id"], attempts=entry["produce_attempts"], error_type=type(e).__name__)
        repo.save_queue(queue)
        repo.commit_and_push(f"produce: {entry['id']}")
    repo.save_queue(queue)
    return done


# ---------------------------------------------------------------- publish + escalate
def _warn_if_youtube_private(repo: ContentRepo, entry: dict) -> None:
    """YouTube silently locks API uploads to Private for unaudited projects: say so once, loudly."""
    note = entry.get("platforms", {}).get("youtube", {}).get("note") or ""
    if note.startswith("private_pending_audit"):
        repo.open_issue("YouTube uploads are landing as Private",
                        "YouTube accepted the upload but set it to **Private** (the API project has not passed the "
                        "YouTube API audit yet). Facebook and Instagram are not affected.\n\n"
                        "- Make the videos Public by hand in YouTube Studio, and\n"
                        "- apply for the audit: https://support.google.com/youtube/contact/yt_api_form\n\n"
                        f"First affected item: `{entry['id']}`")


def publish(repo: ContentRepo, now: datetime, media_repo: Path | None, dry_run: bool) -> int:
    from .publish import media_host
    queue = repo.queue()
    n = 0
    due = due_items(queue, now)
    fresh = [e for e in due if all(p["status"] == "pending" for p in e["platforms"].values())][:MAX_PUBLISH_PER_RUN]
    retries = [e for e in due if e not in fresh and not all(p["status"] == "pending" for p in e["platforms"].values())]
    for entry in fresh + retries:
        if dry_run:
            safe("would_publish", id=entry["id"])
            n += 1
            continue
        dl = WORK / "dl" / entry["id"]
        video = repo.download_asset(entry["week"], entry["asset"], dl)
        thumbs = [repo.download_asset(entry["week"], t, dl) for t in entry.get("thumbnails", [])]
        meta = {"title": entry["title"], "description": entry["description"], "first_comment": entry["first_comment"],
                "is_short": entry["kind"] == "short", "chapters": entry.get("chapters", []), "thumbnails": thumbs}
        if entry.get("teaser_asset"):
            meta["teaser"] = repo.download_asset(entry["week"], entry["teaser_asset"], dl)
        if entry["kind"] == "short" and thumbs:
            meta["cover_image"] = True       # designed cover rendered at production time
        if entry["kind"] == "short" and not thumbs:
            from .render import COVER_T, cover_frame
            try:   # YouTube/Instagram would otherwise auto-pick a frame, which can show the answer
                meta["thumbnails"] = [cover_frame(video, dl / f"{entry['id']}_cover.png")]
                meta["cover_ms"] = int(COVER_T * 1000)
            except Exception as e:  # noqa: BLE001 — a missing cover never blocks publishing
                safe("cover_failed", id=entry["id"], error_type=type(e).__name__)
        from . import publish as _pub
        _pub.publish_item(entry, meta, video, media_repo, now.date())
        _warn_if_youtube_private(repo, entry)
        if any(p["status"] == "published" for p in entry["platforms"].values()):
            entry.setdefault("published_at", now.isoformat())
        n += 1
        repo.save_queue(queue)
        repo.commit_and_push(f"publish: {entry['id']}")
    if media_repo and not dry_run:
        media_host.prune_public(media_repo, now.date())
    return n


def escalate(repo: ContentRepo, now: datetime, dry_run: bool) -> int:
    late = late_items(repo.queue(), now)
    for e in late:
        if dry_run:
            safe("late", id=e["id"])
            continue
        errors = "\n".join(f"- {k}: {v.get('status')} {v.get('error') or ''}" for k, v in e["platforms"].items())
        repo.open_issue(f"Late: {e['id']}", f"Slot {e['slot_utc']} passed more than 2.5 h ago.\n\nStatus: `{e['status']}`\n"
                        f"{e.get('error', '')}\n\n{errors}")
    return len(late)


# ---------------------------------------------------------------- analytics
def run_analytics(repo: ContentRepo, now: datetime, dry_run: bool) -> int:
    queue = repo.queue()
    rows = an.collect_stats(queue, now)
    if not rows:
        return 0
    before = repo.weights(default_weights())
    after = an.apply(before, rows)
    if dry_run:
        return len(rows)
    repo.save_weights(after)
    repo.append_scores([{**r, "date": now.date().isoformat()} for r in rows])
    (repo.path / "analytics").mkdir(exist_ok=True)
    (repo.path / "analytics" / "last_report.md").write_text(an.report_md(rows, before, after))
    repo.save_queue(queue)
    repo.commit_and_push("analytics: weights update")
    return len(rows)


def remind(repo: ContentRepo, week: str) -> None:
    out = repo.run_gh("pr", "list", "-R", repo.gh_repo, "--head", f"batch/{week}", "--state", "open", "--json", "number")
    for pr in json.loads(out or "[]"):
        repo.run_gh("pr", "comment", str(pr["number"]), "-R", repo.gh_repo, "--body",
                    "⏰ Reminder: this week's batch is still waiting for review. Nothing new will publish until it is merged.")


# ---------------------------------------------------------------- auth helper (local, one time)
def auth_youtube() -> None:
    """One-time local helper. Asks for the OAuth client id/secret (hidden input), runs Google sign-in in the
    browser and puts the refresh token on the clipboard (macOS pbcopy) instead of printing it."""
    import getpass
    import http.server
    import os
    import shutil as _sh
    import subprocess as _sp
    import urllib.parse
    import webbrowser
    import requests
    cid = os.environ.get("YT_CLIENT_ID") or input("Paste the YouTube OAuth Client ID, then Enter: ").strip()
    secret = os.environ.get("YT_CLIENT_SECRET") or getpass.getpass("Paste the Client secret (hidden), then Enter: ").strip()
    redirect = "http://localhost:8765"
    scopes = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl",
              "https://www.googleapis.com/auth/yt-analytics.readonly"]
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(scopes),
        "access_type": "offline", "prompt": "consent"})
    code = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Done. Go back to the terminal.")

        def log_message(self, *a):
            pass
    print("Opening Google sign-in in your browser. Sign in with the Family Brain Clash channel account.")
    webbrowser.open(url)
    http.server.HTTPServer(("localhost", 8765), H).handle_request()
    if "code" not in code:
        print("Sign-in was cancelled:", code.get("error"))
        return
    r = requests.post("https://oauth2.googleapis.com/token", data={"code": code["code"][0], "client_id": cid,
                      "client_secret": secret, "redirect_uri": redirect, "grant_type": "authorization_code"}).json()
    token = r.get("refresh_token")
    if not token:
        print("No refresh token returned:", r.get("error"), r.get("error_description"))
        return
    if _sh.which("pbcopy"):
        _sp.run(["pbcopy"], input=token.encode(), check=True)
        print("\nOK: the refresh token is now on your clipboard. Paste it into the YT_REFRESH_TOKEN secret on GitHub.")
    else:
        print("\nYT_REFRESH_TOKEN (paste it into GitHub Secrets, then clear this screen):\n" + token)


# ---------------------------------------------------------------- covers
def refresh_covers(repo: ContentRepo) -> int:
    """Set the hook-frame cover on Shorts already on YouTube (they were published with an auto-picked frame)."""
    from .publish import youtube
    from .render import cover_frame
    token, n, queue = youtube.access_token(), 0, repo.queue()
    for e in queue:
        yt = e.get("platforms", {}).get("youtube", {})
        if e.get("kind") != "short" or yt.get("status") != "published" or e.get("cover_set"):
            continue
        dl = WORK / "dl" / e["id"]
        video = repo.download_asset(e["week"], e["asset"], dl)
        youtube.set_thumbnail(yt["ref"], cover_frame(video, dl / "cover.png"), token=token)
        e["cover_set"] = True
        n += 1
        safe("cover_set", id=e["id"])
        repo.save_queue(queue)
    return n


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fbc")
    ap.add_argument("command", choices=["generate", "produce", "publish", "tick", "analytics", "remind", "auth-youtube", "check",
                                       "covers"])
    ap.add_argument("--week")
    ap.add_argument("--content", default="content", help="path of the fbc-content checkout")
    ap.add_argument("--media", default="media", help="path of the fbc-media checkout")
    ap.add_argument("--horizon-hours", type=float, default=48)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.command == "auth-youtube":
        auth_youtube()
        return 0
    if a.command == "check":
        from .check import run_checks
        return 1 if run_checks() else 0
    now = datetime.now(timezone.utc)
    repo = ContentRepo(Path(a.content), CONTENT_REPO)
    media = Path(a.media) if Path(a.media).exists() else None
    try:
        if a.command == "generate":
            cmd_generate(repo, a.week or next_week(now.date()), a.dry_run, now.date())
        elif a.command == "remind":
            remind(repo, a.week or next_week(now.date()))
        elif a.command == "produce":
            produce(repo, now, a.horizon_hours, a.dry_run)
        elif a.command == "publish":
            publish(repo, now, media, a.dry_run)
        elif a.command == "analytics":
            run_analytics(repo, now, a.dry_run)
        elif a.command == "covers":
            refresh_covers(repo)
        elif a.command == "tick":
            publish(repo, now, media, a.dry_run)      # publishing first: a long render never delays a due post
            escalate(repo, now, a.dry_run)
            produce(repo, now, a.horizon_hours, a.dry_run)
            publish(repo, datetime.now(timezone.utc), media, a.dry_run)
        if not a.dry_run and a.command not in ("remind", "generate"):
            repo.save_queue(repo.queue())
            repo.commit_and_push(f"{a.command}: {now:%Y-%m-%d %H:%M}")
    except Exception as e:  # noqa: BLE001 — public logs: type only
        safe("failed", status=a.command, error_type=type(e).__name__,
             http=getattr(e, "http", None), reason=getattr(e, "reason", None))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
