"""AMG OS version."""
__version__ = "11.1.4"
__version_info__ = (11, 1, 4)
__release_date__ = "2026-05-04"

# v11.1.4 changelog (cluster cap — single-issue patch over v11.1.3):
# - Cap cluster phase AI scoring at CLUSTER_HUNTER_TOP_N=30 by sharpness desc.
#   v11.1.3's PyAV decode removed the implicit time-deadline cap that used to
#   bound the cluster phase; scene 10 produced 145 post-gate candidates that
#   would have been ~15 min of additional AI scoring. Mirrors the existing
#   BUILDUP_HUNTER_TOP_N=10 pattern. No other changes.
#
# v11.1.3 changelog (PyAV decode cherry-pick from v11.2 — isolated, no scope creep):
# - amg/video/reader.py rewritten with PyAV (av==13.1.0) as primary backend,
#   OpenCV preserved as automatic fallback. Linear stream decode replaces
#   OpenCV's seek-and-decode-per-frame pattern (~1.86x faster on scene 8 at 1080p,
#   expected larger speedup on 4K HEVC where the seek penalty scales with frame size).
# - Public VideoReader API unchanged — drop-in replacement for callers.
# - HWACCEL CAVEAT: PyAV 13.1 silently accepts the videotoolbox hwaccel option
#   without a way to confirm HW engagement; decoded frames come back as yuv420p
#   (software pixel format). Log message and backend_name are honest about this:
#   "videotoolbox option set; HW engagement not verifiable" rather than v11.2's
#   misleading "VideoToolbox hardware decode". Speedup is real regardless.
# - amg verify reports PyAV version + the videotoolbox-uncertain caveat on Apple Silicon.
# - NOT included from v11.2: frames_extracted counter hooks, save-time perceptual
#   dedup in covers.py, prompt changes, wider tier_2 sweep. Mario specified
#   PyAV-only; the v11.2 bundle was rolled back precisely because of those other
#   changes regressing cover quality, not because of the decode work.
#
# v11.1.2 changelog (quality patch #2 — diversity & determinism over v11.1.1):
# - E: Deterministic scoring (temperature=0 + seed=42) so prompt/scoring changes
#       can be A/B tested without Ollama RNG as a confound
# - A: Tier C lifted (C1/C2 +0.5 → +1.0, C3 +0.3 → +0.5) to fix score compression
#       at 8.0 — cinematic frames can now spread to 9-10 range
# - D: Buildup hunter dedup loosened (threshold 5 → 2 for buildup only) and
#       top-N raised (6 → 10) so tease/anticipation moments aren't crushed by
#       global dedup that's too liberal for slow zones
# - C: Cluster expansion seeds consolidated — drop seeds whose expansion windows
#       overlap a higher-scoring seed's window (eliminates the v11.1.1 pattern of
#       13 near-duplicate covers from one hot zone)
#
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
