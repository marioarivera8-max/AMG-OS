"""AMG OS version."""
__version__ = "11.1.1"
__version_info__ = (11, 1, 1)
__release_date__ = "2026-05-04"

# v11.1.1 changelog (quality patch — carries v11.1.0's "known-good" baseline forward):
# - FIX: Scoring prompt rewritten to stop DUAL-gaze hallucination
#         (B1b/B1c demoted from +3.5/+4.5 bonuses to +2.0 parity with B1a;
#          inflammatory "RARE and EXCEPTIONALLY VALUABLE" language stripped)
# - FIX: covers_dir wiped before save_covers writes (no more duplicate 01_*, 02_* on rerun)
# - FIX: Partial-success reporting — 0 < covers < COVER_FLOOR with no fatal error_codes
#         now reports as success-with-warning instead of "INCOMPLETE / Unknown error"
# - DIAG: score_frames_parallel logs per-call start/end + worker IDs at INFO
#         (instrumentation to confirm whether 4 Ollama workers actually overlap)
#
# v11.1 changelog:
# - FIX: Lesbian code parsing (G/GG/GGG/GGGG)
# - FIX: All-male code flagging (B/BB/BBB)
# - FIX: Eye contact tiering (B1a single +2.0, B1b dual +3.5)
# - NEW: Cover floor 15, caps 20-30 by duration
# - NEW: amg review (human review form with AI title suggestions)
# - NEW: amg ready (distribution-ready gate)
# - NEW: amg find (scene library search)
# - NEW: amg dvd-compile (DVD packaging)
# - NEW: amg dashboard (performance trends)
# - NEW: amg resume (recover from failures)
# - NEW: Performer document registry (reusable 2257/releases)
# - NEW: Per-platform compliance + title length awareness
# - HOOKS: v11.2/v12 architecture pre-wired
