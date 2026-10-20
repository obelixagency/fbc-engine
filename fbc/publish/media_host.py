"""Public hosting for Instagram (it fetches the video by URL): the fbc-media GitHub Pages repo.
A file is pushed only in the tick that publishes it and removed after 14 days."""
import shutil
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from ..config import MEDIA_BASE_URL


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _commit_push(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    if not st.strip():
        return
    _git(repo, "-c", "user.name=fbc-bot", "-c", "user.email=fbc-bot@users.noreply.github.com", "commit", "-q", "-m", msg)
    _git(repo, "pull", "-q", "--rebase", "origin", "main")
    _git(repo, "push", "-q", "origin", "HEAD:main")


def push_public(repo: Path, path: Path, today: date, wait_s: int = 300) -> str:
    name = f"{today:%Y%m%d}_{path.name}"
    shutil.copy2(path, repo / name)
    _commit_push(repo, f"media: {name}")
    url = f"{MEDIA_BASE_URL}/{name}"
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            if requests.head(url, timeout=15).status_code == 200:
                return url
        except requests.RequestException:
            pass
        time.sleep(10)
    raise RuntimeError("GitHub Pages did not serve the media file in time")


def prune_public(repo: Path, today: date, days: int = 14) -> int:
    cutoff = (today - timedelta(days=days)).strftime("%Y%m%d")
    removed = 0
    for f in [*repo.glob("*.mp4"), *repo.glob("*.png")]:
        if f.name[:8].isdigit() and f.name[:8] < cutoff:
            f.unlink()
            removed += 1
    if removed:
        _commit_push(repo, f"media: prune {removed} old files")
    return removed
