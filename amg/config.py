"""
AMG OS Configuration — Central source of truth for all defaults.

Override hierarchy (later wins):
  1. These built-in defaults
  2. Global config file (~/AMG_OS/config.yaml)
  3. Studio profile (data/studio_profiles/{name}.json)
  4. Scene-level config (.amg_config.json in scene folder)
  5. CLI flags

All numeric values here have research justification (see v11_final_specification.md).
"""
from pathlib import Path
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    return float(raw)


def _resolve_processing_profile() -> str:
    raw = os.environ.get("AMG_PROCESSING_PROFILE", "quality").strip().lower()
    aliases = {
        "default": "quality",
        "safe": "quality",
        "standard": "balanced",
    }
    profile = aliases.get(raw, raw)
    if profile not in {"quality", "balanced", "fast", "turbo"}:
        return "quality"
    return profile


def _profile_default(default, **overrides):
    return overrides.get(PROCESSING_PROFILE, default)


def _profile_int(name: str, default: int, **overrides) -> int:
    return _env_int(name, _profile_default(default, **overrides))


def _profile_float(name: str, default: float, **overrides) -> float:
    return _env_float(name, _profile_default(default, **overrides))


def _profile_bool(name: str, default: bool, **overrides) -> bool:
    return _env_bool(name, _profile_default(default, **overrides))


# Opt-in runtime speed profiles. "quality" preserves current behavior; the
# other profiles tighten caps and disable optional passes to bound wall time.
PROCESSING_PROFILE = _resolve_processing_profile()

# ============================================================
# PATHS
# ============================================================
AMG_OS_ROOT = Path(__file__).parent.parent.resolve()

# AMG_DATA_DIR is env-overridable so AMG can keep its persistent state
# (decision logs, studio profiles, training datasets, batch summaries, etc.)
# at a custom path. Useful for server deployments, Docker volumes, and
# multi-Mac syncing scenarios. Defaults to <repo>/data.
DATA_DIR = Path(
    os.environ.get("AMG_DATA_DIR", str(AMG_OS_ROOT / "data"))
).expanduser().resolve()


def _resolve_incoming_roots() -> list:
    """
    Search paths for scene work-dir lookup (CLI resume, UI scene detail,
    review form). AMG_INCOMING_ROOTS uses the platform path separator; falls
    back to the historical defaults when unset.
    """
    raw = os.environ.get("AMG_INCOMING_ROOTS")
    if raw:
        return [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]
    home = Path.home()
    return [home / "AMG_Processing", home / "AMG_OS" / "incoming"]


INCOMING_ROOTS = _resolve_incoming_roots()
DECISION_LOGS_DIR = DATA_DIR / "decision_logs"
STUDIO_PROFILES_DIR = DATA_DIR / "studio_profiles"
PERFORMERS_DIR = DATA_DIR / "performers"
LOGS_DIR = DATA_DIR / "logs"
BACKUPS_DIR = DATA_DIR / "backups"
TRAINING_DIR = DATA_DIR / "training"
TRAINING_EXAMPLES_DIR = TRAINING_DIR / "examples"
TRAINING_DATASETS_DIR = TRAINING_DIR / "datasets"
TRAINING_TEXT_DIR = TRAINING_DIR / "text"
TRAINING_SCORING_DIR = TRAINING_DIR / "scoring"
TRAINING_REGISTRY_PATH = TRAINING_DIR / "registry.jsonl"
TRAINING_FLAGS_PATH = TRAINING_DIR / "flags.jsonl"
TRAINING_RETRAIN_RUNS_DIR = TRAINING_DIR / "retrain_runs"
TRAINING_ACTIVE_SCORER_PATH = TRAINING_DIR / "active_scorer.json"
TRAINING_RULE_PACKS_DIR = TRAINING_DIR / "rule_packs"
TRAINING_RULE_RUNS_DIR = TRAINING_DIR / "rule_runs"
TRAINING_ACTIVE_RULE_PACK_PATH = TRAINING_DIR / "active_rule_pack.json"
RULE_CANARY_PCT = float(os.environ.get("AMG_RULE_CANARY_PCT", "35"))

# Optional global config
GLOBAL_CONFIG_PATH = AMG_OS_ROOT / "config.yaml"

# Lock file for concurrent batch protection
BATCH_LOCK_FILE = DATA_DIR / ".batch.lock"

# ============================================================
# OLLAMA / AI
# ============================================================
# OLLAMA_HOST is env-overridable so AMG can point at a remote Ollama
# (LAN GPU box, Tailscale node, Runpod TCP proxy, Runpod HTTPS proxy, etc.).
# Accepted forms:
#   "127.0.0.1:11434"                       -> http://127.0.0.1:11434  (local default)
#   "192.168.1.50:11434"                    -> http://192.168.1.50:11434
#   "https://podid-11434.proxy.runpod.net"  -> use HTTPS scheme as-is
#   "http://server.local:11434"             -> use HTTP scheme as-is
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")


