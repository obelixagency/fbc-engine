"""Logging for public GitHub Actions logs: only ids, counts and statuses are printed,
never question text, captions, tokens or unpublished media URLs."""
import json
import sys
import os
import re

ALLOWED = {"id", "count", "platform", "status", "week", "kind", "attempts", "error_type", "http", "reason"}


REASONS = {"invalid_grant", "invalid_client", "unauthorized_client", "access_denied", "insufficientPermissions",
           "quotaExceeded", "dailyLimitExceeded", "forbidden", "UNAUTHENTICATED", "PERMISSION_DENIED",
           "NOT_FOUND", "INVALID_ARGUMENT", "RESOURCE_EXHAUSTED", "INTERNAL", "UNAVAILABLE",
           "PER_DAY", "PER_MINUTE", "no_channel_on_this_account", "token_invalid_or_no_page_access",
           "token_is_not_the_page_token", "no_instagram_account", "repo_not_reachable", "no_write_access"}


def safe_reason(value):
    # A character-pattern alone admits arbitrary token values. Only known codes are diagnostics.
    if isinstance(value, str) and (value in REASONS or re.fullmatch(r"code_[0-9]{1,6}", value)):
        return value
    return None


def error_summary(error: Exception) -> str:
    """Never serialize exception messages: requests/SDKs may include credentials or signed URLs."""
    result = type(error).__name__
    http = getattr(error, "http", None)
    if type(http) is int and 100 <= http <= 599:
        result += f" (HTTP {http})"
    reason = safe_reason(getattr(error, "reason", None))
    if reason:
        result += f" [{reason}]"
    return result


def safe(event: str, **fields) -> None:
    kept = {k: v for k, v in fields.items() if k in ALLOWED}
    if "reason" in kept:
        kept["reason"] = safe_reason(kept["reason"])
    text = f"[fbc] {event} {json.dumps(kept, sort_keys=True, default=str)}"
    for name, value in os.environ.items():
        if value and any(word in name.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
            text = text.replace(value, "[redacted]")
    print(text, file=sys.stdout, flush=True)
