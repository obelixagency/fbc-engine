"""Logging for public GitHub Actions logs: only ids, counts and statuses are printed,
never question text, captions, tokens or unpublished media URLs."""
import json
import sys

ALLOWED = {"id", "count", "platform", "status", "week", "kind", "attempts", "error_type", "http", "reason"}


def safe(event: str, **fields) -> None:
    kept = {k: v for k, v in fields.items() if k in ALLOWED}
    print(f"[fbc] {event} {json.dumps(kept, sort_keys=True, default=str)}", file=sys.stdout, flush=True)