def _build_ollama_base_url(host: str) -> str:
    """Resolve OLLAMA_HOST string to a full base URL (no trailing slash)."""
    if host.startswith(("http://", "https://")):
        return host.rstrip("/")
    return f"http://{host}"


OLLAMA_BASE_URL = _build_ollama_base_url(OLLAMA_HOST)
OLLAMA_API_URL = f"{OLLAMA_BASE_URL}/api/chat"

VISION_MODEL = "qwen2.5vl:7b"
TEXT_MODEL = "qwen2.5:32b-instruct"
TEXT_MODEL_FALLBACK = "qwen2.5:14b-instruct"
FALLBACK_MODEL = "qwen2.5vl:3b"  # If memory pressure
LEGACY_MODEL = "llava:13b"        # For comparison testing only

# Required Ollama version (MLX support added in 0.19)
MIN_OLLAMA_VERSION = "0.19.0"

# Env vars setup script must configure (via launchctl + ~/.zshrc)
REQUIRED_ENV_VARS = {
    "OLLAMA_MLX": "1",
    "OLLAMA_FLASH_ATTENTION": "1",
    "OLLAMA_NUM_PARALLEL": "4",
    "OLLAMA_KV_CACHE_TYPE": "q8_0",
    "OLLAMA_KEEP_ALIVE": "24h",
    "OLLAMA_MAX_LOADED_MODELS": "1",
    "OLLAMA_CONTEXT_LENGTH": "2560",
    "OLLAMA_HOST": OLLAMA_HOST,
}

# AI image input size (qwen2.5-vl native)
AI_IMAGE_SIZE = (672, 672)

# Per-AI-call timeouts
AI_CALL_TIMEOUT_SEC = 30
AI_CALL_RETRY_COUNT = 3
AI_CALL_RETRY_DELAYS = [1, 3, 5]  # Seconds between retries

# Parallel scoring (matches OLLAMA_NUM_PARALLEL).
# Override via AMG_AI_PARALLEL_WORKERS env var for empirical scaling tests.
# Must match OLLAMA_NUM_PARALLEL on the Ollama server to avoid request queuing.
AI_PARALLEL_WORKERS = int(os.environ.get("AMG_AI_PARALLEL_WORKERS", "6"))

# Service-level failure threshold
AI_SERVICE_FAIL_THRESHOLD = 3  # Consecutive failures before pausing batch

# v11.1.2: deterministic scoring. temperature=0 + fixed seed in score_frame() means
# two runs of the same scene with the same prompt produce identical scores. Required
# for A/B testing prompt or scoring changes — without this, Ollama's RNG is a confound.
# Any integer works; 42 is the conventional debugging seed.
AI_SCORING_SEED = 42

# ============================================================
# VIDEO PROCESSING
# ============================================================
# Frame analysis (sharpness, motion, faces) at this size
ANALYSIS_FRAME_SIZE = (640, 360)

# GPU-first CV path (phase migration flags).
# Off by default for conservative compatibility; cloud env can force on.
GPU_CV_ENABLED = _env_bool("AMG_GPU_CV_ENABLED", False)
# opencv_cuda | cupy (cupy is optional and only used when installed)
GPU_CV_BACKEND = os.environ.get("AMG_GPU_CV_BACKEND", "opencv_cuda").strip().lower()
GPU_DEDUP_ENABLED = _env_bool("AMG_GPU_DEDUP_ENABLED", GPU_CV_ENABLED)

# Calibration sample count (for adaptive Tier 1 threshold)
CALIBRATION_SAMPLE_COUNT = _profile_int(
    "AMG_CALIBRATION_SAMPLE_COUNT",
    100,
    balanced=64,
    fast=24,
    turbo=16,
)
# Optional cap on how far into the scene calibration probes frames.
# 0 means "use full scene duration".
CALIBRATION_MAX_DURATION_SEC = _profile_float(
    "AMG_CALIBRATION_MAX_DURATION_SEC",
    0.0,
    balanced=1200.0,
    fast=600.0,
    turbo=300.0,
)

# Tier 1 floor = this percentile of calibration samples
TIER_1_PERCENTILE = 75
TIER_2_PERCENTILE = 50
TIER_3_PERCENTILE = 25

# Sharpness floor (used as absolute minimum even after percentile)
SHARPNESS_HARD_FLOOR = 100

# Motion thresholds
MOTION_CAP_TIER_1 = 2.0
MOTION_CAP_TIER_2 = 3.0
MOTION_CAP_TIER_3 = 4.5

# Frame extraction intervals (frames per second to sample)
TIER_1_INTERVAL = 1.0   # 1 fps
TIER_2_INTERVAL = 0.5   # 1 every 2 sec
TIER_3_INTERVAL = 0.25  # 1 every 4 sec
TIER_1_INTERVAL_MAX = 3.0
TIER_2_INTERVAL_MAX = 1.5
TIER_3_INTERVAL_MAX = 1.0

