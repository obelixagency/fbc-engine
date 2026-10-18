"""Project-wide constants and environment access. Values come from the spec
(docs/superpowers/specs/2026-10-17-family-brain-clash-design.md)."""
import os

BRAND = {
    "navy": "#16133F", "yellow": "#FFD23F", "pink": "#FF3E7F",
    "green": "#2EE59D", "white": "#FFFFFF",
}
OVERLAY = "rgba(22,19,63,0.45)"

SHORT_SLOTS_ET = ["07:00", "16:00"]
LONG_SLOT_ET = ("SAT", "10:00")
TZ_ET = "America/New_York"

LIMITS = {
    "question_words": 12, "option_words": 3, "fact_words": 15, "title_chars": 59,
    "short_min_s": 22.0, "short_max_s": 35.0, "long_min_s": 480.0, "long_max_s": 720.0,
    "dedupe_days": 180, "clip_reuse_days": 30, "late_grace_h": 2.5,
}

TTS_MODEL = "gemini-3.8-flash-tts"
HOST_VOICE = "Puck"
REVEAL_VOICE = "Zephyr"
HOST_STYLE = ("Energetic game-show host, playful and warm, clear American accent, "
              "slight suspense before revealing answers.")
REVEAL_STYLE = "Excited reveal, short and punchy."

OWNER = "obelixagency"
CONTENT_REPO = f"{OWNER}/fbc-content"
MEDIA_REPO = f"{OWNER}/fbc-media"
MEDIA_BASE_URL = f"https://{OWNER}.github.io/fbc-media"
HYPERFRAMES = "hyperframes@0.8.170"


class NotConfigured(RuntimeError):
    """A required secret/variable is missing."""


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise NotConfigured(name)
    return value
