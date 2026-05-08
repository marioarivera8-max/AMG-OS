import json


def test_promote_rule_pack_from_run(monkeypatch, tmp_path):
    import amg.learning.rule_promotion as rp
    import amg.learning.rule_packs as packs

    monkeypatch.setattr(rp, "TRAINING_RULE_RUNS_DIR", tmp_path / "rule_runs")
    monkeypatch.setattr(packs, "TRAINING_RULE_PACKS_DIR", tmp_path / "rule_packs")
    monkeypatch.setattr(packs, "TRAINING_ACTIVE_RULE_PACK_PATH", tmp_path / "active_rule_pack.json")

    packs.save_rule_pack(rule_pack_id="pack_promote", constraints={"tag_boost": ["pov"]})
    run_dir = (tmp_path / "rule_runs" / "run_001")
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "rule_eval_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": "run_001",
                "rule_pack_id": "pack_promote",
                "gates": {"pass": True, "blocked_reasons": []},
                "promotion": {"status": "not_promoted"},
            }
        ),
        encoding="utf-8",
    )

    out = rp.promote_rule_pack_from_run(run_id="run_001", mode="canary", canary_pct=25.0)
    assert out["status"] == "promoted"
    active = packs.get_active_rule_pointer()
    assert active["enabled"] is True
    assert active["rule_pack_id"] == "pack_promote"


def test_advance_rule_pack_retrieval_stage_from_run(monkeypatch, tmp_path):
    import amg.learning.rule_promotion as rp
    import amg.learning.rule_packs as packs

    monkeypatch.setattr(rp, "TRAINING_RULE_RUNS_DIR", tmp_path / "rule_runs")
    monkeypatch.setattr(packs, "TRAINING_RULE_PACKS_DIR", tmp_path / "rule_packs")
    monkeypatch.setattr(packs, "TRAINING_ACTIVE_RULE_PACK_PATH", tmp_path / "active_rule_pack.json")

    packs.save_rule_pack(
        rule_pack_id="pack_rollout",
        constraints={"tag_boost": ["pov"], "retrieval_stage": "titles"},
    )
    run_dir = tmp_path / "rule_runs" / "run_002"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "rule_eval_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": "run_002",
                "rule_pack_id": "pack_rollout",
                "gates": {"pass": True, "blocked_reasons": []},
                "retrieval_rollout": {
                    "advance_recommended": True,
                    "current_stage": "titles",
                    "next_stage": "titles_description",
                },
                "promotion": {"status": "not_promoted"},
            }
        ),
        encoding="utf-8",
    )

    out = rp.advance_rule_pack_retrieval_stage_from_run(run_id="run_002")
    assert out["status"] == "advanced"
    updated = packs.load_rule_pack("pack_rollout")
    constraints = (updated or {}).get("constraints") or {}
    assert constraints.get("retrieval_stage") == "titles_description"