# Scanner shape and guardrails to prevent long-tail scans from monopolizing runtime.
TIER_SCAN_MODE = os.environ.get(
    "AMG_TIER_SCAN_MODE",
    _profile_default("classic", balanced="single_pass", fast="single_pass", turbo="single_pass"),
).strip().lower()
if TIER_SCAN_MODE not in {"classic", "single_pass"}:
    TIER_SCAN_MODE = "classic"

# Set to <=0 via env to disable a specific cap.
TIER_SCAN_MAX_EXTRACTED_FRAMES_PER_TIER = _profile_int(
    "AMG_TIER_SCAN_MAX_EXTRACTED_FRAMES_PER_TIER",
    220,
    balanced=180,
    fast=120,
    turbo=80,
)
TIER_SCAN_MAX_AI_FRAMES_PER_TIER = _profile_int(
    "AMG_TIER_SCAN_MAX_AI_FRAMES_PER_TIER",
    80,
    balanced=60,
    fast=36,
    turbo=24,
)
TIER_SCAN_MAX_WALL_SEC_PER_TIER = _profile_float(
    "AMG_TIER_SCAN_MAX_WALL_SEC_PER_TIER",
    420.0,
    balanced=240.0,
    fast=150.0,
    turbo=90.0,
)
SINGLE_PASS_SCAN_INTERVAL_SEC = _profile_float(
    "AMG_SINGLE_PASS_SCAN_INTERVAL_SEC",
    1.0,
    balanced=1.0,
    fast=1.5,
    turbo=2.0,
)
SINGLE_PASS_MAX_AI_FRAMES = _profile_int(
    "AMG_SINGLE_PASS_MAX_AI_FRAMES",
    TIER_SCAN_MAX_AI_FRAMES_PER_TIER,
    balanced=60,
    fast=36,
    turbo=24,
)
SINGLE_PASS_MIN_GAP_SEC = _profile_float(
    "AMG_SINGLE_PASS_MIN_GAP_SEC",
    20.0,
    balanced=20.0,
    fast=25.0,
    turbo=30.0,
)

# ---------- Streaming scan (v11.4+) ----------
# When AMG_STREAMING_SCAN=1 the pipeline replaces the tier_scan +
# finish/buildup/cluster/floor_enforcement chain with a single producer/
# consumer scan that decodes the video once, runs CV gates as a sieve,
# keeps the GPU saturated through a continuous AI dispatcher, and selects
# top-K via a fused score (AI + sharpness + zone bonuses) with a post-AI
# sharpness gate. Designed to fix the H100-idle problem documented in the
# 2026-05-08 audit (only 15 AI calls in 24 min on Y&B_003 because every
# phase decoded the whole video and gated 99% of frames out before
# scoring).
# Phase 4 cut-over (2026-05-08): the cloud edition runs the
# ``balanced`` profile by default and the streaming scan is the new
# happy path. ``classic`` is the only profile that opts out by
# default; it's there as a safety hatch if the streaming path ever
# misbehaves on a specific scene. ``AMG_STREAMING_SCAN=0`` forces the
# legacy tier_scan + hunters + cluster + fallback chain regardless of
# profile.
STREAMING_SCAN_ENABLED = (
    str(os.environ.get(
        "AMG_STREAMING_SCAN",
        _profile_default("0", balanced="1", fast="1", turbo="1"),
    )).strip().lower()
    in {"1", "true", "yes", "on"}
)

# Sample interval for the streaming sieve (frames per N seconds). Lower =
# more candidates fed to GPU. 1.0 sec → ~1800 frames for a 30-min scene.
STREAMING_SCAN_INTERVAL_SEC = _profile_float(
    "AMG_STREAMING_SCAN_INTERVAL_SEC",
    1.0,
    balanced=1.0,
    fast=1.5,
    turbo=2.0,
)

# Cap on candidates queued for AI scoring (after CV sieve + dedup). The
# dispatcher always pulls the highest-priority candidate from this queue
# next, so dropping the lowest-priority overflow keeps memory bounded
# without losing the best frames.
STREAMING_SCAN_MAX_QUEUED = _profile_int(
    "AMG_STREAMING_SCAN_MAX_QUEUED",
    400,
    balanced=300,
    fast=200,
    turbo=150,
)

# Cap on total AI calls per scene to bound GPU spend even when the
# producer can supply more. 0 = no cap (scan until deadline).
STREAMING_SCAN_MAX_AI_CALLS = _profile_int(
    "AMG_STREAMING_SCAN_MAX_AI_CALLS",
    0,
    balanced=0,
    fast=160,
    turbo=80,
)

# In-memory frame cache budget. Streaming scan caches full-res frames for
# CV survivors so the output phase doesn't re-decode them. 3 GB is fine
# for a Runpod H100 SXM (≥80 GB system RAM); local CLI runs may want to
# lower this via env.
STREAMING_FRAME_CACHE_MAX_MB = int(
    os.environ.get("AMG_STREAMING_FRAME_CACHE_MAX_MB", "3072")
)

