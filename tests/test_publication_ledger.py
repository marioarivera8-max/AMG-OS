def test_publication_ledger_records_latest_status(monkeypatch, tmp_path):
    import amg.publication.ledger as ledger

    monkeypatch.setattr(ledger, "PUBLICATION_EVENTS_PATH", tmp_path / "publication" / "events.jsonl")
    monkeypatch.setattr(ledger, "PUBLICATION_STATUS_DIR", tmp_path / "publication" / "status")

    first = ledger.record_publication_event(
        "scene_pub",
        "aebn",
        "submitted",
        operator="tester",
        external_id="A-123",
    )
    second = ledger.record_publication_event(
        "scene_pub",
        "AEBN",
        "published",
        operator="tester",
        notes="Live",
    )

    status = ledger.load_publication_status("scene_pub")
    assert status["platforms"]["AEBN"]["status"] == "published"
    assert status["platforms"]["AEBN"]["notes"] == "Live"
    assert status["events_count"] == 2

    events = ledger.list_publication_events("scene_pub")
    assert [e["event_id"] for e in events] == [second["event_id"], first["event_id"]]


def test_publication_ledger_rejects_unknown_status(monkeypatch, tmp_path):
    import pytest
    import amg.publication.ledger as ledger

    monkeypatch.setattr(ledger, "PUBLICATION_EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(ledger, "PUBLICATION_STATUS_DIR", tmp_path / "status")

    with pytest.raises(ValueError):
        ledger.record_publication_event("scene_pub", "AEBN", "auto_uploaded")
