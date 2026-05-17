#!/usr/bin/env python3
"""Convert Google-Sheets-HTML "Web VOD delivery" exports into the CSV manifest
format the AMG organizer reads.

Run this whenever a fresh Web VOD delivery export (saved as HTML from Google
Sheets) arrives from Naughty America. The script extracts the rows by header
name (column order varies between deliveries), forward-fills the merged
DVD Title cells, drops non-data rows, and writes a manifest CSV with the
columns the organizer expects:

    DVD Title, Scene ID, Scene Publication, Scene Title, Video file

Standard library only. Python 3.12.

Example:
    python manifest_from_html.py "Delivery 5.html" --output manifest_delivery5.csv
"""

from __future__ import annotations

import argparse
import csv
import html as html_lib
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# Columns the organizer expects, in this exact order.
OUTPUT_COLUMNS = [
    "DVD Title",
    "Scene ID",
    "Scene Publication",
    "Scene Title",
    "Video file",
]


class RowParser(HTMLParser):
    """Collect every <tr> as a list of cell text strings.

    Only <td> and <th> elements that live directly inside a <tr> are recorded
    as cells. Cells preserve inner text (with tags stripped and HTML entities
    decoded). This keeps row structure intact so we can locate the header row
    and align data rows by index.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._cell_buf: list[str] | None = None
        # Track <a href="..."> so we can fall back to the href if the visible
        # text is empty (some exports wrap the video URL in an <a>).
        self._current_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th") and self._current_row is not None:
            self._cell_buf = []
            self._current_href = None
        elif tag == "a" and self._cell_buf is not None:
            for k, v in attrs:
                if k == "href" and v:
                    self._current_href = v
                    break
        elif tag == "br" and self._cell_buf is not None:
            self._cell_buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell_buf is not None and self._current_row is not None:
            text = "".join(self._cell_buf).strip()
            if not text and self._current_href:
                text = self._current_href.strip()
            self._current_row.append(text)
            self._cell_buf = None
            self._current_href = None
        elif tag == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._cell_buf is not None:
            self._cell_buf.append(data)


def _clean(s: str) -> str:
    """Normalize whitespace and decode any lingering HTML entities."""
    s = html_lib.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_rows(html_path: Path) -> list[dict[str, str]]:
    """Parse one HTML export and return its data rows as dicts keyed by
    OUTPUT_COLUMNS. Raises ValueError if the header row can't be located."""
    parser = RowParser()
    parser.feed(html_path.read_text(encoding="utf-8", errors="replace"))
    parser.close()

    # The header row is the first <tr> whose cells contain "Scene ID".
    header_idx = None
    header_cells: list[str] = []
    for i, row in enumerate(parser.rows):
        cleaned = [_clean(c) for c in row]
        if any(c == "Scene ID" for c in cleaned):
            header_idx = i
            header_cells = cleaned
            break
    if header_idx is None:
        raise ValueError(f"{html_path}: header row containing 'Scene ID' not found")

    # Build header-name -> column-index map. Later duplicates lose to first.
    col_index: dict[str, int] = {}
    for idx, name in enumerate(header_cells):
        if name and name not in col_index:
            col_index[name] = idx

    missing = [c for c in OUTPUT_COLUMNS if c not in col_index]
    if missing:
        raise ValueError(
            f"{html_path}: header is missing required columns: {missing} "
            f"(found: {header_cells})"
        )

    out_rows: list[dict[str, str]] = []
    last_dvd_title = ""
    for row in parser.rows[header_idx + 1 :]:
        cells = [_clean(c) for c in row]
        # The first cell on each row is a row-number <th> from Google Sheets;
        # the header row has the same shape, so column indexes align across
        # header and data rows -- no offset adjustment needed.
        def get(name: str) -> str:
            i = col_index[name]
            return cells[i] if i < len(cells) else ""

        dvd_title = get("DVD Title")
        if dvd_title:
            last_dvd_title = dvd_title
        else:
            dvd_title = last_dvd_title

        scene_id = get("Scene ID")
        if not scene_id.isdigit():
            continue  # skip blank/non-numeric rows (headers, totals, etc.)

        out_rows.append(
            {
                "DVD Title": dvd_title,
                "Scene ID": scene_id,
                "Scene Publication": get("Scene Publication"),
                "Scene Title": get("Scene Title"),
                "Video file": get("Video file"),
            }
        )

    return out_rows


def write_csv(rows: list[dict[str, str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="manifest_from_html.py",
        description=(
            "Convert Google-Sheets-HTML 'Web VOD delivery' exports into the CSV "
            "manifest format the AMG organizer reads. Run this whenever a fresh "
            "Web VOD delivery export arrives from Naughty America."
        ),
        epilog=(
            "Example:\n"
            "  python manifest_from_html.py 'Delivery 5.html' "
            "--output manifest_delivery5.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("inputs", nargs="+", type=Path, help="one or more HTML export files")
    p.add_argument(
        "--output",
        "-o",
        required=True,
        type=Path,
        help="path to write the combined manifest CSV",
    )
    args = p.parse_args(argv)

    all_rows: list[dict[str, str]] = []
    per_input: list[tuple[Path, int]] = []
    for inp in args.inputs:
        if not inp.exists():
            print(f"error: input not found: {inp}", file=sys.stderr)
            return 2
        rows = extract_rows(inp)
        per_input.append((inp, len(rows)))
        all_rows.extend(rows)

    write_csv(all_rows, args.output)

    dvd_groups = len({r["DVD Title"] for r in all_rows})
    if len(args.inputs) > 1:
        for inp, count in per_input:
            print(f"  {inp}: {count} rows")
    print(f"wrote {len(all_rows)} rows to {args.output}  ({dvd_groups} DVD groups)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