# Fused-score weights used by the LiveSelector. Sharpness is normalised
# to 0..100 (sharp/10) before weighting so the two terms are comparable
# with the 0..100 AI score.
STREAMING_FUSED_AI_WEIGHT = float(
    os.environ.get("AMG_STREAMING_FUSED_AI_WEIGHT", "0.7")
)
STREAMING_FUSED_SHARP_WEIGHT = float(
    os.environ.get("AMG_STREAMING_FUSED_SHARP_WEIGHT", "0.3")
)
STREAMING_ZONE_BONUS_FINISH = float(
    os.environ.get("AMG_STREAMING_ZONE_BONUS_FINISH", "5.0")
)
STREAMING_ZONE_BONUS_BUILDUP = float(
    os.environ.get("AMG_STREAMING_ZONE_BONUS_BUILDUP", "3.0")
)

# Post-AI sharpness gate: any picked candidate must clear this percentile
# of all scored frames' sharpness. Catches "AI said good but it's
# blurry" — the FALLBACK-C failure mode the 2026-05-08 audit surfaced.
# Set to 0 to disable.
STREAMING_POST_AI_SHARP_PERCENTILE = float(
    os.environ.get("AMG_STREAMING_POST_AI_SHARP_PERCENTILE", "25.0")
)

# Low-resolution streaming analysis. When enabled, the streaming scanner asks
# the video backend for analysis-sized frames and re-extracts full-resolution
# frames only for the final cover picks. On ffmpeg-cuda this avoids piping
# full-size raw BGR frames through Python for every sampled timestamp.
STREAMING_LOW_RES_ANALYSIS_ENABLED = _profile_bool(
    "AMG_STREAMING_LOW_RES_ANALYSIS",
    False,
    fast=True,
    turbo=True,
)
STREAMING_ANALYSIS_MAX_WIDTH = _profile_int(
    "AMG_STREAMING_ANALYSIS_MAX_WIDTH",
    672,
)
STREAMING_ANALYSIS_MAX_HEIGHT = _profile_int(
    "AMG_STREAMING_ANALYSIS_MAX_HEIGHT",
    672,
)

# Optional segment-parallel streaming scan. Keep default at 1 so the current
# production path stays unchanged unless the H100 bake-off explicitly opts in.
STREAMING_SEGMENT_COUNT = _profile_int(
    "AMG_STREAMING_SEGMENT_COUNT",
    1,
)
STREAMING_SEGMENT_MIN_DURATION_SEC = _profile_float(
    "AMG_STREAMING_SEGMENT_MIN_DURATION_SEC",
    1800.0,
)

# Duration-adaptive sampling scale (efficiency for long-form scenes).
# Tuple format: (min_duration_sec, max_duration_sec, interval_scale)
INTERVAL_SCALE_BY_DURATION = [
    (0, 1800, 1.0),               # <30m
    (1800, 3900, 1.8),            # 30m-65m
    (3900, float("inf"), 3.0),    # 65m+
]

# Segment counts and durations
TIER_1_SEGMENTS = 3
TIER_1_SEGMENT_DURATION_SEC = 300  # 5 min each

# ============================================================
# DEDUPLICATION (Perceptual Hashing)
# ============================================================
DEDUP_HASH_SIZE = 8           # 64-bit hash
DEDUP_HAMMING_THRESHOLD = 5   # Industry standard for "near duplicate"

# ============================================================
# TIER A/B/C SCORING (0–100 scale, v11.1.5+)
# ============================================================
# Score thresholds (same semantics as former 8/7/5 on 0–10 scale ×10)
SCORE_TIER_1_SUCCESS_FLOOR = 80.0   # Tier 1 needs 3+ frames at this
SCORE_TIER_2_SUCCESS_FLOOR = 70.0
SCORE_TIER_3_SUCCESS_FLOOR = 50.0

# Minimum candidates per tier to declare success
MIN_CANDIDATES_PER_TIER = 3

# Score cap (vision model output SCORE line, 0–100)
SCORE_MAX = 100.0

# Fallback A: rescue band from already-scored frames (was 3.0–5.0 on 0–10)
SCORE_FALLBACK_A_LOW = 30.0
SCORE_FALLBACK_A_HIGH = 50.0

# ============================================================
# CLUSTER EXPANSION (Score-Triggered)
# ============================================================
# Format: (min_score, max_score, window_seconds, interval_seconds)
CLUSTER_WINDOWS = [
    (50.0, 79.99, 3, 1),       # ±3s at 1s intervals = 6 samples
    (80.0, 89.99, 7, 1),       # ±7s at 1s intervals = 14 samples
    (90.0, 99.99, 20, 3),      # ±20s at 3s intervals = 14 samples
    (100.0, 100.0, 40, 5),     # ±40s at 5s intervals = 16 samples
]

