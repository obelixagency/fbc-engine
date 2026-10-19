"""The private content repo (fbc-content): batches, publish queue, used-question bank, clip history.
Rendered MP4s live as release assets of that private repo (one release per week), never in git."""
from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

from .models import Batch, parse_batch
from .schedule import week_slots

PLATFORMS = ("youtube", "instagram", "facebook")


def _gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed: {r.stderr[-300:]}")
    return r.stdout


def plan_queue(batch: Batch) -> list[dict]:
    slots = week_slots(batch.week)
    shorts = iter(batch.shorts)
    q = []
    for kind, at in slots:
        item = batch.long if kind == "long" else next(shorts, None)
        if item is None:      # an older batch with fewer Shorts than slots: leave the rest empty
            continue
        item_id = item.id
        q.append({"id": item_id, "kind": kind, "week": batch.week, "slot_utc": at.isoformat(), "status": "planned",
                  "asset": None, "platforms": {p: {"status": "pending", "ref": None, "error": None, "attempts": 0}
                                               for p in PLATFORMS}})
    return q


class ContentRepo:
    def __init__(self, path: Path, gh_repo: str, run_gh=_gh):
        self.path, self.gh_repo, self.run_gh = Path(path), gh_repo, run_gh

    # ---- files -------------------------------------------------------------------------
    def _read_json(self, rel: str, default):
        p = self.path / rel
        return json.loads(p.read_text()) if p.exists() else default

    def _write_json(self, rel: str, data) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def _read_jsonl(self, rel: str) -> list[dict]:
        p = self.path / rel
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def _append_jsonl(self, rel: str, rows: list[dict]) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def batch_rel(self, week: str) -> str:
        return f"batches/{week}/batch.json"

    def write_batch(self, week: str, raw: str) -> None:
        p = self.path / self.batch_rel(week)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw)

    def is_merged(self, week: str) -> bool:
        return (self.path / self.batch_rel(week)).exists()

    def load_batch(self, week: str) -> Batch:
        return parse_batch((self.path / self.batch_rel(week)).read_text())

    def merged_weeks(self) -> list[str]:
        d = self.path / "batches"
        return sorted(p.name for p in d.iterdir() if (p / "batch.json").exists()) if d.exists() else []

    def queue(self) -> list[dict]:
        return self._read_json("schedule/queue.json", [])

    def save_queue(self, q: list[dict]) -> None:
        self._write_json("schedule/queue.json", q)

    def used(self) -> list[dict]:
        return self._read_jsonl("bank/used.jsonl")

    def append_used(self, rows: list[dict]) -> None:
        self._append_jsonl("bank/used.jsonl", rows)

    def add_clip_ids(self, ids, on: date) -> None:
        self._append_jsonl("bank/clips.jsonl", [{"id": i, "date": on.isoformat()} for i in ids])

    def recent_clip_ids(self, days: int, today: date) -> set[int]:
        cutoff = today - timedelta(days=days)
        return {r["id"] for r in self._read_jsonl("bank/clips.jsonl") if date.fromisoformat(r["date"]) >= cutoff}

    def weights(self, default) -> dict:
        return self._read_json("analytics/weights.json", default)

    def save_weights(self, w: dict) -> None:
        self._write_json("analytics/weights.json", w)

    def append_scores(self, rows: list[dict]) -> None:
        self._append_jsonl("analytics/scores.jsonl", rows)

    def scores(self) -> list[dict]:
        return self._read_jsonl("analytics/scores.jsonl")

    # ---- git ----------------------------------------------------------------------------
    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, check=True).stdout

    def commit_and_push(self, message: str) -> None:
        self._git("add", "-A")
        if not self._git("status", "--porcelain").strip():
            return
        self._git("-c", "user.name=fbc-bot", "-c", "user.email=fbc-bot@users.noreply.github.com", "commit", "-q", "-m", message)
        for _ in range(3):
            try:
                # -X theirs: when replaying our commit, our version of a conflicting hunk wins, so a
                # published state is never lost (a lost state would re-upload the same video).
                self._git("pull", "-q", "--rebase", "-X", "theirs", "origin", "main")
                self._git("push", "-q", "origin", "HEAD:main")
                return
            except subprocess.CalledProcessError:
                subprocess.run(["git", "-C", str(self.path), "rebase", "--abort"], capture_output=True)
                continue
        raise RuntimeError("could not push to the content repo")

    # ---- GitHub (releases, issues, PRs) -------------------------------------------------
    def upload_asset(self, week: str, path: Path) -> str:
        tag = f"week-{week}"
        try:
            self.run_gh("release", "view", tag, "-R", self.gh_repo)
        except RuntimeError:
            self.run_gh("release", "create", tag, "-R", self.gh_repo, "--title", tag, "--notes", "Rendered videos (private)")
        self.run_gh("release", "upload", tag, str(path), "--clobber", "-R", self.gh_repo)
        return path.name

    def download_asset(self, week: str, name: str, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        self.run_gh("release", "download", f"week-{week}", "-p", name, "-D", str(dest), "--clobber", "-R", self.gh_repo)
        return dest / name

    def open_issue(self, title: str, body: str) -> None:
        existing = json.loads(self.run_gh("issue", "list", "-R", self.gh_repo, "--state", "open", "--search",
                                          f'"{title}" in:title', "--json", "title") or "[]")
        if any(i.get("title") == title for i in existing):
            return
        self.run_gh("issue", "create", "-R", self.gh_repo, "--title", title, "--body", body)

    def report_issue(self, title: str, body: str) -> None:
        """Like open_issue, but a repeat failure is added as a comment so new reasons are never lost."""
        existing = json.loads(self.run_gh("issue", "list", "-R", self.gh_repo, "--state", "open", "--search",
                                          f'"{title}" in:title', "--json", "title,number") or "[]")
        same = [i for i in existing if i.get("title") == title]
        if same:
            self.run_gh("issue", "comment", str(same[0]["number"]), "-R", self.gh_repo, "--body", body)
        else:
            self.run_gh("issue", "create", "-R", self.gh_repo, "--title", title, "--body", body)

    def open_batch_pr(self, week: str, raw: str, body_md: str) -> None:
        branch = f"batch/{week}"
        self._git("checkout", "-q", "-B", branch)
        self.write_batch(week, raw)
        self._git("add", "-A")
        self._git("-c", "user.name=fbc-bot", "-c", "user.email=fbc-bot@users.noreply.github.com", "commit", "-q", "-m",
                  f"batch: {week} (review and merge to approve)")
        self._git("push", "-q", "-f", "origin", branch)
        self._git("checkout", "-q", "main")
        body_file = self.path.parent / f"pr-{week}.md"
        body_file.write_text(body_md)
        self.run_gh("pr", "create", "-R", self.gh_repo, "--base", "main", "--head", branch,
                    "--title", f"Family Brain Clash batch {week} — review & merge to approve", "--body-file", str(body_file))
