# AMG OS v11 — Learning System

How v11 captures data and how it will use it over time.

---

## Phase 1 (Active in v11.0): Data Capture

Every scene processed produces a decision log:
`~/AMG_OS/data/decision_logs/{scene_id}.json`

Captured fields:

```json
{
  "scene_id": "27 BBGG - couple swap",
  "version": "11.0.0",
  "timestamp_processed": "2026-05-04T14:23:00Z",
  "operator": "mario",
  "machine_id": "marios-macbook-pro",

  "input": {
    "duration_sec": 1845,
    "fps": 29.97,
    "resolution": "3840x2160",
    "codec": "hevc",
    "size_gb": 8.2,
    "studio": "YasminaBrady",
    "performer_code": "BBGG",
    "performer_count": 4,
    "scene_type": "FOURSOME",
    "genres": ["GROUP", "SWINGER"]
  },

  "execution": {
    "total_duration_sec": 110.4,
    "phases": {
      "calibration": {"duration_sec": 8.2},
      "tier_scan": {"duration_sec": 31.0, "tier_used": 2, "passing_count": 5},
      "finish_hunter": {"duration_sec": 22.1, "passing_count": 3},
      "buildup_hunter": {"duration_sec": 19.4, "passing_count": 2},
      "cluster": {"duration_sec": 14.8, "expansions": 3},
      "output": {"duration_sec": 14.9}
    },
    "calibration": {
      "tier_1_floor": 654.0,
      "tier_2_floor": 312.0,
      "tier_3_floor": 121.0
    },
    "error_codes": []
  },

  "outcomes": {
    "covers_delivered": 12,
    "covers_verified": 12,
    "scores_distribution": {
      "10": 0, "9-10": 2, "8-9": 4, "7-8": 4, "6-7": 2, "5-6": 0, "<5": 0
    },
    "top_pick_score": 9.5,
    "top_pick_type": "FINISH",
    "top_pick_timestamp_sec": 864,
    "fallbacks_used": []
  },

  "resource_usage": {
    "ai_calls_total": 47,
    "ai_calls_succeeded": 47,
    "ai_calls_retried": 2,
    "frames_extracted": 156
  },

  "human_feedback": {
    "operator_selected_cover": null,
    "operator_rejected_covers": [],
    "operator_notes": null,
    "platform_uploaded_to": null,
    "platform_performance_30d": null
  }
}
```

This data is the **corpus** that powers everything below.

---

## Phase 2 (Active in v11.0): Studio Calibration History

Every scene updates the studio profile's calibration history:

```json
"calibration_history": {
  "tier_1_floor_avg": 654,
  "tier_1_floor_p25": 480,
  "tier_1_floor_p75": 820,
  "scenes_processed": 47,
  "_recent_floors": [620, 650, 670, ...]
}
```

After 5+ scenes, run:
```bash
amg calibrate YasminaBrady
```

This recomputes the studio's optimal Tier 1 threshold from real history, replacing
the auto-detected per-scene calibration. Future scenes from that studio start
with smarter defaults.

---

## Phase 3 (Coming in v11.1): Per-Studio Prompt Adaptation

Once a studio has 50+ scenes processed, v11.1 will analyze patterns:

- Which Tier B signals (B1/B2/...B7) most often appear in high-scoring frames?
- Which AI fail codes (DB1/DB2/DB3/DB4) most commonly trigger?
- What scene types yield the highest average top-pick score?

The prompt sent to the AI for THAT studio will be adapted based on these patterns.

Example: if YasminaBrady scenes consistently score highest when B6 (genuine pleasure
expression) is present, the prompt for Yasmina scenes will explicitly instruct the
AI to prioritize that signal.

This is automatic — runs nightly via `amg calibrate --auto`.

---

## Phase 4 (Coming in v11.2+): Operator Feedback Loop

Today, the `human_feedback` block in decision logs is empty. Future versions
will populate it:

- **Cover selection capture**: when operator picks a final cover for a platform,
  v11.x records which one. After 100+ scenes, ML can learn what operators
  actually choose vs. what AI ranks #1.
- **Rejection reasons**: operator can flag a cover as "too dark" / "wrong gaze"
  / "performer obscured" — these become per-studio negative signals.
- **Platform performance**: tie covers to upload analytics. Covers that drove
  high CTR on AEBN get weighted up in future scoring.

Implementation requires AMG OS Phase 1 (Sheets integration) for capturing
upload events. Currently blocked on Amy's questionnaire response.

---

## Phase 5 (v12.0+, Speculative): Active Learning

If the Phase 4 feedback loop generates enough data (1000+ scenes with
operator selections + platform performance), we could fine-tune the AI prompt
or even train a small ranker model on top of the AI's raw scores.

This is far in the future. v11 captures the data so it's ready when we get there.

---

## What Lives Where

| File | Purpose | Auto-Delete? |
|------|---------|---------------|
| `data/decision_logs/{scene}.json` | Per-scene full record | Never |
| `data/studio_profiles/{studio}.json` | Per-studio config + history | Never |
| `data/performers/registry.json` | Performer ID + scene counts | Never |
| `data/audit_log.jsonl` | Compliance event trail | Never |
| `data/logs/structured/{date}.jsonl` | Daily structured event log | After 90 days |
| `data/logs/runs/{scene}_{ts}.log` | Per-run human-readable log | After 90 days |
| `data/backups/decision_logs_{date}/` | Daily backup snapshots | After 7 days |

The "Never" files are the learning corpus. The "After N days" files are
operational logs — useful short-term, not learning data.

---

## How to Inspect

```bash
# See last 5 decision logs
ls -lt ~/AMG_OS/data/decision_logs/ | head -5

# Read one
jq . ~/AMG_OS/data/decision_logs/{scene_id}.json

# Aggregate analysis
amg analyze --days 30
amg analyze --studio YasminaBrady --days 90

# Studio profile
jq . ~/AMG_OS/data/studio_profiles/YasminaBrady.json
```

---

## Privacy & Portability

Decision logs:
- Live entirely on operator's Mac (`~/AMG_OS/data/decision_logs/`)
- Are NEVER synced via Git (in `.gitignore`)
- Can be exported, backed up, or deleted by operator
- Contain no biometric data, no facial recognition, no network calls

If/when fleet aggregation is built (Phase 1+), it will be opt-in via explicit
Sheets/Drive sync — never automatic.