# v11.1.4: cap on AI scoring count for cluster phase. Mirrors the
# BUILDUP_HUNTER_TOP_N pattern. Without this, fast decode (v11.1.3 PyAV)
# lets cluster balloon — scene 10 produced 145 post-gate candidates that
# would have taken ~15 min to AI-score. Default 30 keeps cluster
# contribution roughly in line with finish (8) + buildup (10) + tier_2 (17).
CLUSTER_HUNTER_TOP_N = _profile_int(
    "AMG_CLUSTER_HUNTER_TOP_N",
    30,
    balanced=18,
    fast=0,
    turbo=0,
)

# ============================================================
# FLOOR ENFORCEMENT (Cover Count)
# ============================================================
COVER_FLOOR = 15  # Always deliver at least this many

# Adaptive max based on duration
COVER_CAPS = [
    (0,    300,    20),   # < 5 min → cap at 20
    (300,  1500,   25),   # 5-25 min → cap at 25
    (1500, 2700,   28),   # 25-45 min → cap at 28
    (2700, float("inf"), 30),  # > 45 min → cap at 30
]

# Quota-fill defaults (v11.1.5+). Positions are part of the final cover set.
QUOTA_POSTERPOSE_TARGET = 3
QUOTA_BUILDUP_TARGET = 3
QUOTA_FINISH_TARGET = 3
QUOTA_POSITION_PER_LABEL_TARGET = 3
QUOTA_POSITION_MAX_LABELS = 4
QUOTA_MIN_GAP_SEC = 20.0

# Position classifier: classify only top candidates to avoid extra AI load.
POSITION_CLASSIFIER_MAX_CANDIDATES = _profile_int(
    "AMG_POSITION_CLASSIFIER_MAX_CANDIDATES",
    40,
    balanced=24,
    fast=0,
    turbo=0,
)
POSITION_CLASSIFIER_MIN_SCORE = 60.0
POSITION_CLASSIFIER_MIN_PEN_CONF = 0.60
POSITION_CLASSIFIER_MIN_LABEL_CONF = 0.60
POSITION_CLASSIFIER_CONTEXT_WINDOW_SEC = 45.0
POSITION_LABELS = [
    "MISSIONARY",
    "COWGIRL",
    "REVERSE_COWGIRL",
    "DOGGY",
    "ORAL_BJ",
    "ORAL_CUNN",
    "SIDE",
    "HANDJOB",
    "TOY",
    "GROUP",
    "OTHER",
]

# Fallback thresholds
ZERO_RATE_FALLBACK_C_TRIGGER = 0.6
ZERO_RATE_FALLBACK_D_TRIGGER = 0.8

# Fallback C: simplified scoring on N random frames
FALLBACK_C_SAMPLE_COUNT = 30

# Fallback D: pure CV, top N by sharpness
FALLBACK_D_TOP_N = 20

# ============================================================
# TIME BUDGET (Per-Scene Safety)
# ============================================================
TIME_BUDGET_SOFT_PCT = 0.75   # 25% of video duration → warning
TIME_BUDGET_HARD_PCT = 1.50   # 50% of video duration → abort
TIME_BUDGET_ABSOLUTE_MAX = 3600  # 20 minutes absolute cap (seconds)

# Per-phase time budgets (% of total scene budget)
PHASE_BUDGET_PCT = {
    "calibration": 0.05,
    "tier_1": 0.20,
    "tier_2": 0.25,
    "tier_3": 0.25,
    "finish_hunter": 0.15,
    "buildup_hunter": 0.15,
    "cluster": 0.15,
    "fallback": 0.25,
    "output": 0.05,
}

# Per-phase absolute hard timeouts (seconds)
PHASE_HARD_TIMEOUT_SEC = {
    "calibration": 60,
    "tier_1": 240,
    "tier_2": 300,
    "tier_3": 300,
    "finish_hunter": 180,
    "buildup_hunter": 180,
    "cluster": 180,
    "fallback": 300,
    "output": 60,
}

# ============================================================
# FINISH HUNTER (Last 20% of Video)
# ============================================================
FINISH_HUNTER_ZONE_START_PCT = 0.80  # Last 20%
FINISH_HUNTER_TOP_N = _profile_int(
    "AMG_FINISH_HUNTER_TOP_N",
    8,
    balanced=6,
    fast=5,
    turbo=0,
)              # Score top candidates
FINISH_HUNTER_INTERVAL_BASE = 1.0
FINISH_HUNTER_INTERVAL_MAX = 3.0

