# Family Brain Clash Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `fbc-engine`, a GitHub Actions pipeline that generates a weekly batch of family-trivia Shorts and one long video, waits for Mohamed's PR approval, then voices, renders, QA-checks and publishes them to YouTube, Instagram Reels and Facebook Reels, and tunes itself from analytics.

**Architecture:** A Python 3.11 package `fbc` does orchestration (Gemini text and TTS, Pexels, scheduling, publishing, analytics). Video is rendered by the HyperFrames CLI (`npx hyperframes@0.8.170 render`) from HTML compositions that Python writes per video. Three workflows run in the public `fbc-engine` repo: `generate.yml` (weekly), `tick.yml` (hourly: produce what's due soon, publish what's due now, escalate what's late), `analytics.yml` (daily). All content lives in the private `fbc-content` repo; rendered MP4s are stored as private release assets there and copied to the public `fbc-media` Pages repo only inside the tick that publishes them.

**Tech Stack:** Python 3.11, `requests`, `jinja2`, `pytest`; Node 22 + HyperFrames 0.8.170 + headless Chromium; ffmpeg/ffprobe; `gh` CLI for the content repo; Gemini API (REST), Pexels API, YouTube Data API v3 and YouTube Analytics API v2, Meta Graph API v21.0.

**Spec:** `docs/superpowers/specs/2026-10-17-family-brain-clash-design.md`

## Global Constraints

- Every upload: YouTube `selfDeclaredMadeForKids=false`, `containsSyntheticMedia=false`, `categoryId="24"`, `defaultLanguage="en"`.
- Copy never says "for kids", "kids' quiz", "toddler", "preschool"; it addresses the family/parents ("Can you beat your kids?"). Enforced by `fbc.copy_rules.check_copy()`.
- Question ≤ 12 words; each option ≤ 3 words; fun fact ≤ 15 words; Short title < 60 chars.
- No question repeats within 180 days; no Pexels clip repeats within 30 days.
- Brand tokens: navy `#16133F`, yellow `#FFD23F`, pink `#FF3E7F`, green `#2EE59D` (answer reveal only), white `#FFFFFF`; fonts Archivo Black (questions, titles) and Archivo (options, facts), vendored as woff2 under `templates/fonts/`.
- Navy overlay on every background clip: `rgba(22,19,63,0.45)`.
- Shorts 1080×1920, 22–35 s; long 1920×1080, 8–12 min; 30 fps.
- Safe zone in Shorts: no text in the top 250 px or bottom 420 px.
- TTS model `gemini-3.8-flash-tts`; host voice `Puck`; long-video answer voice `Zephyr`; host style `"Energetic game-show host, playful and warm, clear American accent, slight suspense before revealing answers."`; reveal style `"Excited reveal, short and punchy."`
- Text model from env `GEMINI_TEXT_MODEL` (no hard-coded default in code; set as a repo variable).
- Publish slots: Shorts 07:00 and 16:00 America/New_York daily; long video Saturday 10:00 America/New_York.
- Nothing publishes from a batch that is not merged into `fbc-content` `main`.
- Public-repo hygiene: workflow logs are public, so no step prints question text, captions, tokens or URLs of unpublished media. Use the `fbc.log.safe()` logger, which only prints ids and counts. No `pull_request_target`; secret-using workflows run only on `schedule` and `workflow_dispatch`.
- Escalate (open a GitHub Issue in `fbc-content`) when a slot is more than 2.5 h late.
- No paid services. On any Gemini 429, stop the current job cleanly and resume next tick.

## Review Focus

1. **Gemini free-tier quota runs out mid-week.** Production must resume where it stopped and stay ahead of the schedule (produce anything due within 48 h), never publish a half-voiced video. Test in Task 7.
2. **Mohamed edits `batch.json` on his phone and breaks it** (trailing comma, deleted field, 4 options). `produce` must reject the batch with a clear Issue, not crash silently or render garbage. Test in Task 3.
3. **Pexels has no usable portrait clip for a query.** Fall back to a landscape clip cropped to 9:16, then to a photo with Ken Burns; never render a blank background. Test in Task 6.
4. **A platform publish fails for one network only** (expired Meta token). The other platforms still publish; the failed one retries each tick and escalates after 2.5 h. Test in Task 10.
5. **Daylight-saving change in America/New_York** (Nov 1 2026). Slots stay at 07:00/16:00 local, no double or skipped posts. Test in Task 9.

---

## File Structure

```
fbc/
  __init__.py
  config.py          brand tokens, slots, limits, env access (env(name) raises NotConfigured)
  log.py             safe(): id/count-only logging for public logs
  models.py          Question, ShortItem, LongItem, Batch dataclasses + parse/validate
  copy_rules.py      check_copy(text) -> list[str]
  dedupe.py          normalize, fingerprint, is_duplicate
  weights.py         Weights load/save, score_video, update_weights, pick_formats
  gemini.py          generate_json, tts_wav
  generate.py        build_batch
  verify.py          verify_questions
  pexels.py          find_clip, download
  audio.py           build_audio_request, synthesize
  timeline.py        Segment, Timeline, build_short_timeline, build_long_timeline
  render.py          write_project, render_mp4, render_thumbnails
  sfx.py             make_sfx (ffmpeg-generated tick, ding, whoosh)
  qa.py              check_video
  store.py           ContentRepo: batches, queue, used bank, release assets, issues
  schedule.py        week_slots, due_items, late_items
  publish/
    __init__.py      publish_item -> per-platform results
    youtube.py       upload_video, set_thumbnail, add_comment
    meta.py          fb_reel, ig_reel, fb_comment, ig_comment (adapted from Unshaken publish.py)
    media_host.py    push_public, prune_public
  analytics.py       collect_stats
  cli.py             generate | produce | publish | analytics | tick
templates/
  tokens.css, fonts/*.woff2
  short.html.j2, long.html.j2, thumb.html.j2
prompts/
  generate_short.md, generate_long.md, verify.md, titles.md
tests/  (mirrors fbc/)
.github/workflows/  generate.yml, tick.yml, analytics.yml, ci.yml
SETUP.md
```

Deviation from spec §4 (noted for Mohamed): `produce.yml`, `publish.yml` and `watchdog.yml` are merged into one hourly `tick.yml`. Same behavior, fewer moving parts, and production can resume after a quota stop. Spec §6.1's Would-You-Rather "imagined vote percentage" is dropped: a made-up poll number would be fabricated data; the Short asks viewers to vote in the comments instead.

---

### Task 1: Project skeleton, config and safe logging

**Files:**
- Create: `pyproject.toml`, `fbc/__init__.py`, `fbc/config.py`, `fbc/log.py`, `.gitignore`, `.github/workflows/ci.yml`
- Test: `tests/test_config.py`, `tests/test_log.py`

**Interfaces:**
- Produces: `config.BRAND: dict[str,str]`, `config.SHORT_SLOTS_ET = ["07:00","16:00"]`, `config.LONG_SLOT_ET = ("SAT","10:00")`, `config.LIMITS` (word/char limits above), `config.env(name: str) -> str` raising `config.NotConfigured(name)`; `log.safe(event: str, **fields)` printing only keys in `{"id","count","platform","status","week"}`.

- [ ] **Step 1: Write failing tests**: `test_env_missing_raises` (`env("NOPE")` raises `NotConfigured`), `test_brand_tokens` (exact five hex values), `test_safe_drops_text` (`safe("x", id="q1", question="secret")` output contains `q1`, not `secret`).
- [ ] **Step 2: Run** `pytest -q` → FAIL (module missing).
- [ ] **Step 3: Implement** the three modules. `pyproject.toml` deps: `requests`, `jinja2`, `python-dateutil`; dev: `pytest`. `ci.yml` runs `pytest -q` on `push` and `pull_request` (no secrets).
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat: project skeleton, config, safe logging`.

### Task 2: Copy rules and dedupe

**Files:** Create `fbc/copy_rules.py`, `fbc/dedupe.py`; Test `tests/test_copy_rules.py`, `tests/test_dedupe.py`

**Interfaces:**
- Produces: `check_copy(text: str) -> list[str]` (problems; banned phrases case-insensitive: "for kids", "kids quiz", "kids' quiz", "toddler", "preschool", "nursery"); `normalize(text) -> str` (lowercase, strip punctuation, collapse spaces); `fingerprint(q: str, answer: str) -> str` (sha1 of normalized question + answer); `is_duplicate(q: str, answer: str, used: list[dict], days: int = 180, today: date) -> bool` (exact fingerprint match, or token Jaccard ≥ 0.8 on questions with the same normalized answer, within `days`).

- [ ] **Step 1: Write failing tests**: `check_copy("Fun quiz for kids")` → 1 problem; `check_copy("Can you beat your kids?")` → `[]`; duplicate when only punctuation/case differs; not duplicate when the used entry is 181 days old; Jaccard case: "Which animal can sleep standing up?" vs "What animal is able to sleep standing up?" with same answer → duplicate.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: copy rules and question dedupe`.

### Task 3: Batch model and validation

**Files:** Create `fbc/models.py`; Test `tests/test_models.py`, `tests/fixtures/batch_valid.json`, `tests/fixtures/batch_broken_*.json`

**Interfaces:**
- Produces:
  - `Question(id, category, difficulty: Literal["easy","medium","hard"], question, options: list[str], answer_index: int, fun_fact, source_url, visual_query, verified: bool, flags: list[str])`. For `tf` format `options == ["True","False"]`; for `riddle` and `zoom`, `options` may be `[]` and the answer is `fun_fact`'s subject in `answer` field: add `answer: str` (always set; for MCQ equals `options[answer_index]`).
  - `ShortItem(id, format: Literal["three_q","zoom_reveal","riddle","parents_vs_kids","true_false","would_you_rather"], hook, questions: list[Question], titles: list[str], chosen_title_index: int = 0, description, first_comment)`
  - `LongItem(id, episode: int, rounds: list[dict(name:str, questions:list[Question])], bonus: Question, titles: list[str], chosen_title_index, description, chapters: list[str])`
  - `Batch(week: str "YYYY-WW", shorts: list[ShortItem], long: LongItem, report_md: str)`
  - `parse_batch(raw: str) -> Batch` raising `BatchError(list[str])` with every problem found (not just the first).
  - `Batch.to_json() -> str` (stable key order, 2-space indent).
- Validation rules: the Global Constraints limits; MCQ has exactly 3 options; `answer_index` in range; questions per format: three_q 3, zoom_reveal 1, riddle 1, parents_vs_kids 2, true_false 5, would_you_rather 1 (options 2, no correct answer: `answer_index = -1`); long has 5 rounds × 6 + bonus; `check_copy` on hook, titles, description, first_comment; `chosen_title_index` in range.

- [ ] **Step 1: Write failing tests**: valid fixture round-trips (`parse_batch(b.to_json()) == b`); broken fixtures each produce the expected message: trailing comma → `"batch.json is not valid JSON (line N)"`, 4 options → `"q_…: needs exactly 3 options"`, 14-word question → `"q_…: question has 14 words (max 12)"`, missing `fun_fact` → `"q_…: missing fun_fact"`, two problems in one file → both reported.
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Commit** `feat: batch model and validation`.

### Task 4: Gemini client and weights

**Files:** Create `fbc/gemini.py`, `fbc/weights.py`; Test `tests/test_gemini.py` (HTTP mocked with `requests` monkeypatch), `tests/test_weights.py`

**Interfaces:**
- Produces:
  - `generate_json(prompt: str, schema: dict, grounded: bool = False) -> dict | list` — POST `https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TEXT_MODEL}:generateContent` with `responseMimeType: application/json` + `responseSchema` (when `grounded`, send `tools: [{"google_search": {}}]` and no schema, then parse the first JSON block from text). Raises `QuotaExceeded` on HTTP 429.
  - `tts_wav(text: str, voice: str, style: str) -> bytes` — model `gemini-3.8-flash-tts`, `responseModalities: ["AUDIO"]`, `speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName = voice`; the style is sent as the instruction prefix `"{style}\n\nSay: {text}"`; response PCM s16le 24 kHz mono is wrapped into a WAV. Raises `QuotaExceeded` on 429.
  - `Weights` = `{"formats": {name: float}, "categories": {...}, "hooks": {...}, "slots": {...}}`; `default_weights()` equal weights over the six formats and the categories `animals, geography, science, food, space, human_body, history, sports, riddles, words`.
  - `score_video(avg_view_pct, shares_rate, comments_rate, subs_rate) -> float` = `0.5*avg_view_pct + 0.2*shares_rate + 0.2*comments_rate + 0.1*subs_rate` (rates as fractions 0–1, avg_view_pct as 0–1).
  - `update_weights(w: Weights, scored: list[dict(format, category, hook_id, score)]) -> Weights` — per key, new = 0.7·old + 0.3·(mean score of that key ÷ overall mean), floored at 0.05, renormalized.
  - `pick_formats(w: Weights, n: int, rng: random.Random) -> list[str]` — `round(0.2*n)` slots uniform-random (exploration), rest sampled by weight; every format appears at least once when `n ≥ 6`.

- [ ] **Step 1: Write failing tests**: `generate_json` sends schema and parses; 429 → `QuotaExceeded`; `tts_wav` output starts with `b"RIFF"` and has 24000 Hz in header; `score_video(0.8,0.1,0.1,0.01)` == 0.441; `pick_formats(n=14)` covers all six formats and has 3 exploration picks (seeded rng); weights stay ≥ 0.05 and sum to 1.
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Commit** `feat: gemini client and weights`.

### Task 5: Batch generation and verification

**Files:** Create `fbc/generate.py`, `fbc/verify.py`, `prompts/generate_short.md`, `prompts/generate_long.md`, `prompts/verify.md`, `prompts/titles.md`; Test `tests/test_generate.py`

**Interfaces:**
- Consumes: `generate_json`, `pick_formats`, `is_duplicate`, `parse_batch`, `check_copy`.
- Produces:
  - `verify_questions(qs: list[Question]) -> list[Question]` — one grounded call per 10 questions returning `[{id, correct: bool, answer, source_url, note}]`; sets `verified`, `source_url`; mismatched or unsourced → `flags += ["⚠️ unverified: <note>"]`, `verified=False`.
  - `build_batch(week: str, weights: Weights, used: list[dict], episode: int, rng) -> Batch` — generates 20% extra candidates, drops duplicates and copy-rule failures, verifies, fills 14 Shorts by `pick_formats(weights, 14)` and the long video (5 rounds × 6 + bonus, ≥ 60% questions not used in this week's Shorts — achieved by generating them separately), 3 titles per item, hook chosen from the hooks bank by weight; raises `BatchError` if fewer than 14 Shorts survive.
  - `report_md` = previous week's top/bottom 3 by score + weight changes (empty section on week 1).
- Prompts carry the spec §2–§3 rules verbatim (family audience, no "for kids", limits, US English, no brands/characters). `prompts/generate_short.md` includes one worked example per format.

- [ ] **Step 1: Write failing tests** with a fake `generate_json`: 14 Shorts with the picked formats; duplicate candidates removed; an unverified question carries the ⚠️ flag and stays in the batch (Mohamed decides); a candidate titled "Quiz for kids" is dropped; too few survivors → `BatchError`.
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Commit** `feat: weekly batch generation with grounded verification`.

### Task 6: Pexels visuals

**Files:** Create `fbc/pexels.py`; Test `tests/test_pexels.py`

**Interfaces:**
- Produces: `Clip(kind: Literal["video","photo"], pexels_id: int, url: str, width, height, duration: float | None)`; `find_clip(query: str, orientation: Literal["portrait","landscape"], exclude_ids: set[int]) -> Clip` — Videos API first (`min_duration=6`, file ≥ 1080 on the short side, prefer the smallest file meeting that), then same query with the other orientation (crop later), then Photos API (`Ken Burns` later); raises `NoVisual(query)` only if all three return nothing; `download(clip, dest: Path) -> Path`.
- `exclude_ids` comes from `store.recent_clip_ids(days=30)`.

- [ ] **Step 1: Write failing tests** (HTTP mocked): portrait video chosen; excluded id skipped; no portrait → landscape clip returned; no video → photo; nothing → `NoVisual`.
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Commit** `feat: pexels clip finder with fallbacks`.

### Task 7: Voice: audio_request.json and synthesis

**Files:** Create `fbc/audio.py`, `fbc/sfx.py`; Test `tests/test_audio.py`

**Interfaces:**
- Consumes: `tts_wav`, `ShortItem`, `LongItem`.
- Produces:
  - `build_audio_request(item: ShortItem | LongItem) -> dict` — exactly the spec §8 shape (`model`, `video_id`, `default_voice`, `default_style`, `lines[{id, text, language:"en-US", voice?, style?}]`). Line ids: `hook`, `q{n}`, `q{n}_reveal`, `outro` for Shorts; for the long video `intro`, `r{r}q{n}`, `r{r}q{n}_reveal` (voice `Zephyr`), `r{r}_score`, `bonus`, `bonus_reveal`, `outro`. Reveal text = `"It's {answer}! {fun_fact}"`.
  - `synthesize(req: dict, outdir: Path) -> dict[str, float]` — writes `{line_id}.wav`, skips lines whose wav already exists (resumable), returns durations from ffprobe; `QuotaExceeded` propagates after keeping finished files.
  - `make_sfx(outdir: Path)` — `tick.wav` (1 kHz 40 ms), `ding.wav` (two-tone 880/1320 Hz 350 ms), `whoosh.wav` (filtered noise 300 ms), generated with ffmpeg `sine`/`anoisesrc`.

- [ ] **Step 1: Write failing tests**: request for a three_q Short has 8 lines in order and reveal lines carry the reveal style; long request reveal lines use `Zephyr`; `synthesize` with a fake `tts_wav` that raises `QuotaExceeded` on the 3rd call keeps 2 wavs, and a second run makes only the remaining calls (Review Focus 1).
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Commit** `feat: audio requests, resumable TTS, generated sfx`.

### Task 8: Timeline and HyperFrames rendering

**Files:** Create `fbc/timeline.py`, `fbc/render.py`, `templates/short.html.j2`, `templates/long.html.j2`, `templates/thumb.html.j2`, `templates/tokens.css`, `templates/fonts/` (Archivo Black 400, Archivo 400/600/700 woff2, OFL); Test `tests/test_timeline.py`, `tests/test_render.py`

Read the `hyperframes-core` and `hyperframes-animation` skills before writing the templates.

**Interfaces:**
- Consumes: durations from `synthesize`, clips from `find_clip`, sfx files.
- Produces:
  - `Segment(kind: Literal["hook","mcq","tf","riddle","wyr","zoom","pvk_label","score","outro"], start: float, end: float, data: dict, audio: list[tuple[str, float]], sfx: list[tuple[str, float]], clip: Path | None)`; `Timeline(width, height, fps=30, duration, segments)`.
  - `build_short_timeline(item, durations, clips) -> Timeline` — per question: read (voice) → countdown 3 s with a tick each second (5 s for riddle) → reveal (ding + green flash + reveal voice); hook ≤ 1.5 s budget (hook text shows at t=0 even if the voice is longer); outro ≤ 3 s; total clamped to 22–35 s by shortening countdowns to a 2 s minimum, else raising `TimelineError`.
  - `build_long_timeline(item, durations, clips) -> Timeline` — countdown 5 s per question, round-score card after each round, bonus, tier card (Rookie 0–10, Brainiac 11–22, Family Champion 23–31), 20 s end screen; also returns `chapters: list[str]` as `"M:SS Round name"`.
  - `write_project(tl: Timeline, assets: dict, template: str, outdir: Path) -> Path` — renders the Jinja template into a HyperFrames project (index.html + copied assets).
  - `render_mp4(project: Path, out: Path) -> Path` — `npx -y hyperframes@0.8.170 render <project> -o <out>`; raises `RenderError` with stderr tail.
  - `render_thumbnails(item: LongItem, clips, outdir) -> list[Path]` — 3 variants 1280×720 PNG (different headline from the 3 titles), via `npx hyperframes@0.8.170 snapshot`.
- Template rules (from spec §6.2 and §9): progress bar on top; background always a moving clip (slow zoom; photo → Ken Burns; landscape clip in Shorts → center crop); navy overlay; text inside the safe zone; loop: the last 0.5 s cross-fades to the first frame's composition; zoom_reveal starts at scale 4 and eases to 1 over the countdown.

- [ ] **Step 1: Write failing tests**: three_q timeline duration between 22 and 35 s given 1.2 s lines; tick sfx exactly at countdown seconds; long timeline chapters start with `"0:00 Intro"` and has 5 round chapters; overlong lines → countdowns shortened to 2 s; still too long → `TimelineError`.
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Golden render**: `tests/test_render.py::test_render_fixture_short` (marked `slow`) renders `tests/fixtures/batch_valid.json` Short #1 with fixture wavs and a fixture clip; asserts the MP4 exists, is 1080×1920, 30 fps, and `qa.check_video` is empty. Also run `npx hyperframes@0.8.170 snapshot` at 1 s, 8 s, 26 s and look at the PNGs.
- [ ] **Step 6: Commit** `feat: timelines and hyperframes templates`.

### Task 9: QA, content store and schedule

**Files:** Create `fbc/qa.py`, `fbc/store.py`, `fbc/schedule.py`; Test `tests/test_qa.py`, `tests/test_store.py`, `tests/test_schedule.py`

**Interfaces:**
- Produces:
  - `check_video(path: Path, kind: Literal["short","long"]) -> list[str]` — ffprobe: duration within limits, 1080×1920 or 1920×1080, 30 fps, has an audio stream; ffmpeg `silencedetect=noise=-45dB:d=1.2` finds no silence ≥ 1.2 s outside countdown windows (countdown windows passed in via a sidecar `qa.json` written by `render`).
  - `ContentRepo(path: Path, gh_repo: str)` with `load_batch(week) -> Batch`, `is_merged(week) -> bool` (batch file present on `main`), `queue() -> list[dict]` / `save_queue(q)`, `used() -> list[dict]` / `append_used(entries)`, `recent_clip_ids(days) -> set[int]`, `upload_asset(week, path) -> str` (`gh release upload week-<week>` into the private repo, creating the release if needed), `download_asset(week, name, dest)`, `open_issue(title, body)`, `open_batch_pr(week, batch_json, body_md)`.
  - Queue entry: `{id, kind, week, slot_utc, status: "planned"|"produced"|"published"|"failed", asset, platforms: {youtube|instagram|facebook: {status, ref, error, attempts}}}`.
  - `week_slots(week: str) -> list[tuple[str, datetime]]` — 14 Short slots (07:00 and 16:00 ET, Mon–Sun of that ISO week following the Friday generation) + 1 long slot (Sat 10:00 ET), as UTC datetimes via `zoneinfo("America/New_York")`.
  - `due_items(queue, now) -> list[dict]`; `late_items(queue, now, grace=timedelta(hours=2.5)) -> list[dict]`.

- [ ] **Step 1: Write failing tests**: `check_video` flags a 40 s Short and a silent file; queue round-trip; `week_slots("2026-W44")` (the week containing the Nov 1 2026 DST change) gives 07:00 local both before and after the change, 15 slots, no duplicates (Review Focus 5); a slot 3 h past due and unpublished is late; one 2 h late is not.
- [ ] **Step 2–4:** fail → implement → pass (`gh` calls behind a small `run_gh()` that tests replace).
- [ ] **Step 5: Commit** `feat: qa checks, content store, ET schedule`.

### Task 10: Publishing

**Files:** Create `fbc/publish/__init__.py`, `fbc/publish/youtube.py`, `fbc/publish/meta.py`, `fbc/publish/media_host.py`; Test `tests/test_publish.py`

**Interfaces:**
- Consumes: Unshaken's `publish.py` patterns (copy the Graph API helpers `_check`, `_ig_wait_ready`, `_ig_create_and_publish`, cover offset), queue entries, `ContentRepo`.
- Produces:
  - `youtube.upload_video(path, title, description, tags, is_short: bool) -> str` — resumable upload; status `privacyStatus="public"`, `selfDeclaredMadeForKids=False`, `containsSyntheticMedia=False`; snippet `categoryId="24"`, `defaultLanguage="en"`, `defaultAudioLanguage="en"`; Shorts title gets ` #Shorts` only if it still fits under 100 chars. Returns video id. Note: until the API audit passes, YouTube forces uploads to private; `upload_video` returns the id regardless and the tick logs `status="private_pending_audit"` when the returned status is private.
  - `youtube.set_thumbnail(video_id, png)`; `youtube.add_comment(video_id, text)` (top-level comment by the channel).
  - `meta.fb_reel(path, caption) -> str` — Reels resumable flow `/{page}/video_reels` start → upload → finish with `video_state=PUBLISHED` (so it lands in the Reels tray, unlike Unshaken's `/videos`).
  - `meta.ig_reel(public_url, caption) -> str` — `media_type=REELS`, `share_to_feed=true`.
  - `meta.fb_comment(object_id, text)`, `meta.ig_comment(media_id, text)`.
  - `media_host.push_public(path) -> str` (commit into `fbc-media` and return `https://obelixagency.github.io/fbc-media/<file>`; wait until the URL returns 200, up to 5 min); `prune_public(days=14)`.
  - `publish_item(entry: dict, repo: ContentRepo, now) -> dict` — per platform independently: skip platforms already `published`; on failure record error, `attempts += 1`; one platform's failure never blocks the others (Review Focus 4). Posts `first_comment` after each successful upload.

- [ ] **Step 1: Write failing tests** (HTTP mocked): YouTube upload body carries the four fixed status/snippet values; FB reel uses the 3-phase reels endpoint; Instagram failure leaves YouTube and Facebook `published` and Instagram `failed` with attempts 1; a second call retries only Instagram.
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Commit** `feat: youtube and meta publishing with per-platform retry`.

### Task 11: Analytics loop

**Files:** Create `fbc/analytics.py`; Test `tests/test_analytics.py`

**Interfaces:**
- Produces: `collect_stats(queue, now) -> list[dict(id, format, category, hook_id, platform, views, avg_view_pct, likes, comments, shares, subs_gained)]` for items published ≥ 72 h ago and not yet scored — YouTube Analytics API v2 `reports` (`ids=channel==MINE`, metrics `views,averageViewPercentage,likes,comments,shares,subscribersGained`, `filters=video==<id>`), Meta Insights (metrics `plays, likes, comments, shares`; avg view % = `ig_reels_avg_watch_time` ÷ video duration on Instagram, `fb_reels_avg_time_watched` ÷ duration on Facebook); scores with `score_video`, then `update_weights`; writes `analytics/weights.json` and appends rows to `analytics/scores.jsonl` in the content repo.

- [ ] **Step 1: Write failing tests** (HTTP mocked): an item published 71 h ago is skipped; scored items update the format weight in the right direction; missing Meta metrics → that platform omitted, YouTube still scored.
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Commit** `feat: analytics loop`.

### Task 12: CLI and workflows

**Files:** Create `fbc/cli.py`, `.github/workflows/generate.yml`, `.github/workflows/tick.yml`, `.github/workflows/analytics.yml`; Test `tests/test_cli.py`

**Interfaces:**
- Produces: `python -m fbc.cli generate --week YYYY-WW [--dry-run]`, `produce --horizon-hours 48`, `publish`, `tick` (= produce then publish then escalate), `analytics`. `--dry-run` on every command: no uploads, no PRs, writes to `./out/`.
- `generate.yml`: cron `0 7 * * 5` (Fri 10:00 Cairo), checks out `fbc-content` with `CONTENT_REPO_TOKEN`, runs `generate`, opens the PR. PR body = Markdown table (id · format · question · answer · ⚠️ flags · source) + the 3 titles per item + report. Sunday 17:00 UTC reminder step: if the week's PR is still open, comment on it.
- `tick.yml`: cron `5 * * * *`; `concurrency: tick` (no overlap); installs ffmpeg, Node 22, Python deps, caches `~/.npm` and the HyperFrames Chromium; `produce` renders queue items whose slot is within 48 h and status `planned` (one at a time, QA-gated; QA failure → status `failed` + Issue); `publish` handles `due_items`; escalation opens one Issue per late item (not repeated: label `late-<id>`).
- `analytics.yml`: cron `0 12 * * *`.
- Secrets: `GEMINI_API_KEY`, `PEXELS_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`, `FB_PAGE_ID`, `FB_PAGE_TOKEN`, `IG_USER_ID`, `CONTENT_REPO_TOKEN` (fine-grained PAT: contents+PRs+issues on `fbc-content`, contents on `fbc-media`). Variable: `GEMINI_TEXT_MODEL`.

- [ ] **Step 1: Write failing tests**: `tick --dry-run` on a fixture queue produces only items within 48 h and publishes nothing; a merged-but-broken batch makes `produce` open an Issue listing every `BatchError` message (Review Focus 2) and produce nothing.
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Lint workflows**: `npx -y @action-validator/cli .github/workflows/*.yml` → no errors; grep confirms no `pull_request_target` and no `echo` of secrets or question fields.
- [ ] **Step 6: Commit** `feat: cli and github actions workflows`.

### Task 13: Setup guide and end-to-end dry run

**Files:** Create `SETUP.md` (Arabic, Egyptian dialect, step-by-step like Unshaken's); Modify `README.md`

- [ ] **Step 1:** Write `SETUP.md`: create the 3 repos, enable Pages on `fbc-media`, create secrets/variable (table from Task 12), Google Cloud (YouTube Data API v3 + YouTube Analytics API, OAuth consent, refresh token via a local `python -m fbc.cli auth youtube` helper that prints the token once, **apply for the API audit**), Meta app (reuse "Unshaken Automation" app or a new one; Page token, IG user id), Gemini key with a usage cap, Pexels key, phone verification, weekly manual steps (Related video link, Test & Compare thumbnails, publishing private uploads until the audit passes).
- [ ] **Step 2: End-to-end dry run** with real Gemini/Pexels keys if available, else fakes: `generate --dry-run` → `produce --dry-run` renders 2 Shorts + the long video → `qa` passes → look at snapshots. Record timings for the render step (budget: ≤ 6 min per Short, ≤ 25 min for the long video on `ubuntu-latest`).
- [ ] **Step 3: Commit** `docs: setup guide; chore: e2e dry run`.

---

## Self-Review Notes

- Spec coverage: §2 decisions → Tasks 1, 7, 8, 10, 12; §3 constraints → Global Constraints + Tasks 2, 3, 10, 12; §5 weekly cycle → Tasks 5, 9, 12; §6 viral engine → Tasks 4, 5, 8, 10, 11; §7 formats → Task 8; §8 voice → Task 7; §10 platforms → Task 10; §11 QA/watchdog → Tasks 9, 12; §12 tests → every task + Task 8 golden render + Task 13 dry run; §14 setup → Task 13.
- Deviations recorded above (merged tick workflow; no fake poll numbers).
- Music bed: spec allows YouTube Audio Library or code-generated; v1 ships code-generated SFX only and no music bed. A music track can be dropped into `templates/music/` later without code changes (render picks it up if present, ducked to -18 dB).
