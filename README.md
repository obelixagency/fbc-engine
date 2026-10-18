# fbc-engine — Family Brain Clash automation

Weekly family-trivia Shorts + one long "Family Quiz Night" for YouTube, Instagram Reels and Facebook Reels.

- `generate.yml` (Fri): Gemini writes 14 Shorts + 1 long video, fact-checks every answer with Google Search grounding, opens a review PR in the private `fbc-content` repo.
- `tick.yml` (hourly): after the PR is merged, voices (Gemini TTS: Puck host, Zephyr reveals), picks Pexels videos, renders with HyperFrames, QA-checks, and publishes on schedule (07:00 / 16:00 ET daily, long video Sat 10:00 ET). Late or failed items open an Issue.
- `analytics.yml` (daily): scores videos after 72 h and re-weights formats, categories, hooks and slots.

Setup: see [SETUP.md](SETUP.md). Design: `docs/superpowers/specs/`. Plan: `docs/superpowers/plans/`.

Local tests: `pip install -e ".[dev]" && pytest -q` (add `-m slow` to render a real fixture video; needs Node 22 + ffmpeg).
