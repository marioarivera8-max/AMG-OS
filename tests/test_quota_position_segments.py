from amg.output.quota_fill import QuotaSpec, select_quota_fill
from amg.scoring.parser import ScoredFrame


def _candidate(ts: float, score: float, label: str, *, type_: str = "PENETRATION") -> dict:
    scored = ScoredFrame(
        parse_succeeded=True,
        score=score,
        type_=type_,
        penetration_visible=True,
        penetration_confidence=0.95,
        position_label=label,
        position_confidence=0.9,
    )
    return {
        "timestamp_sec": ts,
        "scored_frame": scored,
        "position_label": label,
        "position_label_confidence": 0.9,
    }


def test_temporal_position_segments_get_three_picks_each():
    candidates = [
        _candidate(0.0, 92.0, "DOGGY_STYLE"),
        _candidate(10.0, 91.0, "DOGGY_STYLE"),
        _candidate(22.0, 90.0, "DOGGY_STYLE"),
        _candidate(180.0, 89.0, "MISSIONARY"),
        _candidate(190.0, 88.0, "MISSIONARY"),
        _candidate(205.0, 87.0, "MISSIONARY"),
        _candidate(400.0, 86.0, "DOGGY_STYLE"),
        _candidate(410.0, 85.0, "DOGGY_STYLE"),
        _candidate(425.0, 84.0, "DOGGY_STYLE"),
    ]
    quota = QuotaSpec(
        posterpose=0,
        buildup=0,
        finish=0,
        min_gap_sec=20.0,
        position_segment_target=3,
        position_segment_max_total=9,
    )

    selected, stats = select_quota_fill(candidates, max_total=6, min_total=0, quota=quota)

    assert len(selected) == 9
    assert stats["effective_max_total"] == 9
    assert stats["position_segments"] == 3
    assert stats["position_segment_DOGGY_STYLE_01"] == 3
    assert stats["position_segment_MISSIONARY_01"] == 3
    assert stats["position_segment_DOGGY_STYLE_02"] == 3
    assert {c["position_segment_id"] for c in selected} == {
        "DOGGY_STYLE_01",
        "MISSIONARY_01",
        "DOGGY_STYLE_02",
    }


def test_oral_sex_position_labels_count_as_position_bucket():
    candidates = [
        _candidate(0.0, 90.0, "ORAL_BJ", type_="SEX_ACT"),
        _candidate(10.0, 88.0, "ORAL_BJ", type_="SEX_ACT"),
        _candidate(25.0, 86.0, "ORAL_BJ", type_="SEX_ACT"),
    ]
    quota = QuotaSpec(posterpose=0, buildup=0, finish=0, position_segment_target=3)

    selected, stats = select_quota_fill(candidates, max_total=3, min_total=0, quota=quota)

    assert len(selected) == 3
    assert stats["bucket_positions"] == 3
    assert stats["position_segment_ORAL_BJ_01"] == 3