# ============================================================
# BUILDUP HUNTER (25-50% of Video)
# ============================================================
BUILDUP_HUNTER_ZONE_START_PCT = 0.25
BUILDUP_HUNTER_ZONE_END_PCT = 0.50
# v11.1.2: bumped from 6 → 10. The cap was never the bottleneck — dedup was —
# but once dedup is loosened (below) we want enough headroom to score the variety
# we now get out. Adds ~30-40s to scene wall time at the parallelism we're seeing.
BUILDUP_HUNTER_TOP_N = _profile_int(
    "AMG_BUILDUP_HUNTER_TOP_N",
    10,
    balanced=6,
    fast=4,
    turbo=0,
)
BUILDUP_HUNTER_INTERVAL_BASE = 2.0
BUILDUP_HUNTER_INTERVAL_MAX = 6.0
# v11.1.2: stricter dedup just for buildup. Default DEDUP_HAMMING_THRESHOLD=5 is
# too liberal for slow zones (kissing, undressing, oral) where many frames share
# composition but differ in small details — it was collapsing 55 candidates to 2
# on scene 8 today. Threshold=2 means hashes must differ by ≤2 bits (out of 64)
# to be considered duplicates; small but real visual changes now pass through.
BUILDUP_DEDUP_HAMMING_THRESHOLD = 2

# ============================================================
# OUTPUT
# ============================================================
COVER_FORMAT = "JPEG"
COVER_QUALITY = 92  # JPEG quality
ENHANCE_DEFAULT = True

# If a selected frame is slightly blurry but otherwise high-value, sample a few
# nearby timestamps and keep the sharpest close match. This improves "almost
# perfect" picks without re-scoring the whole scene.
COVER_NEARBY_POLISH_ENABLED = _profile_bool(
    "AMG_COVER_NEARBY_POLISH_ENABLED",
    True,
    fast=False,
    turbo=False,
)
COVER_NEARBY_POLISH_MIN_SCORE = 75.0
COVER_NEARBY_POLISH_OFFSETS_SEC = (-0.30, -0.15, 0.15, 0.30)
COVER_NEARBY_POLISH_MIN_SHARPNESS_GAIN = 35.0
COVER_NEARBY_POLISH_MIN_SHARPNESS_GAIN_PCT = 0.12

# Enhancement values (subtle)
ENHANCE_SATURATION = 1.10  # +10%
ENHANCE_CONTRAST = 1.05    # +5%
ENHANCE_SHARPNESS = 1.15   # +15%
ENHANCE_BRIGHTNESS_THRESHOLD = 0.35  # If avg brightness below this...
ENHANCE_BRIGHTNESS_BOOST = 1.10      # ...boost brightness by 10%

# Contact sheet layout
CONTACT_SHEET_THUMB_SIZE = (640, 360)
CONTACT_SHEET_COLS = 3
CONTACT_SHEET_MARGIN = 15
CONTACT_SHEET_LABEL_HEIGHT = 28
CONTACT_SHEET_HEADER_HEIGHT = 110
CONTACT_SHEET_BG_COLOR = (15, 15, 15)
CONTACT_SHEET_QUALITY = 92

# Provided thumbnail grading/import
# Some creators/agencies include cover candidates in the submission folder.
# We score these with the same local vision model and import only the good ones.
PROVIDED_THUMB_MAX_SCAN = _profile_int(
    "AMG_PROVIDED_THUMB_MAX_SCAN",
    40,
    balanced=24,
    fast=0,
    turbo=0,
)
PROVIDED_THUMB_MAX_ACCEPT = _profile_int(
    "AMG_PROVIDED_THUMB_MAX_ACCEPT",
    4,
    fast=0,
    turbo=0,
)
PROVIDED_THUMB_MIN_SCORE = 80.0

# Filename schema
FILENAME_SCHEMA_PATTERN = "{rank:02d}_{performer}_{code}_{type}_{gaze}_{score:.1f}_{mins}m{secs:02d}s"

# ============================================================
# DATA RETENTION
# ============================================================
WORK_DIR_RETENTION_DAYS = 30
DECISION_LOG_RETENTION_DAYS = 365
RUN_LOG_RETENTION_DAYS = 90
STRUCTURED_LOG_RETENTION_DAYS = 90
BACKUP_RETENTION_DAYS = 7

# ============================================================
# DISK SPACE GUARDRAIL
# ============================================================
MIN_FREE_SPACE_GB = 10  # Pause batch if less than this

# ============================================================
# ERROR CODES
# ============================================================
ERROR_CODES = {
    # Time budget
    "E_TIMEOUT_SOFT": "Processing exceeded soft budget (25% of video)",
    "E_TIMEOUT_HARD": "Processing exceeded hard budget (50% of video)",
    "E_TIMEOUT_ABSOLUTE": "Processing exceeded absolute cap (20 minutes)",

    # Source
    "E_SOURCE_UNREADABLE": "Cannot read video file",
    "E_SOURCE_CORRUPT": "Decoder errors during read",
    "E_SOURCE_TOO_SHORT": "Video less than 60 seconds",
    "E_SOURCE_NO_AUDIO": "No audio track (some platforms require)",

    # Calibration
    "E_CALIB_NO_VARIATION": "Sharpness uniform across video (likely solid color)",
    "E_CALIB_ALL_BLURRY": "Even 75th percentile fails sharpness floor",

    # AI/Ollama
    "E_AI_UNAVAILABLE": "Ollama service not responding",
    "E_AI_MODEL_NOT_LOADED": "Required vision model not installed",
    "E_AI_TIMEOUT": "AI call exceeded timeout, retries exhausted",
    "E_AI_PARSE_FAIL": "AI returned unparseable response",

    # Floor enforcement
    "E_FLOOR_FALLBACK_D": "Pure-CV rescue used (lower quality covers possible)",
    "E_FLOOR_NOT_MET": "Even Fallback D could not deliver 10 covers",

    # Compliance
    "E_COMPLIANCE_NO_2257": "Missing 2257 documentation",
    "E_COMPLIANCE_BANNED": "Scene title contains banned terms",
}

