import json
from pathlib import Path


def test_preview_manifest_records_generated_outputs(monkeypatch, tmp_path):
    import amg.output.previews as previews

    video = tmp_path / "scene.mp4"
    video.write_bytes(b"video")

    def fake_run(cmd, *, timeout_sec):
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"preview")
        return True, ""

    monkeypatch.setattr(previews, "_resolve_ffmpeg", lambda _bin: "ffmpeg")
    monkeypatch.setattr(previews, "_run", fake_run)

    manifest = previews.generate_preview_outputs(
        video_path=video,
        work_dir=tmp_path / "work",
        analysis={
            "sections": [
                {"section_tag": "DOGGY", "thumbnail_timestamp": 30.0, "evidence_ids": ["ev_001"]},
                {"section_tag": "ORAL_BJ", "thumbnail_timestamp": 90.0, "evidence_ids": ["ev_002"]},
            ],
            "policy_flags": [],
            "thumbnail_moments": [],
        },
        clip_count=2,
        clip_duration_sec=5.0,
        gif_enabled=False,
    )

    assert len(manifest["outputs"]) == 2
    assert all(row["status"] == "ok" for row in manifest["outputs"])
    manifest_path = tmp_path / "work" / "previews" / "preview_manifest.json"
    assert manifest_path.exists()
    persisted = json.loads(manifest_path.read_text())
    assert persisted["outputs"][0]["duration_sec"] == 5.0
    assert Path(persisted["outputs"][0]["path"]).exists()


def test_preview_generation_is_nonfatal_when_ffmpeg_missing(monkeypatch, tmp_path):
    import amg.output.previews as previews

    monkeypatch.setattr(previews, "_resolve_ffmpeg", lambda _bin: None)
    manifest = previews.generate_preview_outputs(
        video_path=tmp_path / "missing.mp4",
        work_dir=tmp_path / "work",
        analysis={"sections": [{"thumbnail_timestamp": 30.0}]},
        clip_count=1,
        gif_enabled=False,
    )

    assert manifest["errors"][0]["error"] == "ffmpeg_not_found"
    assert (tmp_path / "work" / "previews" / "preview_manifest.json").exists()
