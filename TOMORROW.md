# AMG OS — Resume Tomorrow

## Where things stand (end of session 2026-05-04, ~14:00)

HEAD is at v11.1.4 with the bake-off harness and the env-var override
in place (commits cd38015 and 4d1df06 sit on top of the v11.1.4 bump
at 557df15). Tests 67/67 green. Source tree clean.

The PyAV cherry-pick is back in (commit fbc03b7) and the cluster cap
that PyAV exposed the need for landed (commit 6d42fae). All the
"Recovery path" items in the prior version of this file are DONE.

Switched from Claude Code to Cursor at session end. AGENTS.md and
SESSION_NOTES_2026-05-04.md added at the repo root for handoff.

## What we now know

PyAV decode is real and worth keeping (~3-5x faster than OpenCV on 4K
HEVC; ~1.86x on 1080p). One known regression: scene 26 (8.3GB 4K, the
highest-bitrate file in the set) hangs PyAV at Tier 1 — extracted
4 frames in 43 minutes before the time-budget hard cap kicked in.
Likely an HEVC profile or encoder quirk specific to that file. v11.1.4
auto-recovered cleanly via the time deadline; no autonomous kill needed.
Worth reproducing under the OpenCV backend (`AMG_VIDEO_BACKEND=opencv
amg process ...`) when you have time, to confirm the file processes
fine that way and isolate the PyAV blame.

The cluster cap (CLUSTER_HUNTER_TOP_N=30) saved roughly 60 min of AI
scoring across the 10-scene batch. Single biggest impact: scene 7
(birthday gangbang) where the cap dropped 230 cluster candidates in
one run. Without the cap, that scene alone would have been ~1h 35m
longer.

Bake-off ran tonight: qwen2.5vl:7b vs qwen2.5vl:3b vs minicpm-v:8b
on scenes 4 and 7. Wall-time spread across all three models was
within 3% of each other (47:39 — 49:21 over both scenes). Cover
counts varied (24-99) but the actual frame picks were "very similar
in output" per Mario's eye on the contact sheets. Conclusion: model
swap is not the speed lever.

## The actual speed lever

The pipeline is presenting too many covers because the architecture
is "score everything and rank top N." Mario's sketched alternative:
quota-fill by shot category. Rough categories from his note:

- ~3 thumbnail/poster shots (all actors visible, posing for camera)
- ~3 shots of EACH sex position
- ~3 buildup shots (intense action, faces)
- ~3 finishing/climax shots
- ~3 aftermath shots (bodily fluids visible)

Strengths: massively fewer AI calls (score until each category has
its 3, then stop), predictable output shape, better aligned with
how the operator actually uses the covers.

Tradeoff: classification ("what kind of shot is this?") is harder
than scoring ("how cover-worthy?"), and "sex positions" needs a
consistent vocabulary the model can apply. Multi-week rebuild —
call it v11.2 (the real one) or v12.

## Smaller win available right now

`save_covers` doesn't enforce `COVER_CAPS` (the 25-cover ceiling
for 5-25 min videos). That's why the batch produced 24/61/48/48/.../80/93
covers per scene with no upper bound. Enforcing the existing cap would
cut review burden 3-4x without any architectural change. Stepping
stone toward the quota-fill rebuild.

## Open issues deferred (kept from prior version)

- Score compression at 8.5 (Tier C lift only partially fixed it)
- Bake-off harness has a sanitization bug — `find_latest_decision_log`
  replaces hyphens with underscores, but the pipeline keeps hyphens.
  Result: every cell in the auto-generated SUMMARY.txt shows ERR even
  on successful runs. Easy fix (one regex change).
- No PSD integration
- No upload automation
- No Google Sheets sync
- Performer code only fits 3 of 12 creators
- 2257 binary toggle vs multi-tiered reality
- Amy's intake PDF still not actually read (no poppler installed; we've
  been working from the markdown template, which is unfilled)
- 85% revenue from unnamed platforms

## Cursor handoff notes

- AGENTS.md is the cross-tool entry point — Cursor's AI auto-loads it.
  Points at CLAUDE.md, CLAUDE_CODE_HANDOFF.md, this file, and
  SESSION_NOTES_2026-05-04.md.
- CLAUDE.md is unchanged — still useful as the project deep-dive.
- CLAUDE_CODE_HANDOFF.md is unchanged — still the strategic brief.
- Open in Cursor: install Cursor's `cursor` CLI from
  Cursor menu > "Install 'cursor' command", then `cursor ~/AMG_OS`.
- Cursor auto-detects the venv at `venv/` and the git repo. The
  Anthropic API model behind Cursor's chat will see all the same
  files; what it does NOT have is this conversation's transcript,
  so SESSION_NOTES_2026-05-04.md is what bridges that gap.

## Lessons (kept + added)

- Do not kill long-running processes based on CPU time alone. Check
  log lines first.
- One change, one commit, one validation.
- Mario's eye is the only metric that ships.
- Decode speedup unlocks downstream caps. Anytime a phase becomes
  faster, audit whether it now overflows the next phase's bounds.
- Determinism (temperature=0 + seed=42) is content-dependent in
  practice — locks behavior on visually-uniform scenes (BG bath),
  works fine on visually-varied scenes (BGG threesome).

## Planned next (not started — add when ready)

- **Safe mode (memory-aware):** When Activity Monitor shows high swap /
  yellow memory pressure, auto-reduce `OLLAMA_NUM_PARALLEL` and AMG’s
  vision worker count to match (e.g. 4→2), optionally raise
  `OLLAMA_KEEP_ALIVE` tradeoffs only after tuning. Goal: fewer tail
  stalls, not higher peak GPU %.

- **Scoring / framing vNext (POV + shot grammar):** Separate **camera
  framing** (POV / traditional third-person / JOI / mixed) from **act
  type** (oral, penetration, tease, finish). Explicit bonuses for
  **oral close-ups** (toy/penis in frame + faces), **dual subject +
  lens awareness** (poster-style without requiring strict “eye contact”
  if composition reads as intentional), and **non-blurry action beats**
  (e.g. spit / fluid moments). Revisit **hard cover cap vs soft cap**
  when many frames cluster ≥8.0 — keep floor for minimum deliverables,
  allow optional “overflow bucket” for operator review. Calibrate on
  uploaded reference run `20260504_173558_2025-05-16_16.33.49` (good:
  shot 1 ~8.5 dual pose; shot 4 ~9.5 spit beat without eye-contact
  requirement).

- **UI / jobs:** If `POST /jobs` returns 400 while a run still proceeds,
  treat as duplicate or empty submit (HTMX); add client-side debounce or
  clearer error surface when no files attached.

## Archive location

~/AMG_archive_20260504/ also on external drive.