# ============================================================
# COMPLIANCE
# ============================================================
REQUIRE_2257_DOC = False  # v11.1: disabled per Mario, 2257s tracked manually for now  # Refuse processing if no 2257 found
COMPLIANCE_2257_FILENAMES = ["2257.pdf", "2257.jpg", "2257.png", "id.pdf"]

# ============================================================
# LOGGING
# ============================================================
LOG_LEVEL = "INFO"  # DEBUG / INFO / WARN / ERROR / FATAL
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ============================================================
# OPERATOR IDENTITY
# ============================================================
DEFAULT_OPERATOR = os.environ.get("USER", "unknown")
DEFAULT_MACHINE_ID = os.uname().nodename if hasattr(os, 'uname') else "unknown"


def load_global_overrides():
    """
    Load ~/AMG_OS/config.yaml if it exists, override defaults.
    Returns a dict of overrides (empty if file doesn't exist).
    """
    if not GLOBAL_CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        with open(GLOBAL_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def get_cover_cap(duration_sec):
    """Return adaptive cover cap based on video duration."""
    for low, high, cap in COVER_CAPS:
        if low <= duration_sec < high:
            return cap
    return COVER_FLOOR  # Fallback


def get_cluster_window(score):
    """Return (window_seconds, interval_seconds) for given score."""
    for min_s, max_s, window, interval in CLUSTER_WINDOWS:
        if min_s <= score <= max_s:
            return window, interval
    # Score below tier-3 floor = no cluster expansion
    return 0, 0


def get_interval_scale(duration_sec):
    """Return interval scaling factor based on video duration."""
    try:
        d = float(duration_sec or 0)
    except (TypeError, ValueError):
        d = 0.0
    for low, high, scale in INTERVAL_SCALE_BY_DURATION:
        if low <= d < high:
            return float(scale)
    return 1.0


def get_adaptive_interval(base_interval, duration_sec, max_interval=None):
    """
    Scale a sampling interval by duration, then clamp to an optional max.

    Base interval is treated as the minimum to avoid oversampling.
    """
    try:
        base = float(base_interval)
    except (TypeError, ValueError):
        base = 1.0
    scaled = base * get_interval_scale(duration_sec)
    if max_interval is not None:
        try:
            cap = float(max_interval)
            if cap > 0:
                scaled = min(scaled, cap)
        except (TypeError, ValueError):
            pass
    return round(max(scaled, base), 3)

# ============================================================
# v11.1 ADDITIONS
# ============================================================

# Per-platform requirements (defaults — override in config.yaml)
PLATFORM_REQUIREMENTS = {
    "AEBN": {
        "title_max_chars": 60,
        "requires_2257": True,
        "requires_individual_releases": True,
        "banned_terms": [],  # Operator-populated as encountered
        "preferred_resolution_min": (1280, 720),
        "metadata": {
            "title_min_chars": 30,
            "description_min_chars": 140,
            "description_max_chars": 500,
            "min_categories": 8,
            "max_categories": 15,
            "min_tags": 15,
            "max_tags": 30,
        },
    },
    "SLR": {
        "title_max_chars": 80,
        "requires_2257": True,
        "requires_individual_releases": False,  # Studio release sufficient
        "banned_terms": [],
        "preferred_resolution_min": (1920, 1080),
        "vr_supported": True,
        "metadata": {
            "title_min_chars": 30,
            "description_min_chars": 120,
            "description_max_chars": 500,
            "min_categories": 8,
            "max_categories": 15,
            "min_tags": 15,
            "max_tags": 30,
        },
    },
    "ADE": {
        "title_max_chars": 100,
        "requires_2257": True,
        "requires_individual_releases": True,
        "banned_terms": [],
        "preferred_resolution_min": (1280, 720),
        "metadata": {
            "title_min_chars": 30,
            "description_min_chars": 140,
            "description_max_chars": 500,
            "min_categories": 8,
            "max_categories": 15,
            "min_tags": 15,
            "max_tags": 30,
        },
    },
}

# Title generation
TITLE_SUGGESTIONS_PER_SCENE = 5  # AI generates this many candidates
TITLE_AI_TIMEOUT_SEC = 15
TITLE_TONE_DEFAULT = "edgy"  # retail_safe | edgy | premium_story | creative
TEXT_GEN_TIMEOUT_SEC = 25
TEXT_GEN_TEMPERATURE = 0.7

# Title style patterns (v11.1 quick patterns; v11.2 will replace with research-driven)
TITLE_STYLE_PATTERNS = [
    "performer_led",      # "Yasmina's Wild Night"
    "narrative_hook",     # "Two Couples, One Bedroom"
    "scene_descriptive",  # "Gangbang Foursome with Yasmina Khan"
    "studio_branded",     # "The Yasmina Brady Experience"
    "numbered_series",    # "Couples Weekend Vol. 7"
]

# v11.2 hook: path to research-driven title patterns
TITLE_RESEARCH_PATTERNS_PATH = DATA_DIR / "title_patterns" / "research_patterns.json"
TITLE_QUICK_PATTERNS_PATH = DATA_DIR / "title_patterns" / "quick_patterns.json"

# Performer document registry
PERFORMER_DOCS_DIR = DATA_DIR / "performer_documents"

# Review and distribution tracking
REVIEWED_DIR = DATA_DIR / "reviewed"
DISTRIBUTION_STATUS_DIR = DATA_DIR / "distribution_status"
OPERATOR_FEEDBACK_DIR = DATA_DIR / "operator_feedback"
OPERATOR_FEEDBACK_PATH = OPERATOR_FEEDBACK_DIR / "feedback.jsonl"

# Batch summaries
BATCH_SUMMARIES_DIR = DATA_DIR / "batch_summaries"

# Title corpus (v11.2 hook)
TITLE_CORPUS_DIR = DATA_DIR / "title_corpus"

# Optional "soft" (non-nude) thumbnail extraction for studios/platforms that
# require safe cover art.
ENABLE_FINISH_HUNTER = _profile_bool(
    "AMG_ENABLE_FINISH_HUNTER",
    True,
    turbo=False,
)
ENABLE_BUILDUP_HUNTER = _profile_bool(
    "AMG_ENABLE_BUILDUP_HUNTER",
    True,
    turbo=False,
)
ENABLE_CLUSTER_EXPANSION = _profile_bool(
    "AMG_ENABLE_CLUSTER_EXPANSION",
    True,
    fast=False,
    turbo=False,
)
ENABLE_POSITION_CLASSIFIER = _profile_bool(
    "AMG_ENABLE_POSITION_CLASSIFIER",
    True,
    fast=False,
    turbo=False,
)
ENABLE_SCENE_INSIGHT = _profile_bool(
    "AMG_ENABLE_SCENE_INSIGHT",
    True,
    turbo=False,
)
ENABLE_PROVIDED_THUMBNAIL_SCORING = _profile_bool(
    "AMG_ENABLE_PROVIDED_THUMBNAIL_SCORING",
    True,
    fast=False,
    turbo=False,
)

SOFT_THUMB_ENABLED = _profile_bool(
    "AMG_SOFT_THUMB_ENABLED",
    True,
    fast=False,
    turbo=False,
)
SOFT_THUMB_SAMPLE_COUNT = _profile_int(
    "AMG_SOFT_THUMB_SAMPLE_COUNT",
    24,
    balanced=18,
    fast=0,
    turbo=0,
)
SOFT_THUMB_MIN_SCORE = 72.0
SOFT_THUMB_FILENAME = "00_soft_thumbnail.jpg"

# DVD compilation
DVD_OUTPUT_DIR_NAME = "DVD_Output"
DVD_QUAD_LAYOUT_SIZE = (1600, 1200)  # Case art canvas

# Eye contact scoring — v11.1.1 collapsed to uniform +2.0 to stop DUAL hallucination.
# v11.1 had B1b/B1c at +3.5/+4.5; qwen2.5vl over-reported DUAL because the bonus
# differential (and capitalized "RARE / EXCEPTIONALLY VALUABLE" prompt language) gave
# it a strong incentive to mark dual when in doubt. The GAZE label is preserved in
# output for descriptive use; only the score reward is flattened.
SCORE_B1_EYE_CONTACT = 20.0      # Doc-only: matches prompt B1 weight on 0–100 scale
# Legacy aliases — kept for backwards compatibility with anything that imported them.
# Do not use for new code; prefer SCORE_B1_EYE_CONTACT.
SCORE_B1A_SINGLE_GAZE = SCORE_B1_EYE_CONTACT
SCORE_B1B_DUAL_GAZE = SCORE_B1_EYE_CONTACT
SCORE_B1B_TRIPLE_PLUS_GAZE = SCORE_B1_EYE_CONTACT

# All-male code handling
FLAG_ALL_MALE_CODES = True  # Process but flag for review (don't reject)

# Performance tracking
TRACK_BATCH_PERFORMANCE = True
PERFORMANCE_TREND_BASELINE_BATCHES = 5  # Compare against last N batches for slowdown detection
