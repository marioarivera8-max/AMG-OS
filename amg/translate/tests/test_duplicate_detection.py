"""Duplicate workbook detection archived without re-processing."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook


def _minimal_catalog(path: Path, suffix: str) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Catalog"
    hdr = ["scene_code", "source_title", "filename", "studio", "language", "notes", "title", "description", "tags"]
    for i, h in enumerate(hdr, start=1):
        ws.cell(row=1, column=i, value=h)
    ws.append(
        [
            f"SC_{suffix}",
            "sekretärin bleibt nach feier",
            f"clip_{suffix}.mp4",
            "",
            "de",
            "",
            "",
            "",
            "",
        ]
    )
    wb.save(path)


def _reload_translate_stack() -> None:
    import amg.translate.claude_client as cc
    import amg.translate.config as cfg
    import amg.translate.processor as proc
    import amg.translate.spend_tracker as st
    import amg.translate.watcher as watch

    importlib.reload(cfg)
    importlib.reload(st)
    importlib.reload(cc)
    importlib.reload(proc)
    importlib.reload(watch)


class DuplicateDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["AMG_TRANSLATE_CLAUDE"] = "0"
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.environ["AMG_TRANSLATE_ROOT"] = str(self.root)
        os.environ["AMG_OS_ROOT"] = str(self.root / "amg_state")
        _reload_translate_stack()

    def tearDown(self) -> None:
        self._tmp.cleanup()
        os.environ.pop("AMG_TRANSLATE_ROOT", None)
        os.environ.pop("AMG_OS_ROOT", None)
        _reload_translate_stack()

    @patch("amg.translate.watcher.process_workbook")
    def test_second_identical_drop_is_archived_as_duplicate(self, mock_proc) -> None:
        import amg.translate.config as tc

        drop = tc.INCOMING_DIR
        arch = tc.ARCHIVE_DIR
        drop.mkdir(parents=True, exist_ok=True)
        arch.mkdir(parents=True, exist_ok=True)

        f1 = drop / "batch.xlsx"
        _minimal_catalog(f1, "a")

        import amg.translate.watcher as w

        with patch.object(tc, "STABILITY_SCANS_REQUIRED", 1):
            w.scan_once_inner(use_claude=False)
        self.assertEqual(mock_proc.call_count, 1)
        self.assertFalse(f1.exists())

        f2 = drop / "batch.xlsx"
        _minimal_catalog(f2, "a")

        with patch.object(tc, "STABILITY_SCANS_REQUIRED", 1):
            w.scan_once_inner(use_claude=False)
        self.assertEqual(mock_proc.call_count, 1, "processor should not run for duplicate content")

        dups = list(arch.glob("*_duplicate_*.xlsx"))
        self.assertEqual(len(dups), 1)


if __name__ == "__main__":
    unittest.main()
