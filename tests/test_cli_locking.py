from pathlib import Path


def test_acquire_batch_lock_fails_when_existing_lock_unreadable(monkeypatch, tmp_path):
    import amg.cli as cli

    lock_path = tmp_path / "batch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("existing-lock")
    monkeypatch.setattr(cli, "BATCH_LOCK_FILE", lock_path)

    real_open = open

    def fake_open(path, *args, **kwargs):
        if Path(path) == lock_path:
            raise OSError("permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    assert cli._acquire_batch_lock() is False
    assert lock_path.read_text() == "existing-lock"
