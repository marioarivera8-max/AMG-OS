"""Same filename with different content gets distinct archive names."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook


def _write_row(path: Path, code: str) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Catalog"
    hdr = ["scene_code", "source_title", "filename", "studio", "language", "notes", "title", "description", "tags"]
    for i, h in enumerate(hdr, start=1):
        ws.cell(row=1, column=i, value=h)
    ws.append(
        [
            code,
            "sekretärin bleibt nach feier",
            f"{code}.mp4",
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


class NameCollisionTests(unittest.TestCase):
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
    def test_sequential_same_name_different_hash(self, mock_proc) -> None:
        import amg.translate.config as tc
        import amg.translate.watcher as w

        drop = tc.INCOMING_DIR
        arch = tc.ARCHIVE_DIR
        drop.mkdir(parents=True, exist_ok=True)
        arch.mkdir(parents=True, exist_ok=True)

        p = drop / "batch.xlsx"
        _write_row(p, "ROW_A")
        with patch.object(tc, "STABILITY_SCANS_REQUIRED", 1):
            w.scan_once_inner(use_claude=False)
        self.assertEqual(mock_proc.call_count, 1)

        _write_row(p, "ROW_B")
        with patch.object(tc, "STABILITY_SCANS_REQUIRED", 1):
            w.scan_once_inner(use_claude=False)
        self.assertEqual(mock_proc.call_count, 2)

        originals = sorted(arch.glob("batch*.xlsx"))
        self.assertGreaterEqual(len(originals), 2)
        names = {x.name for x in originals}
        self.assertEqual(len(names), len(originals), "archive entries must be unique on disk")


if __name__ == "__main__":
    unittest.main()
