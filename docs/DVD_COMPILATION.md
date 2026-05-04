# DVD Compilation — v11.1

`amg dvd-compile` packages multiple processed and reviewed scenes into a single DVD-length output, complete with chapter markers and quad-layout case art.

## Quick Path

```bash
# Find candidates
amg find --genre swinger --min-score 8.0

# Compile picks (typically 4 scenes, but 2-6 supported)
amg dvd-compile <scene_id_1> <scene_id_2> <scene_id_3> <scene_id_4> \
    --theme "Swinger Weekend" \
    --output ~/AMG_Processing/DVD_Output/
```

## What It Does

### Step 1: Validates spec compatibility
Checks all scenes share:
- FPS (within 0.1 tolerance)
- Resolution
- Codec (warning only — re-encode flagged but not blocked)
- Audio sample rate

If specs don't match, the tool warns but still produces metadata + case art. Concatenation only runs if specs are compatible (otherwise re-encode would be needed and v11.1 doesn't auto-re-encode).

### Step 2: Concatenates videos
Uses ffmpeg's concat demuxer (lossless `-c copy`):
```
output_dir/dvd_<timestamp>_<theme>.mp4
```
- All scenes joined seamlessly
- Total duration = sum of source durations
- Chapter markers preserved in metadata

### Step 3: Builds case art
Generates a 1600x1200 quad layout:
- 2x2 grid of hero covers from each scene
- Theme banner across the top in gold
- Saved as `dvd_<id>_case_art.jpg`

If you supply fewer than 4 scenes, empty quadrants are left blank — useful for promotional inserts.

### Step 4: Writes metadata
`dvd_<id>_metadata.json` contains:
```json
{
  "dvd_id": "dvd_20260504_194200_Swinger_Weekend",
  "theme": "Swinger Weekend",
  "scenes": [
    {
      "scene_id": "...",
      "title": "...",
      "studio": "...",
      "duration_sec": 1845,
      "performers": [...],
      "genres": [...],
      "chapter_start_sec": 0
    },
    ...
  ],
  "total_duration_sec": 7200,
  "spec_compatible": true,
  "chapters": [...]
}
```

This is the bundle you'd ship to platforms or burn to physical media.

## Why Post-Process (Not at Ingest)

DVD compilation is a **distribution decision**, not a processing decision. Reasons to wait:
- You don't always know at ingest time which scenes belong together
- Theme curation often spans multiple studios/performers
- Scenes from different shoots can be combined into themed DVDs
- Some scenes get reviewed and rejected for retail — those shouldn't end up on DVDs

The flow is: ingest individual scenes → process → review → THEN compile DVDs from your reviewed library.

## Manual Override for Case Art

If the auto-generated case art needs Photoshop polish:
1. Run `amg dvd-compile` to get the auto art
2. Open the output `dvd_<id>_case_art.jpg` in Photoshop
3. Replace covers, adjust banner, add taglines, etc.
4. Save back to the same path (or use it as a Smart Object source in your retail PSD)

The metadata JSON is unaffected — only the visual asset changes.

## Spec Issues

If `validate_dvd_specs` finds mismatches, you'll see:

```
⚠ Spec issues encountered:
    - Scene 2: FPS mismatch (29.97 vs 24.0)
    - Scene 3: Resolution mismatch (1920x1080 vs 3840x2160)
```

In this case the metadata + case art still produce, but the concat step is skipped. To fix:
- **FPS mismatch:** Re-encode to a common FPS (typically 30 or 24)
- **Resolution mismatch:** Resize all to the lowest common (typically 1920x1080)
- **Codec mismatch:** Re-encode to common codec (H.264 most compatible)

A future v11.2 update may add automatic re-encode when specs differ slightly.

## Limits

- **Minimum 2 scenes**, no hard maximum (4 is typical for a DVD)
- **Total runtime:** ffmpeg concat is fast (~1 minute per hour of source) but very long compilations may be unwieldy
- **File size:** lossless concat preserves source bitrates; a 4-scene DVD typically = sum of source sizes

## v11.2 Preview

- Auto re-encode when specs differ (configurable quality)
- Multiple output formats (DVD ISO, Blu-ray, MKV, individual MP4s)
- Smart chapter title generation from scene metadata
- Bumper/trailer insertion between scenes

## v12 Preview

- Theme suggestion: "These 4 scenes share `swinger + foursome + outdoor` — package them?"
- Performance feedback: track which DVD compilations get retail uptake; learn what themes work
