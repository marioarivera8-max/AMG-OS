"""Daily spend cap limits Anthropic batch calls."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from amg.translate.claude_client import PolishResult


def _many_med_rows(path: Path, n: int) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Catalog"
    hdr = ["scene_code", "source_title", "filename", "studio", "language", "notes", "title", "description", "tags"]
    for i, h in enumerate(hdr, start=1):
        ws.cell(row=1, column=i, value=h)
    for i in range(n):
        ws.append(
            [
                f"BH_MED_{i}",
                "stiefmutter erwischt dich beim wichsen",
                f"bh_{i}.mp4",
                "",
                "de",
                "",
                "",
                "",
                "",
            ]
        )
    wb.save(path)


def _reload_cfg_proc() -> None:
    import amg.translate.claude_client as cc
    import amg.translate.config as cfg
    import amg.translate.processor as proc
    import amg.translate.spend_tracker as st

    importlib.reload(cfg)
    importlib.reload(st)
    importlib.reload(cc)
    importlib.reload(proc)


class SpendCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.environ["AMG_OS_ROOT"] = str(self.root / "amg_state")
        os.environ["AMG_TRANSLATE_CLAUDE"] = "1"
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        os.environ["AMG_TRANSLATE_DAILY_SPEND_CAP_USD"] = "0.01"
        _reload_cfg_proc()

    def tearDown(self) -> None:
        self._tmp.cleanup()
        for k in ("AMG_OS_ROOT", "AMG_TRANSLATE_DAILY_SPEND_CAP_USD", "ANTHROPIC_API_KEY"):
            os.environ.pop(k, None)
        os.environ["AMG_TRANSLATE_CLAUDE"] = "0"
        _reload_cfg_proc()

    def test_second_chunk_skipped_after_first_batch_charge(self) -> None:
        import amg.translate.config as cfg
        import amg.translate.processor as proc
        import amg.translate.spend_tracker as st

        src = Path(self._tmp.name) / "med_batch.xlsx"
        out = Path(self._tmp.name) / "out.xlsx"
        _many_med_rows(src, 25)

        calls: list[int] = []

        def fake_batch(requests):
            spent = st.get_today_summary()[1]
            if spent >= cfg.DAILY_SPEND_CAP_USD:
                return []
            calls.append(len(requests))
            st.record_batch_charge()
            return [
                PolishResult(
                    scene_code=r.scene_code,
                    title=f"Polished {r.scene_code}",
                    description="Sensory pacing scene with bold studio energy and crisp audio.",
                    tags=["Adult", "MILF", "Solo", "Teaser", "Sexy", "Scene"],
                    confidence="high",
                )
                for r in requests
            ]

        with mock.patch.object(proc, "polish_batch", side_effect=fake_batch):
            proc.process_workbook(src, out, overwrite_titles=False, use_claude=True)

        self.assertEqual(calls, [10], "only the first Claude batch runs before spend cap stops further polish")
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
