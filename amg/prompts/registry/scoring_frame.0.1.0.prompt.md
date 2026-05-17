Score this adult VOD frame for use as a thumbnail/cover image.

{scene_context}
DETECTED GENRES: {genres_str}{genre_section}{studio_section}

═══════════════════════════════════════════════════════════════
TIER A — DEAL BREAKERS (binary checks, ANY failure → SCORE: 0)
═══════════════════════════════════════════════════════════════

DB1: Female performer must be CLEARLY visible in frame
DB2: Female must show bare breasts, buttocks, or vagina (clothed = fail)
DB3: Frame sharp enough to read at thumbnail size (severe blur = fail)
DB4: If REAR shot composition: must show ass/butt clearly (not just back)

If ANY Tier A fails, output ONLY:
TIER_A_FAIL: <code>
SCORE: 0
END

═══════════════════════════════════════════════════════════════
TIER B — STRONG SIGNALS (integer points, only if unambiguous)
═══════════════════════════════════════════════════════════════

B1:  Performer eye contact with camera (any count):     +10
B2:  Eye whites visible (open, any direction):          +6
B3:  Visible penetration matching scene type/genre:     +16
B4:  Multiple penises visible (group scenes only):      +10
B5:  Female centered or dominant in composition:         +6
B6:  Genuine pleasure expression on female face:        +10
B7:  Money shot (visible facial/creampie/squirt):        +16
B8:  Body fully nude and dominant in frame:              +7
B9:  Oral close-up (mouth contact + readable face):     +12
B10: Dual gaze at lens (exactly 2 performers):          +10
B11: Aggressive/intense action beat is clearly visible: +8
B12: Climax cue (release, anticipation, creampie setup):+12
B13: Bodily fluid prominently visible (spit/cum/etc):   +14

B1 applies once per frame regardless of how many performers are looking
at the camera. The GAZE field below records the count for descriptive
purposes only — do not stack the B1 points more than once.

═══════════════════════════════════════════════════════════════
TIER C — AESTHETIC SIGNALS (separate the cinematic from the competent)
═══════════════════════════════════════════════════════════════

C1: Lighting/aesthetic looks professional (not flat/flash): +5
C2: Strong contrast/colors (pops as thumbnail):             +5
C3: Background not distracting (composition reads cleanly): +3
C4: Face-forward close-up framing reads clean at thumb size:+4
C5: Subject/action readability survives heavy downscale:    +4

Tier C is what separates a competent action frame (which most
candidates are) from a cinematic cover (which is what we want at the
top of the rank). Be strict — apply C1 only when lighting actively
flatters the subject, not just because the frame is exposed correctly.

═══════════════════════════════════════════════════════════════
TIER D — PENALTIES (subtract for degradations; include only when present)
═══════════════════════════════════════════════════════════════

D1: Mild blur or motion softness hurts readability:         -8
D2: Awkward crop (cut faces/body key points):               -6
D3: Face occlusion weakens cover utility:                   -7
D4: Distracting clutter/noise in frame:                     -5
D5: Ambiguous action read despite nudity:                   -6

═══════════════════════════════════════════════════════════════
SCORE FORMULA (0–100, TIER_A_PASS only)
═══════════════════════════════════════════════════════════════

RETAIL_BASE = 34 (every Tier-A-passing frame starts here — "meets minimum
B2B thumbnail hygiene" before bonuses).

SCORE = min(100, RETAIL_BASE + sum(Tier B + Tier C) - sum(Tier D penalties)).
The backend recomputes score from your listed codes, so code lists must match
what is truly visible. If nothing beyond base is clearly earned, SCORE should
sit near 34–45. Do not inflate.

Calibration (use the full span — avoid parking unrelated frames in the same band):
  34–48: weak / cluttered / flat — usable only as filler
  49–62: competent but ordinary
  63–76: clearly good retail thumbnail
  77–88: strong — would compete for hero placement
  89–96: exceptional — immediate hero artwork
  97–100: flawless — reserve for rare perfect composition + moment + light

If two frames differ only slightly in quality, their SCORE values must differ
by a few points, not sit on the same tenth. Penalize muddy focus, awkward crop,
and busy backgrounds with lower SCORE even when nudity is present.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT (REQUIRED)
═══════════════════════════════════════════════════════════════

If Tier A passes, respond EXACTLY in this format:

TIER_A_PASS: yes
TIER_B_PRESENT: <comma-separated B-codes that apply, e.g. B1,B3,B6>
TIER_C_PRESENT: <comma-separated C-codes>
TIER_D_PRESENT: <comma-separated D-codes, or NONE>
SCORE: <number 0.0-100.0, one decimal allowed>
TYPE: <NUDE/SEX_ACT/PENETRATION/BUILDUP/FINISH/COMPOSITION>
GAZE: <SINGLE/DUAL/TRIPLE/AVERTED/CLOSED/REAR>
AESTHETIC: <PROFESSIONAL/STANDARD/AMATEUR>
PENETRATION_VISIBLE: <yes/no>
PENETRATION_CONFIDENCE: <0.00-1.00>
ACTION_EVIDENCE: <EXPLICIT_PENETRATION|ORAL_CONTACT|POSE_NO_CONTACT|OCCLUDED|WATER_OCCLUSION|NONE>
END

GAZE field semantics (v11.1.1 — be conservative):
  SINGLE  = exactly one performer making eye contact with the lens
  DUAL    = exactly two performers, BOTH with eyes pointed at the lens
  TRIPLE  = three or more performers, ALL with eyes pointed at the lens
  AVERTED = no eye contact, eyes open
  CLOSED  = eyes closed
  REAR    = rear-shot composition

If you cannot clearly resolve every performer's eye direction in this
frame, default to SINGLE (if one is clearly looking) or AVERTED (if none
clearly are). Do not mark DUAL or TRIPLE as a guess. The B1 bonus is the
same regardless, so over-reporting helps no one.

Dual-gaze scoring policy:
  - B10 requires exactly two performers both looking at lens.
  - Do not award B10 for SINGLE or TRIPLE gaze.
  - POV with camera-as-male still qualifies if two performers are lens-aware.

Penetration truth rules (critical):
  - PENETRATION_VISIBLE=yes ONLY when explicit insertion/contact is clearly
    visible in-frame (not implied by pose).
  - If limbs/water/angle occlude the key area, use PENETRATION_VISIBLE=no,
    confidence <= 0.49, and ACTION_EVIDENCE=OCCLUDED or WATER_OCCLUSION.
  - TYPE must be PENETRATION only when PENETRATION_VISIBLE=yes.
  - In uncertain cases, prefer conservative outputs:
      TYPE=SEX_ACT or NUDE, PENETRATION_VISIBLE=no.

If lighting is flat, do not award C1. If composition is cluttered, do not award C3.
The score must differentiate frames, not normalize them toward the mid 80s.
