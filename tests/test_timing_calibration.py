from argparse import Namespace
from pathlib import Path


def _write_run_timings(path: Path) -> None:
    rows = [
        {
            "scene_id": "s1",
            "phase_durations_sec": {"tier_scan": 110.0, "cluster": 40.0, "output": 20.0},
        },
        {
            "scene_id": "s2",
            "phase_durations_sec": {"tier_scan": 160.0, "cluster": 60.0, "output": 30.0},
        },
        {
            "scene_id": "s3",
            "phase_durations_sec": {"tier_scan": 220.0, "cluster": 80.0, "output": 50.0},
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(__import__("json").dumps(r) for r in rows) + "\n")


def test_calibrate_phase_thresholds_reads_jsonl(tmp_path):
    from amg.learning.timing_calibration import calibrate_phase_thresholds

    ledger = tmp_path / "run_timings.jsonl"
    _write_run_timings(ledger)

    rows = calibrate_phase_thresholds(ledger, min_samples=2)
    by_phase = {r["phase"]: r for r in rows}
    assert "tier_scan" in by_phase
    assert by_phase["tier_scan"]["count"] == 3
    assert by_phase["tier_scan"]["green_max_sec"] > 0
    assert by_phase["tier_scan"]["yellow_max_sec"] >= by_phase["tier_scan"]["green_max_sec"]
    assert by_phase["tier_scan"]["red_over_sec"] >= by_phase["tier_scan"]["yellow_max_sec"]


def test_update_cheat_sheet_thresholds_replaces_marker_block(tmp_path):
    from amg.learning.timing_calibration import update_cheat_sheet_thresholds

    doc = tmp_path / "phase_timing_cheat_sheet.md"
    doc.write_text(
        "before\n"
        "<!-- AUTO_THRESHOLD_TABLE_START -->\n"
        "old\n"
        "<!-- AUTO_THRESHOLD_TABLE_END -->\n"
        "after\n"
    )
    ok = update_cheat_sheet_thresholds(
        doc_path=doc,
        rows=[{"phase": "tier_scan", "count": 5, "green_max_sec": 100.0, "yellow_max_sec": 150.0, "red_over_sec": 200.0, "max_sec": 250.0}],
        run_timings_path=tmp_path / "run_timings.jsonl",
        min_samples=3,
    )
    assert ok is True
    text = doc.read_text()
    assert "tier_scan" in text
    assert "\nold\n" not in text


def test_cmd_timing_calibrate_updates_doc(tmp_path, monkeypatch):
    import amg.cli as cli

    ledger = tmp_path / "run_timings.jsonl"
    _write_run_timings(ledger)
    doc = tmp_path / "phase_timing_cheat_sheet.md"
    doc.write_text(
        "# Cheat\n"
        "<!-- AUTO_THRESHOLD_TABLE_START -->\n"
        "placeholder\n"
        "<!-- AUTO_THRESHOLD_TABLE_END -->\n"
    )
    monkeypatch.setattr(cli, "init_logging", lambda: None)

    args = Namespace(
        run_timings_path=ledger,
        min_samples=2,
        doc_path=doc,
        update_doc=True,
    )
    code = cli.cmd_timing_calibrate(args)
    assert code == 0
    assert "tier_scan" in doc.read_text()
