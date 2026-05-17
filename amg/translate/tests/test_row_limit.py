"""Row count guard moves oversize files to errors folder."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook


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


class RowLimitTests(unittest.TestCase):
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

    def test_too_many_rows_goes_to_errors(self) -> None:
        import amg.translate.config as tc
        import amg.translate.watcher as w

        drop = tc.INCOMING_DIR
        err = tc.FAILED_DIR
        drop.mkdir(parents=True, exist_ok=True)
        err.mkdir(parents=True, exist_ok=True)

        p = drop / "big.xlsx"
        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "Catalog"
        hdr = ["scene_code", "source_title", "filename", "studio", "language", "notes", "title", "description", "tags"]
        for i, h in enumerate(hdr, start=1):
            ws.cell(row=1, column=i, value=h)
        for i in range(2001):
            ws.append([f"SC_{i}", "t", "f.mp4", "", "de", "", "", "", ""])
        wb.save(p)

        with patch.object(tc, "STABILITY_SCANS_REQUIRED", 1):
            w.scan_once_inner(use_claude=False)

        self.assertFalse(p.exists())
        failed = list(err.glob("big*.xlsx"))
        self.assertTrue(failed, "oversize workbook should move to errors directory")
        logs = list(err.glob("big*.error.log"))
        self.assertTrue(logs, "error log should accompany failed workbook")
        body = logs[0].read_text(encoding="utf-8")
        self.assertIn("exceeds limit", body)


if __name__ == "__main__":
    unittest.main()
