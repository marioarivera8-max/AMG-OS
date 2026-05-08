import json


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_rule_kpi_eval_and_gates(monkeypatch, tmp_path):
    import amg.learning.rule_evaluator as reval

    reviewed_dir = tmp_path / "reviewed"
    feedback_path = tmp_path / "operator_feedback" / "feedback.jsonl"
    data_dir = tmp_path

    monkeypatch.setattr(reval, "REVIEWED_DIR", reviewed_dir)
    monkeypatch.setattr(reval, "OPERATOR_FEEDBACK_PATH", feedback_path)
    monkeypatch.setattr(reval, "DATA_DIR", data_dir)

    _write_json(
        reviewed_dir / "scene_a.json",
        {
            "scene_id": "scene_a",
            "timestamp": "2026-05-07T12:00:00Z",
            "title_override": "Scene A Title",
            "long_description": "Scene A description",
            "metadata_validation": {"overall_ready": True, "blockers": []},
            "rule_pack_id": "pack_1",
        },
    )
    _write_json(
        reviewed_dir / "scene_b.json",
        {
            "scene_id": "scene_b",
            "timestamp": "2026-05-07T12:00:00Z",
            "title_override": "Scene B Title",
            "long_description": "Scene B description",
            "metadata_validation": {"overall_ready": False, "blockers": ["x"]},
            "rule_pack_id": None,
        },
    )
    _write_json(
        data_dir / "work_dirs" / "scene_a" / "insight.json",
        {
            "ai_titles": [{"text": "Scene A Title"}],
            "long_description": "Scene A description",
        },
    )
    _write_json(
        data_dir / "work_dirs" / "scene_b" / "insight.json",
        {
            "ai_titles": [{"text": "Different"}],
            "long_description": "Different description",
        },
    )
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-07T12:00:00Z",
                        "scene_id": "scene_a",
                        "operator": {"decision": "keep"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-07T12:00:00Z",
                        "scene_id": "scene_b",
                        "operator": {"decision": "reject"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    res = reval.evaluate_rule_pack_kpis(rule_pack_id="pack_1", days_back=30)
    assert res.candidate["scene_count"] == 1
    assert "metadata_ready_rate_pct_delta" in res.delta
    gates = reval.evaluate_rule_promotion_gates(
        {
            "candidate": res.candidate,
            "baseline": res.baseline,
            "delta": res.delta,
        }
    )
    assert "pass" in gates
    assert "blocked_reasons" in gates


def test_rule_gates_require_two_consecutive_windows():
    import amg.learning.rule_evaluator as reval

    gates = reval.evaluate_rule_promotion_gates(
        {
            "candidate": {
                "scene_count": 8,
                "metadata_acceptance_rate_pct": 80.0,
            },
            "baseline": {
                "scene_count": 8,
            },
            "delta": {
                "metadata_ready_rate_pct_delta": 1.0,
                "avg_metadata_blockers_delta": -0.2,
                "avg_metadata_blocker_severity_delta": -0.2,
                "metadata_acceptance_rate_pct_delta": 4.0,
                "median_title_edit_distance_delta": -0.03,
                "median_description_edit_distance_delta": -0.02,
                "feedback_reject_rate_pct_delta": -1.0,
            },
            "recent_runs": [{"gates": {"pass": True}}],
        }
    )
    assert gates["pass"] is True
    assert gates.get("consecutive_windows_passing") == 2
