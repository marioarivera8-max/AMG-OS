#!/usr/bin/env python3
"""
Amy delivery automation.

Turns Amy's spreadsheet export into local DVD-title folders, downloads each
scene into the right folder, verifies row/file coverage, and can upload the
completed delivery to a configured Google Drive rclone remote.

Network actions are explicit:
  - download: fetches scene URLs from the manifest.
  - upload: copies the finished local delivery folder to rclone.
Everything else is local validation/reporting.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


DEFAULT_ROOT = Path(os.environ.get("AMG_ROOT", "~/AMG_OS")).expanduser()
DEFAULT_DELIVERIES_DIR = DEFAULT_ROOT / "deliveries"
DEFAULT_REMOTE = "gdrive_amy:"
DEFAULT_REMOTE_BASE = "Amy Deliveries"
DEFAULT_INTAKE_DIR = DEFAULT_ROOT / "delivery_intake"
DEFAULT_BROWSER_PROFILE_DIR = DEFAULT_ROOT / "browser_profiles" / "studio"
PLAYWRIGHT_FALLBACK_PYTHON = Path("/Users/mariorivera/keyframe-ingestion/venv/bin/python")
DEFAULT_DRIVE_AMY_DELIVERIES = Path(
    "/Users/mariorivera/Library/CloudStorage/"
    "GoogleDrive-triedtrue411@gmail.com/My Drive/Amy Deliveries"
)
DEFAULT_SHEET_INBOX = DEFAULT_DRIVE_AMY_DELIVERIES / "DVD Scene Organizer Inbox"
DEFAULT_SHEET_DONE = DEFAULT_SHEET_INBOX / "_processed"
DEFAULT_SHEET_FAILED = DEFAULT_SHEET_INBOX / "_failed"
FORBIDDEN_DELIVERY_OUTPUT_DIRS = {
    DEFAULT_DRIVE_AMY_DELIVERIES.parent.resolve(),
    DEFAULT_DRIVE_AMY_DELIVERIES.resolve(),
    Path.home().resolve(),
    Path("/").resolve(),
}

SERIES_NAME_MAP = {
    "ADD": "American Daydreams",
    "ATH": "Naughty Athletics",
    "DWC": "Dirty Wives Club",
    "NADWC": "Dirty Wives Club",
    "IHW": "I Have a Wife",
    "MDHF": "My Daughter's Hot Friend",
    "MDHG": "My Dad's Hot Girlfriend",
    "MFHG": "My Friend's Hot Girl",
    "MFHM": "My Friend's Hot Mom",
    "MFST": "My First Sex Teacher",
    "MSHF": "My Sister's Hot Friend",
    "MWHF": "My Wife's Hot Friend",
    "NAF": "Naughty America Flix",
    "NAPFS": "Perfect Fucking Strangers",
    "NBW": "Neighbor's Bush",
    "NO": "Naughty Office",
    "TNGF": "Tonight's Girlfriend",
}

REQUIRED_COLUMNS = {
    "dvd_title": ("DVD Title", "delivery_folder", "Delivery Folder", "Title"),
    "scene_id": ("Scene ID", "scene_id", "SceneID", "ID"),
    "scene_title": ("Scene Title", "scene_title", "Title"),
    "video_url": ("Video file", "Video File", "video_url", "URL", "Download URL"),
}

LINK_SUFFIX = " __link"

ASSET_COLUMN_HINTS = (
    "2257",
    "asset",
    "art",
    "compliance",
    "cover",
    "document",
    "model release",
    "model releases",
    "psd",
    "release",
    "sleeve",
)

VIDEO_COLUMN_HINTS = ("video", "mp4", "movie")

DVD_CONTINUATION_MARKERS = {"", "x", "same", "ditto", '"'}


@dataclass(frozen=True)
class ManifestRow:
    source_row: int
    dvd_title: str
    scene_id: str
    scene_title: str
    video_url: str
    filename: str
    raw: dict[str, str]
    display_dvd_title: str

    @property
    def folder_name(self) -> str:
        return delivery_folder_name(self.dvd_title)

    @property
    def validto(self) -> int | None:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.video_url).query)
        try:
            return int(qs.get("validto", [""])[0])
        except (TypeError, ValueError):
            return None


def safe_name(value: str, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        text = fallback
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(". ")
    return text or fallback


def filename_from_url(url: str, fallback: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(urllib.parse.unquote(parsed.path)).name
    return safe_name(name, fallback)


def delivery_folder_name(dvd_title: str) -> str:
    """Convert sheet shorthand like 'ADD Vol. 24' to Amy's Drive folder name."""
    cleaned = safe_name(dvd_title, "Untitled DVD")
    match = re.match(r"^(?P<prefix>[A-Za-z]+)\s+Vol\.\s*(?P<num>\d+)$", cleaned)
    if not match:
        return cleaned
    prefix = match.group("prefix").upper()
    full = SERIES_NAME_MAP.get(prefix)
    if not full:
        return cleaned
    return f"{full} Vol. {match.group('num')}"


@dataclass(frozen=True)
class CompanionAsset:
    source_row: int
    dvd_title: str
    folder_name: str
    scene_id: str
    scene_title: str
    column: str
    url: str
    filename: str
    relative_path: str
    kind: str


def resolve_columns(header: Iterable[str]) -> dict[str, str]:
    actual_by_lower = {h.strip().lower(): h for h in header if h}
    found: dict[str, str] = {}
    for logical, candidates in REQUIRED_COLUMNS.items():
        for candidate in candidates:
            actual = actual_by_lower.get(candidate.lower())
            if actual:
                found[logical] = actual
                break
    return found


def column_index_from_ref(cell_ref: str) -> int:
    letters = re.match(r"([A-Z]+)", cell_ref.upper())
    if not letters:
        return 0
    index = 0
    for char in letters.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def read_xlsx_first_sheet(path: Path) -> list[dict[str, str]]:
    ns = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for item in root.findall("main:si", ns):
                parts = [node.text or "" for node in item.findall(".//main:t", ns)]
                shared.append("".join(parts))

        wb = ET.fromstring(z.read("xl/workbook.xml"))
        first_sheet = wb.find("main:sheets/main:sheet", ns)
        if first_sheet is None:
            return []
        rel_id = first_sheet.attrib.get(f"{{{ns['rel']}}}id")
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = ""
        for rel in rels.findall("pkgrel:Relationship", ns):
            if rel.attrib.get("Id") == rel_id:
                target = rel.attrib.get("Target", "")
                break
        if not target:
            return []
        sheet_path = "xl/" + target.lstrip("/")
        if sheet_path not in z.namelist():
            sheet_path = "xl/worksheets/" + Path(target).name

        sheet_rels_path = str(Path(sheet_path).parent / "_rels" / (Path(sheet_path).name + ".rels"))
        rel_targets: dict[str, str] = {}
        if sheet_rels_path in z.namelist():
            sheet_rels = ET.fromstring(z.read(sheet_rels_path))
            for rel in sheet_rels.findall("pkgrel:Relationship", ns):
                rel_targets[rel.attrib.get("Id", "")] = rel.attrib.get("Target", "")

        sheet = ET.fromstring(z.read(sheet_path))
        hyperlink_by_cell: dict[str, str] = {}
        for link in sheet.findall("main:hyperlinks/main:hyperlink", ns):
            ref = link.attrib.get("ref", "")
            rel_id = link.attrib.get(f"{{{ns['rel']}}}id", "")
            target = rel_targets.get(rel_id) or link.attrib.get("location", "")
            if ref and target:
                hyperlink_by_cell[ref] = target

        matrix: list[list[str]] = []
        for row in sheet.findall("main:sheetData/main:row", ns):
            values: list[str] = []
            for cell in row.findall("main:c", ns):
                index = column_index_from_ref(cell.attrib.get("r", ""))
                while len(values) <= index:
                    values.append("")
                cell_type = cell.attrib.get("t")
                text = ""
                if cell_type == "inlineStr":
                    text = "".join(node.text or "" for node in cell.findall(".//main:t", ns))
                else:
                    node = cell.find("main:v", ns)
                    if node is not None and node.text is not None:
                        text = node.text
                        if cell_type == "s":
                            try:
                                text = shared[int(text)]
                            except (ValueError, IndexError):
                                pass
                values[index] = text
            matrix.append(values)

        if not matrix:
            return []
        header = [str(value).strip() for value in matrix[0]]
        records: list[dict[str, str]] = []
        for row_index, values in enumerate(matrix[1:], start=2):
            if not any(str(value).strip() for value in values):
                continue
            record: dict[str, str] = {}
            for index, column in enumerate(header):
                if not column:
                    continue
                cell_ref = f"{chr(ord('A') + index)}{row_index}" if index < 26 else ""
                value = values[index] if index < len(values) else ""
                record[column] = str(value)
                if cell_ref and cell_ref in hyperlink_by_cell:
                    record[column + LINK_SUFFIX] = hyperlink_by_cell[cell_ref]
            records.append(record)
        return records


def read_manifest_records(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if path.suffix.lower() == ".xlsx":
        records = read_xlsx_first_sheet(path)
        header: list[str] = []
        for record in records:
            for key in record:
                if key not in header:
                    header.append(key)
        return records, header
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def load_manifest(path: Path) -> tuple[list[ManifestRow], dict[str, str], list[str]]:
    records, header = read_manifest_records(path)
    columns = resolve_columns(header)
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    rows: list[ManifestRow] = []
    if missing:
        return rows, columns, missing

    current_dvd_title = ""
    for source_row, raw in enumerate(records, start=2):
        display_dvd_title = (raw.get(columns["dvd_title"]) or "").strip()
        if display_dvd_title.lower() not in DVD_CONTINUATION_MARKERS:
            current_dvd_title = display_dvd_title
        dvd_title = current_dvd_title
        scene_id = (raw.get(columns["scene_id"]) or "").strip()
        scene_title = (raw.get(columns["scene_title"]) or "").strip()
        video_url = (raw.get(columns["video_url"]) or "").strip()

        if not any((display_dvd_title, scene_id, scene_title, video_url)):
            continue
        fallback = f"{safe_name(scene_id, 'scene')}.mp4"
        rows.append(
            ManifestRow(
                source_row=source_row,
                dvd_title=dvd_title,
                scene_id=scene_id,
                scene_title=scene_title,
                video_url=video_url,
                filename=filename_from_url(video_url, fallback),
                raw=dict(raw),
                display_dvd_title=display_dvd_title,
            )
        )
    return rows, columns, []


def load_manifest_old(path: Path) -> tuple[list[ManifestRow], dict[str, str], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        columns = resolve_columns(header)
        missing = [name for name in REQUIRED_COLUMNS if name not in columns]
        rows: list[ManifestRow] = []
        if missing:
            return rows, columns, missing

        current_dvd_title = ""
        for source_row, raw in enumerate(reader, start=2):
            display_dvd_title = (raw.get(columns["dvd_title"]) or "").strip()
            if display_dvd_title.lower() not in DVD_CONTINUATION_MARKERS:
                current_dvd_title = display_dvd_title
            dvd_title = current_dvd_title
            scene_id = (raw.get(columns["scene_id"]) or "").strip()
            scene_title = (raw.get(columns["scene_title"]) or "").strip()
            video_url = (raw.get(columns["video_url"]) or "").strip()

            if not any((display_dvd_title, scene_id, scene_title, video_url)):
                continue
            fallback = f"{safe_name(scene_id, 'scene')}.mp4"
            rows.append(
                ManifestRow(
                    source_row=source_row,
                    dvd_title=dvd_title,
                    scene_id=scene_id,
                    scene_title=scene_title,
                    video_url=video_url,
                    filename=filename_from_url(video_url, fallback),
                    raw=dict(raw),
                    display_dvd_title=display_dvd_title,
                )
            )
    return rows, columns, []


def infer_delivery_name(manifest: str) -> str:
    stem = Path(manifest).stem
    match = re.search(r"(Delivery\s*\d+)", stem, flags=re.IGNORECASE)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).title()
    cleaned = re.sub(r"[_-]+", " ", stem)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Delivery"


def effective_delivery_name(args: argparse.Namespace) -> str:
    name = args.delivery_name or infer_delivery_name(args.manifest)
    prefix = getattr(args, "delivery_prefix", "") or ""
    suffix = getattr(args, "delivery_suffix", "") or ""
    return safe_name(f"{prefix}{name}{suffix}", "Delivery")


def delivery_dir(base_dir: Path, delivery_name: str) -> Path:
    return base_dir / safe_name(delivery_name, "Delivery")


def assert_safe_delivery_dir(out_dir: Path, delivery_name: str) -> None:
    resolved = out_dir.resolve()
    expected_name = safe_name(delivery_name, "Delivery")
    if resolved.name != expected_name:
        raise ValueError(
            f"refusing unsafe output directory {resolved}: expected final folder name {expected_name!r}"
        )
    if resolved in FORBIDDEN_DELIVERY_OUTPUT_DIRS:
        raise ValueError(f"refusing to write generated delivery files directly into {resolved}")


def safe_delivery_dir_from_args(args: argparse.Namespace) -> tuple[str, Path]:
    delivery_name = effective_delivery_name(args)
    out_dir = delivery_dir(Path(args.out_dir).expanduser(), delivery_name)
    assert_safe_delivery_dir(out_dir, delivery_name)
    return delivery_name, out_dir


def row_key(row: ManifestRow) -> str:
    return f"row_{row.source_row}_{row.scene_id or row.filename}"


def status_path(out_dir: Path) -> Path:
    return out_dir / "_delivery_status.json"


def load_status(out_dir: Path) -> dict:
    path = status_path(out_dir)
    if not path.exists():
        return {"rows": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"rows": {}}


def save_status(out_dir: Path, state: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = status_path(out_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def target_for(out_dir: Path, row: ManifestRow) -> Path:
    return out_dir / row.folder_name / row.filename


def extract_urls(value: str) -> list[str]:
    if not value:
        return []
    return [
        match.rstrip("),.;]")
        for match in re.findall(r"https?://[^\s,;]+", value)
    ]


def is_asset_column(column: str) -> bool:
    lower = column.lower()
    return any(hint in lower for hint in ASSET_COLUMN_HINTS) and not any(
        hint in lower for hint in VIDEO_COLUMN_HINTS
    )


def asset_kind(column: str, filename: str, url: str = "") -> str:
    lower = f"{column} {filename} {url}".lower()
    if "drive.google.com/drive/folders/" in lower or "drive.google.com/open?id=" in lower:
        return "asset_folder"
    if any(token in lower for token in ("2257", "compliance", "release", "releases")):
        return "2257"
    if any(token in lower for token in ("sleeve", "cover", "art", ".psd")):
        return "dvd_asset"
    return "dvd_asset"


def companion_assets(rows: list[ManifestRow], columns: dict[str, str]) -> list[CompanionAsset]:
    video_column = columns.get("video_url", "")
    assets: list[CompanionAsset] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        for column, value in row.raw.items():
            if column == video_column or not is_asset_column(column):
                continue
            for url in extract_urls(value or ""):
                parsed_name = filename_from_url(url, f"asset_row_{row.source_row}")
                kind = asset_kind(column, parsed_name, url)
                if kind == "asset_folder":
                    parsed_name = safe_name((row.raw.get(column.removesuffix(LINK_SUFFIX)) or row.folder_name), "asset_folder")
                relative = Path(row.folder_name) / ("2257" if kind == "2257" else "") / parsed_name
                relative_text = str(relative).replace("//", "/")
                key = (row.folder_name.lower(), relative_text.lower(), url)
                if key in seen:
                    continue
                seen.add(key)
                assets.append(
                    CompanionAsset(
                        source_row=row.source_row,
                        dvd_title=row.dvd_title,
                        folder_name=row.folder_name,
                        scene_id=row.scene_id,
                        scene_title=row.scene_title,
                        column=column,
                        url=url,
                        filename=parsed_name,
                        relative_path=relative_text,
                        kind=kind,
                    )
                )
    return assets


def companion_target(out_dir: Path, asset: CompanionAsset) -> Path:
    return out_dir / asset.relative_path


def scene_people(scene_title: str) -> list[str]:
    people: list[str] = []
    for part in re.split(r"\s*/\s*", scene_title or ""):
        person = re.sub(r"\s+", " ", part).strip()
        if person and person.lower() not in {p.lower() for p in people}:
            people.append(person)
    return people


def expected_companion_rows(rows: list[ManifestRow], out_dir: Path) -> list[dict[str, str]]:
    by_folder: dict[str, list[ManifestRow]] = {}
    for row in rows:
        by_folder.setdefault(row.folder_name, []).append(row)

    expected: list[dict[str, str]] = []
    for folder, folder_rows in sorted(by_folder.items()):
        folder_dir = out_dir / folder
        root_assets = list(folder_dir.glob("*.psd")) + list(folder_dir.glob("*Model Releases*.pdf"))
        release_assets = list((folder_dir / "2257").glob("*Model Releases*.pdf"))
        expected.append(
            {
                "scope": "dvd",
                "delivery_folder": folder,
                "scene_id": "",
                "scene_title": "",
                "expected": "DVD sleeve PSD in folder root",
                "status": "present" if any(p.suffix.lower() == ".psd" for p in root_assets) else "missing",
                "matched_files": " | ".join(p.name for p in root_assets if p.suffix.lower() == ".psd"),
                "note": "Delivery 1 includes one sleeve/cover PSD beside the scene videos.",
            }
        )
        expected.append(
            {
                "scope": "dvd",
                "delivery_folder": folder,
                "scene_id": "",
                "scene_title": "",
                "expected": "DVD-level Model Releases PDF",
                "status": "present" if root_assets or release_assets else "missing",
                "matched_files": " | ".join(p.name for p in root_assets + release_assets if p.suffix.lower() == ".pdf"),
                "note": "Delivery 1 often includes an aggregate model-release PDF, sometimes in root and sometimes under 2257.",
            }
        )
        for row in folder_rows:
            title = safe_name(row.scene_title.replace("/", "-"), "Scene")
            pub = (row.raw.get("Scene Publication") or row.raw.get("Publication Date") or "").strip()
            release_files = list((folder_dir / "2257").glob("*Model Releases*.pdf"))
            for person in scene_people(row.scene_title):
                person_tokens = [token for token in re.split(r"\s+", person.lower()) if token]
                candidates = [
                    p for p in release_files
                    if all(token in p.stem.lower() for token in person_tokens)
                    or title.lower().replace("_", " ") in p.stem.lower().replace("_", " ")
                    or (row.scene_id and row.scene_id in p.stem)
                ]
                expected.append(
                    {
                        "scope": "performer",
                        "delivery_folder": folder,
                        "scene_id": row.scene_id,
                        "scene_title": row.scene_title,
                        "expected": f"2257/model release for {person} ({pub} {title})",
                        "status": "present" if candidates else "missing",
                        "matched_files": " | ".join(p.name for p in candidates),
                        "note": "Each person present in each scene needs 2257/release coverage.",
                    }
                )
    return expected


def write_delivery_manifests(rows: list[ManifestRow], out_dir: Path) -> None:
    """Write root and per-folder manifests so spreadsheet context travels."""
    out_dir.mkdir(parents=True, exist_ok=True)
    routing_fields = [
        "csv_row",
        "expected_filename",
        "relative_path",
        "file_present",
        "effective_dvd_title",
        "spreadsheet_dvd_title_cell",
    ]
    source_fields: list[str] = []
    for row in rows:
        for key in row.raw:
            if key not in source_fields:
                source_fields.append(key)
    fields = routing_fields + source_fields

    def write_rows(path: Path, manifest_rows: list[ManifestRow]) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in manifest_rows:
                target = target_for(out_dir, row)
                writer.writerow(
                    {
                        "csv_row": row.source_row,
                        "expected_filename": row.filename,
                        "relative_path": str(target.relative_to(out_dir)),
                        "file_present": "yes" if target.exists() else "no",
                        "effective_dvd_title": row.dvd_title,
                        "spreadsheet_dvd_title_cell": row.display_dvd_title,
                        **row.raw,
                    }
                )

    write_rows(out_dir / "_delivery_manifest.csv", rows)
    write_rows(out_dir / "_source_spreadsheet_with_paths.csv", rows)
    by_folder: dict[str, list[ManifestRow]] = {}
    for row in rows:
        by_folder.setdefault(row.folder_name, []).append(row)
    for folder, folder_rows in by_folder.items():
        folder_dir = out_dir / folder
        folder_dir.mkdir(parents=True, exist_ok=True)
        write_rows(folder_dir / "_folder_manifest.csv", folder_rows)
        write_rows(folder_dir / "_folder_spreadsheet_rows.csv", folder_rows)


def write_companion_asset_manifests(
    rows: list[ManifestRow],
    columns: dict[str, str],
    out_dir: Path,
) -> list[CompanionAsset]:
    assets = companion_assets(rows, columns)
    fields = [
        "csv_row",
        "dvd_title",
        "delivery_folder",
        "scene_id",
        "scene_title",
        "source_column",
        "asset_kind",
        "asset_filename",
        "relative_path",
        "file_present",
        "source_url",
    ]
    root_path = out_dir / "_companion_assets.csv"
    with root_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for asset in assets:
            target = companion_target(out_dir, asset)
            writer.writerow(
                {
                    "csv_row": asset.source_row,
                    "dvd_title": asset.dvd_title,
                    "delivery_folder": asset.folder_name,
                    "scene_id": asset.scene_id,
                    "scene_title": asset.scene_title,
                    "source_column": asset.column,
                    "asset_kind": asset.kind,
                    "asset_filename": asset.filename,
                    "relative_path": asset.relative_path,
                    "file_present": "yes" if target.exists() and target.stat().st_size > 0 else "no",
                    "source_url": asset.url,
                }
            )

    by_folder: dict[str, list[CompanionAsset]] = {}
    for asset in assets:
        by_folder.setdefault(asset.folder_name, []).append(asset)
    for folder, folder_assets in by_folder.items():
        folder_dir = out_dir / folder
        folder_dir.mkdir(parents=True, exist_ok=True)
        with (folder_dir / "_companion_assets.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for asset in folder_assets:
                target = companion_target(out_dir, asset)
                writer.writerow(
                    {
                        "csv_row": asset.source_row,
                        "dvd_title": asset.dvd_title,
                        "delivery_folder": asset.folder_name,
                        "scene_id": asset.scene_id,
                        "scene_title": asset.scene_title,
                        "source_column": asset.column,
                        "asset_kind": asset.kind,
                        "asset_filename": asset.filename,
                        "relative_path": asset.relative_path,
                        "file_present": "yes" if target.exists() and target.stat().st_size > 0 else "no",
                        "source_url": asset.url,
                    }
                )
    return assets


def validate_rows(rows: list[ManifestRow]) -> list[str]:
    problems: list[str] = []
    seen_targets: dict[tuple[str, str], int] = {}
    now = int(time.time())
    for row in rows:
        label = f"CSV row {row.source_row}"
        if not row.dvd_title:
            problems.append(f"{label}: missing DVD Title")
        if not row.scene_id:
            problems.append(f"{label}: missing Scene ID")
        if not row.scene_title:
            problems.append(f"{label}: missing Scene Title")
        if not row.video_url.startswith(("http://", "https://")):
            problems.append(f"{label}: Video file is not an HTTP URL")
        if row.validto is not None and row.validto <= now:
            expires = datetime.fromtimestamp(row.validto, timezone.utc).astimezone().isoformat()
            problems.append(f"{label}: Video file link expired at {expires}")
        target = (row.folder_name.lower(), row.filename.lower())
        if target in seen_targets:
            problems.append(
                f"{label}: duplicate target filename with CSV row {seen_targets[target]} "
                f"({row.folder_name}/{row.filename})"
            )
        else:
            seen_targets[target] = row.source_row
    return problems


def print_summary(rows: list[ManifestRow], out_dir: Path, columns: dict[str, str]) -> None:
    folders = sorted({row.folder_name for row in rows})
    print(f"manifest rows: {len(rows)}")
    print(f"title folders: {len(folders)}")
    print(f"local delivery: {out_dir}")
    print("column map:")
    for logical in sorted(columns):
        print(f"  {logical}: {columns[logical]}")
    if folders:
        print("folders:")
        for folder in folders:
            count = sum(1 for row in rows if row.folder_name == folder)
            print(f"  {folder} ({count})")


def ensure_folders(rows: list[ManifestRow], out_dir: Path) -> None:
    for row in rows:
        target_for(out_dir, row).parent.mkdir(parents=True, exist_ok=True)
        (target_for(out_dir, row).parent / "2257").mkdir(exist_ok=True)


def download_one(row: ManifestRow, dst: Path, timeout: int) -> tuple[bool, str]:
    return download_url_to_path(row.video_url, dst, timeout)


def download_url_to_path(url: str, dst: Path, timeout: int) -> tuple[bool, str]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0:
        return True, "already_exists"

    fd, tmp_name = tempfile.mkstemp(prefix=dst.name + ".", suffix=".part", dir=dst.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "AMG-Delivery-Automation/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            with tmp.open("wb") as f:
                shutil.copyfileobj(response, f, length=1024 * 1024)
        if tmp.stat().st_size <= 0:
            tmp.unlink(missing_ok=True)
            return False, "empty_download"
        os.replace(tmp, dst)
        return True, "downloaded"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        return False, str(exc)


def cmd_download_assets(args: argparse.Namespace) -> int:
    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    _delivery_name, out_dir = safe_delivery_dir_from_args(args)
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2

    ensure_folders(rows, out_dir)
    write_delivery_manifests(rows, out_dir)
    assets = write_companion_asset_manifests(rows, columns, out_dir)
    if not assets:
        print("no companion asset URLs found in spreadsheet columns")
        print("wrote companion manifest anyway so this is visible in the delivery folder")
        return 0

    selected = assets[: args.limit] if args.limit else assets
    counts = {"downloaded": 0, "already_exists": 0, "failed": 0}
    for index, asset in enumerate(selected, start=1):
        if asset.kind == "asset_folder":
            print(f"[{index}/{len(selected)}] asset folder link captured -> {asset.folder_name}: {asset.url}")
            continue
        dst = companion_target(out_dir, asset)
        print(f"[{index}/{len(selected)}] asset {asset.filename} -> {asset.relative_path}")
        ok, detail = download_url_to_path(asset.url, dst, args.timeout)
        counts[detail if detail in counts else ("failed" if not ok else "downloaded")] += 1
        if not ok:
            print(f"  failed: {detail}", file=sys.stderr)
            if args.stop_on_error:
                break
        write_companion_asset_manifests(rows, columns, out_dir)

    print(
        "asset download complete: "
        f"downloaded={counts['downloaded']} "
        f"already_exists={counts['already_exists']} "
        f"failed={counts['failed']}"
    )
    return 1 if counts["failed"] else 0


def cmd_validate(args: argparse.Namespace) -> int:
    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    out_dir = delivery_dir(Path(args.out_dir).expanduser(), effective_delivery_name(args))
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        print(f"detected column map: {columns}", file=sys.stderr)
        return 2
    print_summary(rows, out_dir, columns)
    problems = validate_rows(rows)
    if problems:
        print("\nproblems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nvalidation: ok")
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    _delivery_name, out_dir = safe_delivery_dir_from_args(args)
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2
    problems = validate_rows(rows)
    if problems and not args.allow_problems:
        for problem in problems:
            print(f"problem: {problem}", file=sys.stderr)
        print("use --allow-problems to create folders anyway", file=sys.stderr)
        return 1
    ensure_folders(rows, out_dir)
    write_delivery_manifests(rows, out_dir)
    assets = write_companion_asset_manifests(rows, columns, out_dir)
    state = load_status(out_dir)
    for row in rows:
        state["rows"].setdefault(
            row_key(row),
            {
                "csv_row": row.source_row,
                "scene_id": row.scene_id,
                "dvd_title": row.dvd_title,
                "scene_title": row.scene_title,
                "filename": row.filename,
                "target": str(target_for(out_dir, row)),
                "state": "pending",
                "error": None,
            },
        )
    save_status(out_dir, state)
    print_summary(rows, out_dir, columns)
    print(f"\nprepared folders, status file, delivery manifests, and {len(assets)} companion asset links")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    _delivery_name, out_dir = safe_delivery_dir_from_args(args)
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2
    problems = validate_rows(rows)
    if problems and not args.allow_problems:
        for problem in problems:
            print(f"problem: {problem}", file=sys.stderr)
        print("download stopped before network work", file=sys.stderr)
        return 1

    ensure_folders(rows, out_dir)
    write_delivery_manifests(rows, out_dir)
    write_companion_asset_manifests(rows, columns, out_dir)
    state = load_status(out_dir)
    counts = {"downloaded": 0, "already_exists": 0, "failed": 0}
    for index, row in enumerate(rows, start=1):
        key = row_key(row)
        dst = target_for(out_dir, row)
        if not row.video_url.startswith(("http://", "https://")):
            counts["failed"] += 1
            state["rows"][key] = {
                "csv_row": row.source_row,
                "scene_id": row.scene_id,
                "dvd_title": row.dvd_title,
                "scene_title": row.scene_title,
                "filename": row.filename,
                "target": str(dst),
                "state": "unavailable",
                "error": "missing_or_non_http_video_url",
                "updated_at": int(time.time()),
            }
            save_status(out_dir, state)
            print(f"[{index}/{len(rows)}] unavailable {row.scene_id} {row.scene_title}")
            continue
        existing = state["rows"].get(key, {})
        if existing.get("state") == "downloaded" and dst.exists():
            counts["already_exists"] += 1
            print(f"[{index}/{len(rows)}] skip {row.filename}")
            continue

        print(f"[{index}/{len(rows)}] download {row.filename} -> {row.folder_name}")
        ok, detail = download_one(row, dst, args.timeout)
        state["rows"][key] = {
            "csv_row": row.source_row,
            "scene_id": row.scene_id,
            "dvd_title": row.dvd_title,
            "scene_title": row.scene_title,
            "filename": row.filename,
            "target": str(dst),
            "state": "downloaded" if ok else "failed",
            "error": None if ok else detail,
            "updated_at": int(time.time()),
        }
        save_status(out_dir, state)
        write_delivery_manifests(rows, out_dir)
        counts[detail if detail in counts else ("failed" if not ok else "downloaded")] += 1

    print(
        "download complete: "
        f"downloaded={counts['downloaded']} "
        f"already_exists={counts['already_exists']} "
        f"failed={counts['failed']}"
    )
    return 1 if counts["failed"] else 0


def ensure_playwright_available() -> None:
    try:
        import playwright.sync_api  # noqa: F401
        return
    except ModuleNotFoundError:
        if PLAYWRIGHT_FALLBACK_PYTHON.exists() and Path(sys.executable) != PLAYWRIGHT_FALLBACK_PYTHON:
            os.execv(
                str(PLAYWRIGHT_FALLBACK_PYTHON),
                [str(PLAYWRIGHT_FALLBACK_PYTHON), str(Path(__file__).resolve())] + sys.argv[1:],
            )
        raise


def trigger_browser_download(page, row: ManifestRow, timeout_ms: int):
    """Try the two common browser download paths for a signed media URL."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    # Some servers return Content-Disposition and a navigation itself becomes
    # a download. Playwright captures that only when wrapped.
    try:
        with page.expect_download(timeout=timeout_ms) as download_info:
            page.goto(row.video_url, wait_until="domcontentloaded", timeout=timeout_ms)
        return download_info.value, "navigation"
    except PlaywrightTimeoutError:
        pass
    except Exception as exc:  # noqa: BLE001
        if "Download is starting" not in str(exc) and "net::ERR_ABORTED" not in str(exc):
            print(f"  navigation note: {exc}")

    # If the URL opens as playable media, inject a normal download anchor.
    try:
        page.goto(row.video_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    with page.expect_download(timeout=timeout_ms) as download_info:
        page.evaluate(
            """
            ([url, filename]) => {
              const a = document.createElement('a');
              a.href = url;
              a.download = filename;
              a.rel = 'noopener';
              document.body.appendChild(a);
              a.click();
              a.remove();
            }
            """,
            [row.video_url, row.filename],
        )
    return download_info.value, "anchor"


def cmd_browser_download(args: argparse.Namespace) -> int:
    ensure_playwright_available()
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    _delivery_name, out_dir = safe_delivery_dir_from_args(args)
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2

    ensure_folders(rows, out_dir)
    write_delivery_manifests(rows, out_dir)
    write_companion_asset_manifests(rows, columns, out_dir)
    state = load_status(out_dir)
    profile_dir = Path(args.profile_dir).expanduser()
    profile_dir.mkdir(parents=True, exist_ok=True)
    selected = rows[: args.limit] if args.limit else rows

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            accept_downloads=True,
            downloads_path=str(out_dir / "_browser_downloads_tmp"),
        )
        page = context.pages[0] if context.pages else context.new_page()
        if args.login_url:
            page.goto(args.login_url, wait_until="domcontentloaded", timeout=args.nav_timeout * 1000)
        if args.pause_for_login:
            print("\nBrowser is open. Log into the studio if needed, then return here and press Enter.")
            input("Press Enter to start downloads...")

        counts = {"downloaded": 0, "skipped": 0, "failed": 0}
        for index, row in enumerate(selected, start=1):
            dst = target_for(out_dir, row)
            if dst.exists() and dst.stat().st_size > 0 and not args.overwrite:
                counts["skipped"] += 1
                print(f"[{index}/{len(selected)}] skip existing {dst}")
                continue

            print(f"[{index}/{len(selected)}] browser download {row.filename} -> {row.folder_name}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                download, method = trigger_browser_download(page, row, args.download_timeout * 1000)
                download.save_as(str(dst))
                if dst.exists() and dst.stat().st_size > 0:
                    state["rows"][row_key(row)] = {
                        "csv_row": row.source_row,
                        "scene_id": row.scene_id,
                        "dvd_title": row.dvd_title,
                        "scene_title": row.scene_title,
                        "filename": row.filename,
                        "target": str(dst),
                        "state": "browser_downloaded",
                        "error": None,
                        "method": method,
                        "updated_at": int(time.time()),
                    }
                    save_status(out_dir, state)
                    write_delivery_manifests(rows, out_dir)
                    counts["downloaded"] += 1
                else:
                    raise RuntimeError("download completed but target file is missing or empty")
            except PlaywrightTimeoutError as exc:
                counts["failed"] += 1
                print(f"  failed timeout: {exc}", file=sys.stderr)
                if args.stop_on_error:
                    break
            except Exception as exc:  # noqa: BLE001
                counts["failed"] += 1
                print(f"  failed: {exc}", file=sys.stderr)
                if args.stop_on_error:
                    break

        context.close()

    print(
        "browser download complete: "
        f"downloaded={counts['downloaded']} skipped={counts['skipped']} failed={counts['failed']}"
    )
    return 1 if counts["failed"] else 0


def render_queue_html(rows: list[ManifestRow], delivery_name: str, out_dir: Path) -> str:
    generated = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        f"<title>{safe_name(delivery_name, 'Delivery')} Download Queue</title>",
        "<style>",
        "body{font:14px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:24px;color:#17202a}",
        "table{border-collapse:collapse;width:100%;font-size:13px}",
        "th,td{border-bottom:1px solid #dce1e7;padding:8px;text-align:left;vertical-align:top}",
        "th{font-size:11px;text-transform:uppercase;color:#657180}",
        ".mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}",
        ".folder{font-weight:650}",
        "a{color:#1f5fbf}",
        "</style></head><body>",
        f"<h1>{delivery_name} Download Queue</h1>",
        f"<p>Generated {generated}. Right-click each URL in the studio browser/session and save the file. Put downloaded files in the intake folder, then run the import command.</p>",
        f"<p class='mono'>Intake: {DEFAULT_INTAKE_DIR}<br>Delivery output: {out_dir}</p>",
        "<table><thead><tr><th>Done</th><th>CSV Row</th><th>DVD Folder</th><th>Scene</th><th>Expected File</th><th>Studio URL</th></tr></thead><tbody>",
    ]
    for row in rows:
        scene = f"{row.scene_id} - {row.scene_title}".strip(" -")
        lines.append(
            "<tr>"
            "<td><input type='checkbox'></td>"
            f"<td>{row.source_row}</td>"
            f"<td class='folder'>{html_escape(row.folder_name)}</td>"
            f"<td>{html_escape(scene)}</td>"
            f"<td class='mono'>{html_escape(row.filename)}</td>"
            f"<td class='mono'><a href='{html_escape(row.video_url)}'>{html_escape(row.video_url)}</a></td>"
            "</tr>"
        )
    lines.extend(["</tbody></table>", "</body></html>"])
    return "\n".join(lines)


def html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def render_review_html(
    rows: list[ManifestRow],
    columns: dict[str, str],
    delivery_name: str,
    out_dir: Path,
) -> str:
    assets = companion_assets(rows, columns)
    expected_assets = expected_companion_rows(rows, out_dir)
    state = load_status(out_dir)
    failed_by_row = {
        int(item.get("csv_row")): item
        for item in state.get("rows", {}).values()
        if str(item.get("csv_row", "")).isdigit() and item.get("state") == "failed"
    }
    linked_assets_missing = [
        asset for asset in assets
        if not (companion_target(out_dir, asset).exists() and companion_target(out_dir, asset).stat().st_size > 0)
    ]
    missing_expected = [item for item in expected_assets if item.get("status") != "present"]

    scene_rows: list[dict[str, str]] = []
    for row in rows:
        target = target_for(out_dir, row)
        present = target.exists() and target.stat().st_size > 0
        failure = failed_by_row.get(row.source_row, {})
        if present:
            status = "OK"
            note = ""
        elif failure:
            status = "MISSING SCENE FILE"
            note = str(failure.get("error", "failed download"))
        elif not row.video_url.lower().startswith(("http://", "https://")):
            status = "MISSING SCENE FILE"
            note = "No usable video link in spreadsheet"
        else:
            status = "MISSING SCENE FILE"
            note = "Expected file is not present in delivery folder"
        scene_rows.append(
            {
                "csv_row": str(row.source_row),
                "status": status,
                "folder": row.folder_name,
                "scene_id": row.scene_id,
                "scene_title": row.scene_title,
                "file": row.filename,
                "note": note,
            }
        )

    missing_scene_rows = [item for item in scene_rows if item["status"] != "OK"]
    asset_rows: list[dict[str, str]] = []
    for asset in linked_assets_missing:
        asset_rows.append(
            {
                "csv_row": str(asset.source_row),
                "status": "MISSING 2257/ASSET FILE",
                "folder": asset.folder_name,
                "scene_id": asset.scene_id,
                "scene_title": asset.scene_title,
                "file": asset.relative_path,
                "note": f"Spreadsheet linked {asset.kind} URL, but file is not present.",
            }
        )
    for item in missing_expected:
        asset_rows.append(
            {
                "csv_row": "",
                "status": "MISSING 2257/ASSET FILE",
                "folder": item.get("delivery_folder", ""),
                "scene_id": item.get("scene_id", ""),
                "scene_title": item.get("scene_title", ""),
                "file": item.get("expected", ""),
                "note": item.get("note", ""),
            }
        )

    def table(headers: list[str], body: list[dict[str, str]], empty: str) -> list[str]:
        lines = ["<table>", "<thead><tr>"]
        for header in headers:
            lines.append(f"<th>{html_escape(header)}</th>")
        lines.append("</tr></thead><tbody>")
        for item in body:
            status = item.get("status", "")
            cls = "ok"
            if "MISSING SCENE" in status:
                cls = "missing-scene"
            elif "MISSING 2257" in status or "missing" in status.lower():
                cls = "missing-asset"
            lines.append(f"<tr class='{cls}'>")
            for header in headers:
                key = header.lower().replace(" ", "_")
                lines.append(f"<td>{html_escape(item.get(key, ''))}</td>")
            lines.append("</tr>")
        if not body:
            lines.append(f"<tr><td colspan='{len(headers)}'>{html_escape(empty)}</td></tr>")
        lines.append("</tbody></table>")
        return lines

    headers = ["CSV Row", "Status", "Folder", "Scene ID", "Scene Title", "File", "Note"]
    lines = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        f"<title>{html_escape(delivery_name)} Amy Review</title>",
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;color:#1f2937}",
        "h1{font-size:24px;margin:0 0 8px}",
        "h2{font-size:18px;margin:28px 0 8px}",
        ".summary{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}",
        ".pill{border:1px solid #d1d5db;border-radius:8px;padding:10px 12px;background:#f9fafb}",
        "table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 24px}",
        "th,td{border:1px solid #d1d5db;padding:8px;vertical-align:top;text-align:left}",
        "th{background:#111827;color:white;position:sticky;top:0}",
        "tr.ok{background:#dcfce7}",
        "tr.missing-scene{background:#fecaca}",
        "tr.missing-asset{background:#fef3c7}",
        ".red{background:#fecaca}.yellow{background:#fef3c7}.green{background:#dcfce7}",
        "</style></head><body>",
        f"<h1>{html_escape(delivery_name)} Amy Review</h1>",
        f"<p>Generated {html_escape(datetime.now().strftime('%Y-%m-%d %H:%M'))}. Red means missing scene video. Yellow means missing 2257/compliance/asset coverage. Green means scene video present.</p>",
        "<div class='summary'>",
        f"<div class='pill green'>Scene files present: {len(scene_rows) - len(missing_scene_rows)} / {len(scene_rows)}</div>",
        f"<div class='pill red'>Missing scene files: {len(missing_scene_rows)}</div>",
        f"<div class='pill yellow'>Missing 2257/assets checks: {len(asset_rows)}</div>",
        f"<div class='pill'>Spreadsheet companion URLs found: {len(assets)}</div>",
        "</div>",
        "<h2>Missing Scene Files</h2>",
    ]
    lines.extend(table(headers, missing_scene_rows, "No missing scene files."))
    lines.append("<h2>Missing 2257 / Compliance / Assets</h2>")
    lines.extend(table(headers, asset_rows, "No missing 2257/compliance/asset checks."))
    lines.append("<h2>All Scene Rows</h2>")
    lines.extend(table(headers, scene_rows, "No scene rows."))
    lines.append("</body></html>")
    return "\n".join(lines)


def cmd_review(args: argparse.Namespace) -> int:
    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    delivery_name, out_dir = safe_delivery_dir_from_args(args)
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    write_delivery_manifests(rows, out_dir)
    write_companion_asset_manifests(rows, columns, out_dir)
    review_path = out_dir / "_AMY_REVIEW.html"
    review_path.write_text(
        render_review_html(rows, columns, delivery_name, out_dir),
        encoding="utf-8",
    )
    print(f"review: {review_path}")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    delivery_name, out_dir = safe_delivery_dir_from_args(args)
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    write_delivery_manifests(rows, out_dir)
    write_companion_asset_manifests(rows, columns, out_dir)
    queue_path = out_dir / "_download_queue.html"
    queue_path.write_text(render_queue_html(rows, delivery_name, out_dir), encoding="utf-8")
    print(queue_path)
    return 0


def candidate_files(source_dir: Path) -> list[Path]:
    if not source_dir.exists():
        return []
    files = []
    for path in source_dir.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.suffix.lower() in {".crdownload", ".download", ".part", ".tmp"}:
            continue
        files.append(path)
    return sorted(files)


def is_stable_file(path: Path, settle_seconds: int) -> bool:
    try:
        first_size = path.stat().st_size
        first_mtime = path.stat().st_mtime
        if first_size <= 0:
            return False
        if time.time() - first_mtime < settle_seconds:
            return False
        time.sleep(0.5)
        second_size = path.stat().st_size
        return first_size == second_size
    except OSError:
        return False


def cmd_import(args: argparse.Namespace) -> int:
    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    _delivery_name, out_dir = safe_delivery_dir_from_args(args)
    source_dir = Path(args.source_dir).expanduser()
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2
    ensure_folders(rows, out_dir)
    write_delivery_manifests(rows, out_dir)
    write_companion_asset_manifests(rows, columns, out_dir)
    by_filename = {row.filename.lower(): row for row in rows}
    state = load_status(out_dir)
    counts = {"moved": 0, "skipped": 0, "unmatched": 0}
    unmatched_dir = source_dir / "_unmatched"
    for src in candidate_files(source_dir):
        row = by_filename.get(src.name.lower())
        if not row:
            counts["unmatched"] += 1
            if args.move_unmatched:
                unmatched_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(unmatched_dir / src.name))
            else:
                print(f"unmatched: {src.name}")
            continue
        dst = target_for(out_dir, row)
        if dst.exists() and dst.stat().st_size > 0:
            counts["skipped"] += 1
            print(f"skip existing: {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if args.copy:
            shutil.copy2(src, dst)
        else:
            shutil.move(str(src), str(dst))
        key = row_key(row)
        state["rows"][key] = {
            "csv_row": row.source_row,
            "scene_id": row.scene_id,
            "dvd_title": row.dvd_title,
            "scene_title": row.scene_title,
            "filename": row.filename,
            "target": str(dst),
            "state": "imported",
            "error": None,
            "updated_at": int(time.time()),
        }
        counts["moved"] += 1
        print(f"{'copied' if args.copy else 'moved'}: {src.name} -> {dst.parent.name}/")
    save_status(out_dir, state)
    write_delivery_manifests(rows, out_dir)
    write_companion_asset_manifests(rows, columns, out_dir)
    print(f"import complete: moved={counts['moved']} skipped={counts['skipped']} unmatched={counts['unmatched']}")
    return 0 if counts["unmatched"] == 0 else 1


def cmd_watch_import(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).expanduser()
    source_dir.mkdir(parents=True, exist_ok=True)
    print(f"watching intake: {source_dir}")
    print("press Ctrl-C to stop")
    try:
        while True:
            ready = [
                path for path in candidate_files(source_dir)
                if is_stable_file(path, args.settle_seconds)
            ]
            if ready:
                cmd_import(args)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


def cmd_report(args: argparse.Namespace) -> int:
    rows, _columns, missing = load_manifest(Path(args.manifest).expanduser())
    _delivery_name, out_dir = safe_delivery_dir_from_args(args)
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2
    state = load_status(out_dir)
    missing_files: list[ManifestRow] = []
    present = 0
    for row in rows:
        if target_for(out_dir, row).exists():
            present += 1
        else:
            missing_files.append(row)
    failed = [
        item for item in state.get("rows", {}).values()
        if item.get("state") == "failed"
    ]
    print(f"local delivery: {out_dir}")
    print(f"manifest rows: {len(rows)}")
    print(f"files present: {present}")
    print(f"files missing: {len(missing_files)}")
    print(f"failed downloads: {len(failed)}")
    if missing_files:
        print("\nmissing:")
        for row in missing_files[: args.limit]:
            print(f"  CSV row {row.source_row}: {row.folder_name}/{row.filename}")
        if len(missing_files) > args.limit:
            print(f"  ... {len(missing_files) - args.limit} more")
    if failed:
        print("\nfailed:")
        for item in failed[: args.limit]:
            print(f"  CSV row {item.get('csv_row')}: {item.get('filename')} :: {item.get('error')}")
    return 1 if missing_files or failed else 0


def cmd_audit_data(args: argparse.Namespace) -> int:
    rows, _columns, missing = load_manifest(Path(args.manifest).expanduser())
    _delivery_name, out_dir = safe_delivery_dir_from_args(args)
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2

    audit_path = out_dir / "_data_audit.csv"
    fields = [
        "csv_row",
        "dvd_title",
        "delivery_folder",
        "scene_id",
        "scene_title",
        "expected_filename",
        "relative_path",
        "file_present",
        "file_size_bytes",
        "root_manifest_present",
        "folder_manifest_present",
        "folder_spreadsheet_present",
        "status",
    ]
    problems = 0
    root_manifest = out_dir / "_source_spreadsheet_with_paths.csv"
    with audit_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            target = target_for(out_dir, row)
            folder_dir = target.parent
            folder_manifest = folder_dir / "_folder_manifest.csv"
            folder_spreadsheet = folder_dir / "_folder_spreadsheet_rows.csv"
            present = target.exists() and target.stat().st_size > 0
            status = "ok"
            if not present:
                status = "missing_file"
            elif not root_manifest.exists() or not folder_manifest.exists() or not folder_spreadsheet.exists():
                status = "missing_metadata"
            if status != "ok":
                problems += 1
            writer.writerow(
                {
                    "csv_row": row.source_row,
                    "dvd_title": row.dvd_title,
                    "delivery_folder": row.folder_name,
                    "scene_id": row.scene_id,
                    "scene_title": row.scene_title,
                    "expected_filename": row.filename,
                    "relative_path": str(target.relative_to(out_dir)),
                    "file_present": "yes" if present else "no",
                    "file_size_bytes": target.stat().st_size if target.exists() else 0,
                    "root_manifest_present": "yes" if root_manifest.exists() else "no",
                    "folder_manifest_present": "yes" if folder_manifest.exists() else "no",
                    "folder_spreadsheet_present": "yes" if folder_spreadsheet.exists() else "no",
                    "status": status,
                }
            )

    folder_counts: dict[str, int] = {}
    for row in rows:
        folder_counts[row.folder_name] = folder_counts.get(row.folder_name, 0) + 1
    print(f"audit: {audit_path}")
    print(f"rows audited: {len(rows)}")
    print(f"folders audited: {len(folder_counts)}")
    print(f"problems: {problems}")
    for folder in sorted(folder_counts):
        mp4_count = len(list((out_dir / folder).glob("*.mp4")))
        print(f"  {folder}: spreadsheet_rows={folder_counts[folder]} mp4_files={mp4_count}")
        if mp4_count != folder_counts[folder]:
            problems += 1
    return 1 if problems else 0


def cmd_audit_assets(args: argparse.Namespace) -> int:
    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    _delivery_name, out_dir = safe_delivery_dir_from_args(args)
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    assets = write_companion_asset_manifests(rows, columns, out_dir)
    expected = expected_companion_rows(rows, out_dir)

    audit_path = out_dir / "_asset_audit.csv"
    fields = [
        "scope",
        "delivery_folder",
        "scene_id",
        "scene_title",
        "expected",
        "status",
        "matched_files",
        "note",
    ]
    with audit_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(expected)

    missing_expected = [row for row in expected if row["status"] != "present"]
    missing_linked = [
        asset for asset in assets
        if not (companion_target(out_dir, asset).exists() and companion_target(out_dir, asset).stat().st_size > 0)
    ]
    print(f"asset audit: {audit_path}")
    print(f"spreadsheet companion URLs: {len(assets)}")
    print(f"linked companion files missing: {len(missing_linked)}")
    print(f"Delivery 1-style expected asset checks: {len(expected)}")
    print(f"Delivery 1-style missing checks: {len(missing_expected)}")
    if missing_expected:
        print("\nmissing examples:")
        for item in missing_expected[: args.limit]:
            scene = f" / {item['scene_title']}" if item["scene_title"] else ""
            print(f"  {item['delivery_folder']}{scene}: {item['expected']}")
        if len(missing_expected) > args.limit:
            print(f"  ... {len(missing_expected) - args.limit} more")
    return 1 if missing_linked or missing_expected else 0


def cmd_audit_cloud_layout(args: argparse.Namespace) -> int:
    rows, _columns, missing = load_manifest(Path(args.manifest).expanduser())
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2

    delivery_name = effective_delivery_name(args)
    remote_delivery = remote_target(args.remote, args.remote_base, delivery_name)
    try:
        remote_files = remote_existing_files(remote_delivery)
    except RuntimeError as exc:
        print(f"cloud layout audit failed: {exc}", file=sys.stderr)
        return 1

    expected = {f"{row.folder_name}/{row.filename}": row for row in rows if row.video_url}
    remote_mp4 = sorted(path for path in remote_files if path.lower().endswith(".mp4"))
    root_mp4 = sorted(path for path in remote_mp4 if "/" not in path)
    unexpected_mp4 = sorted(path for path in remote_mp4 if path not in expected)
    missing_mp4 = sorted(path for path in expected if path not in remote_files)

    _delivery_name, out_dir = safe_delivery_dir_from_args(args)
    reports_dir = out_dir / "_cloud_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    audit_path = reports_dir / f"{safe_name(delivery_name, 'Delivery')}_cloud_layout_audit.csv"
    fields = ["status", "relative_path", "csv_row", "delivery_folder", "scene_title"]
    with audit_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for path in missing_mp4:
            row = expected[path]
            writer.writerow(
                {
                    "status": "missing_expected_mp4",
                    "relative_path": path,
                    "csv_row": row.source_row,
                    "delivery_folder": row.folder_name,
                    "scene_title": row.scene_title,
                }
            )
        for path in root_mp4:
            writer.writerow({"status": "loose_root_mp4", "relative_path": path})
        for path in unexpected_mp4:
            if path not in root_mp4:
                writer.writerow({"status": "unexpected_mp4", "relative_path": path})

    by_folder_expected: dict[str, int] = {}
    by_folder_present: dict[str, int] = {}
    for row in rows:
        by_folder_expected[row.folder_name] = by_folder_expected.get(row.folder_name, 0) + 1
    for path in remote_mp4:
        if "/" in path:
            folder = path.split("/", 1)[0]
            by_folder_present[folder] = by_folder_present.get(folder, 0) + 1

    print(f"cloud layout audit: {audit_path}")
    print(f"remote delivery: {remote_delivery}")
    print(f"expected mp4: {len(expected)}")
    print(f"remote mp4: {len(remote_mp4)}")
    print(f"missing expected mp4: {len(missing_mp4)}")
    print(f"loose root mp4: {len(root_mp4)}")
    print(f"unexpected mp4: {len(unexpected_mp4)}")
    for folder in sorted(by_folder_expected):
        print(f"  {folder}: expected={by_folder_expected[folder]} present={by_folder_present.get(folder, 0)}")
    return 1 if missing_mp4 or root_mp4 or unexpected_mp4 else 0


def ffprobe_json(source: str, timeout: int) -> dict:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,time_base,sample_rate,channels,channel_layout",
        "-of",
        "json",
        source,
    ]
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return json.loads(result.stdout or "{}")


def primary_streams(probe: dict) -> tuple[dict, dict]:
    video: dict = {}
    audio: dict = {}
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video" and not video:
            video = stream
        elif stream.get("codec_type") == "audio" and not audio:
            audio = stream
    return video, audio


def spec_signature(video: dict, audio: dict) -> str:
    return "|".join(
        [
            str(video.get("codec_name", "")),
            str(video.get("width", "")),
            str(video.get("height", "")),
            str(video.get("avg_frame_rate") or video.get("r_frame_rate", "")),
            str(video.get("time_base", "")),
            str(audio.get("codec_name", "")),
            str(audio.get("sample_rate", "")),
            str(audio.get("channels", "")),
            str(audio.get("channel_layout", "")),
        ]
    )


def cmd_audit_video_specs(args: argparse.Namespace) -> int:
    rows, _columns, missing = load_manifest(Path(args.manifest).expanduser())
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2

    delivery_name = effective_delivery_name(args)
    selected = rows[: args.limit] if args.limit else rows
    _delivery_name, out_dir = safe_delivery_dir_from_args(args)
    reports_dir = out_dir / "_cloud_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    audit_path = reports_dir / f"{safe_name(delivery_name, 'Delivery')}_video_spec_audit.csv"
    fields = [
        "csv_row",
        "delivery_folder",
        "filename",
        "scene_title",
        "status",
        "video_codec",
        "width",
        "height",
        "avg_frame_rate",
        "r_frame_rate",
        "time_base",
        "audio_codec",
        "sample_rate",
        "channels",
        "channel_layout",
        "signature",
        "error",
    ]
    output_rows: list[dict[str, str]] = []
    signatures_by_folder: dict[str, set[str]] = {}
    problems = 0
    for index, row in enumerate(selected, start=1):
        target = target_for(out_dir, row)
        source = str(target) if args.local and target.exists() else row.video_url
        print(f"[{index}/{len(selected)}] ffprobe {row.folder_name}/{row.filename}")
        status = "ok"
        error = ""
        video: dict = {}
        audio: dict = {}
        signature = ""
        try:
            probe = ffprobe_json(source, args.timeout)
            video, audio = primary_streams(probe)
            signature = spec_signature(video, audio)
            if not video:
                status = "missing_video_stream"
        except Exception as exc:
            status = "probe_failed"
            error = str(exc)[:1000]
        if status == "ok":
            signatures_by_folder.setdefault(row.folder_name, set()).add(signature)
        else:
            problems += 1
        output_rows.append(
            {
                "csv_row": row.source_row,
                "delivery_folder": row.folder_name,
                "filename": row.filename,
                "scene_title": row.scene_title,
                "status": status,
                "video_codec": video.get("codec_name", ""),
                "width": video.get("width", ""),
                "height": video.get("height", ""),
                "avg_frame_rate": video.get("avg_frame_rate", ""),
                "r_frame_rate": video.get("r_frame_rate", ""),
                "time_base": video.get("time_base", ""),
                "audio_codec": audio.get("codec_name", ""),
                "sample_rate": audio.get("sample_rate", ""),
                "channels": audio.get("channels", ""),
                "channel_layout": audio.get("channel_layout", ""),
                "signature": signature,
                "error": error,
            }
        )

    mismatched_folders = {folder for folder, sigs in signatures_by_folder.items() if len(sigs) > 1}
    for item in output_rows:
        if item["delivery_folder"] in mismatched_folders and item["status"] == "ok":
            item["status"] = "folder_spec_mismatch"
    problems += len(mismatched_folders)

    with audit_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"video spec audit: {audit_path}")
    print(f"rows audited: {len(output_rows)}")
    print(f"folders audited: {len(signatures_by_folder)}")
    print(f"folders with mismatched specs: {len(mismatched_folders)}")
    for folder in sorted(mismatched_folders):
        print(f"  mismatch: {folder}")
    return 1 if problems else 0


def remote_target(remote: str, remote_base: str, delivery_name: str) -> str:
    prefix = remote if remote.endswith(":") else remote + ":"
    parts = [p.strip("/") for p in (remote_base, safe_name(delivery_name, "Delivery")) if p]
    return prefix + "/".join(parts)


def remote_path_for_row(remote: str, remote_base: str, delivery_name: str, row: ManifestRow) -> str:
    return remote_target(remote, remote_base, delivery_name) + "/" + row.folder_name


def remote_existing_files(remote_delivery: str) -> set[str]:
    cmd = ["rclone", "lsf", remote_delivery, "--recursive", "--files-only"]
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode:
        message = (result.stderr or result.stdout).strip()
        if "directory not found" in message.lower() or "object not found" in message.lower():
            return set()
        raise RuntimeError(message or f"rclone lsf failed with {result.returncode}")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def remote_lsf(target: str, *args: str) -> list[str]:
    cmd = ["rclone", "lsf", target, *args]
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode:
        message = (result.stderr or result.stdout).strip()
        if "directory not found" in message.lower() or "object not found" in message.lower():
            return []
        raise RuntimeError(message or f"rclone lsf failed with {result.returncode}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def generated_or_media_name(path: str) -> bool:
    name = Path(path.rstrip("/")).name
    lower = name.lower()
    return (
        lower.endswith(".mp4")
        or ".mp4." in lower and lower.endswith(".part")
        or name.startswith("_folder_")
        or name.startswith("_delivery_")
        or name.startswith("_source_spreadsheet")
        or name.startswith("_companion")
        or name.startswith("_AMY_REVIEW")
    )


def looks_like_dvd_title_folder(path: str) -> bool:
    name = Path(path.rstrip("/")).name
    return bool(re.search(r"\bVol\.\s*\d+\b", name))


def cmd_audit_drive_roots(args: argparse.Namespace) -> int:
    root = args.remote if args.remote.endswith(":") else args.remote + ":"
    remote_base = args.remote_base.strip("/")
    base = root + remote_base if remote_base else root
    problems: list[tuple[str, str]] = []
    try:
        for item in remote_lsf(root, "--max-depth", "1", "--files-only"):
            if generated_or_media_name(item):
                problems.append(("drive_root_loose_file", item))
        for item in remote_lsf(root, "--max-depth", "1", "--dirs-only"):
            if looks_like_dvd_title_folder(item):
                problems.append(("drive_root_loose_title_folder", item))
        for item in remote_lsf(base, "--max-depth", "1", "--files-only"):
            if generated_or_media_name(item):
                problems.append(("delivery_root_loose_file", item))
        for item in remote_lsf(base, "--max-depth", "1", "--dirs-only"):
            if looks_like_dvd_title_folder(item):
                problems.append(("delivery_root_loose_title_folder", item))
    except RuntimeError as exc:
        print(f"drive root audit failed: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir).expanduser()
    reports_dir = out_dir / "_cloud_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    audit_path = reports_dir / "drive_root_hygiene_audit.csv"
    with audit_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["status", "relative_path"])
        writer.writeheader()
        for status, path in problems:
            writer.writerow({"status": status, "relative_path": path})

    print(f"drive root audit: {audit_path}")
    print(f"remote root: {root}")
    print(f"delivery root: {base}")
    print(f"loose generated/media files or title folders: {len(problems)}")
    for status, path in problems[: args.limit]:
        print(f"  {status}: {path}")
    if len(problems) > args.limit:
        print(f"  ... {len(problems) - args.limit} more")
    return 1 if problems else 0


def write_cloud_failure_report(
    rows: list[dict[str, str]],
    delivery_name: str,
    out_dir: Path,
) -> Path:
    reports_dir = out_dir / "_cloud_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"{safe_name(delivery_name, 'Delivery')}_cloud_failures_{stamp}.csv"
    fields = ["csv_row", "delivery_folder", "filename", "remote_path", "return_code", "error"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_cloud_transfer_report(
    rows: list[dict[str, str]],
    delivery_name: str,
    out_dir: Path,
) -> Path:
    reports_dir = out_dir / "_cloud_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"{safe_name(delivery_name, 'Delivery')}_cloud_transfers_{stamp}.csv"
    fields = [
        "csv_row",
        "delivery_folder",
        "filename",
        "remote_path",
        "return_code",
        "seconds",
        "status",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def cmd_cloud_plan(args: argparse.Namespace) -> int:
    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2
    folders = sorted({row.folder_name for row in rows})
    print(f"remote delivery: {remote_target(args.remote, args.remote_base, effective_delivery_name(args))}")
    print_summary(rows, Path("(cloud)"), columns)
    print("\nremote folders:")
    for folder in folders:
        print(f"  {remote_target(args.remote, args.remote_base, effective_delivery_name(args))}/{folder}")
        print(f"  {remote_target(args.remote, args.remote_base, effective_delivery_name(args))}/{folder}/2257")
    return 0


def cmd_cloud_mkdirs(args: argparse.Namespace) -> int:
    rows, _columns, missing = load_manifest(Path(args.manifest).expanduser())
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2
    folders = sorted({row.folder_name for row in rows})
    for folder in folders:
        for suffix in ("", "/2257"):
            target = remote_target(args.remote, args.remote_base, effective_delivery_name(args)) + "/" + folder + suffix
            cmd = ["rclone", "mkdir", target]
            if args.dry_run:
                print(" ".join(cmd))
            else:
                print("running:", " ".join(cmd))
                rc = subprocess.call(cmd)
                if rc:
                    return rc
    return 0


def cmd_cloud_copyurls(args: argparse.Namespace) -> int:
    rows, columns, missing = load_manifest(Path(args.manifest).expanduser())
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2
    selected = rows[: args.limit] if args.limit else rows
    delivery_name, out_dir = safe_delivery_dir_from_args(args)
    remote_delivery = remote_target(args.remote, args.remote_base, delivery_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_delivery_manifests(rows, out_dir)
    write_companion_asset_manifests(rows, columns, out_dir)
    (out_dir / "_AMY_REVIEW.html").write_text(
        render_review_html(rows, columns, delivery_name, out_dir),
        encoding="utf-8",
    )
    if not args.dry_run and args.upload_metadata:
        meta_cmd = [
            "rclone",
            "copy",
            str(out_dir),
            remote_delivery,
            "--create-empty-src-dirs",
            "--filter",
            "+ **/",
            "--filter",
            "+ *.csv",
            "--filter",
            "+ *.html",
            "--filter",
            "- *",
            "--tpslimit",
            str(args.tpslimit),
            "--tpslimit-burst",
            str(args.tpslimit_burst),
        ]
        print("upload metadata package:", " ".join(meta_cmd))
        meta_rc = subprocess.call(meta_cmd)
        if meta_rc:
            return meta_rc

    existing: set[str] = set()
    if args.skip_existing and not args.dry_run:
        try:
            existing = remote_existing_files(remote_delivery)
            print(f"remote resume scan: {len(existing)} existing files under {remote_delivery}")
        except RuntimeError as exc:
            print(f"warning: remote resume scan failed: {exc}", file=sys.stderr)
            if args.require_resume_scan:
                return 1

    failures: list[dict[str, str]] = []
    transfer_rows: list[dict[str, str]] = []
    skipped = 0
    copied = 0

    def build_copy_cmd(row: ManifestRow) -> tuple[str, list[str]]:
        remote_folder = remote_path_for_row(args.remote, args.remote_base, delivery_name, row)
        remote_file = remote_folder + "/" + row.filename
        cmd = [
            "rclone",
            "copyurl",
            row.video_url,
            remote_file,
            "--auto-filename=false",
            "--retries",
            str(args.retries),
            "--low-level-retries",
            str(args.low_level_retries),
            "--drive-chunk-size",
            args.drive_chunk_size,
            "--tpslimit",
            str(args.tpslimit),
            "--tpslimit-burst",
            str(args.tpslimit_burst),
            "--stats",
            args.stats,
            "--stats-one-line",
        ]
        if args.disable_http2:
            cmd.append("--disable-http2")
        return remote_file, cmd

    pending: list[tuple[int, ManifestRow, str, list[str]]] = []
    for index, row in enumerate(selected, start=1):
        relative_file = f"{row.folder_name}/{row.filename}"
        if args.skip_existing and relative_file in existing:
            skipped += 1
            print(f"[{index}/{len(selected)}] skip existing -> {relative_file}")
            continue
        if not row.video_url:
            print(f"[{index}/{len(selected)}] missing video URL -> row {row.source_row} {relative_file}", file=sys.stderr)
            failures.append(
                {
                    "csv_row": str(row.source_row),
                    "delivery_folder": row.folder_name,
                    "filename": row.filename,
                    "remote_path": remote_path_for_row(args.remote, args.remote_base, delivery_name, row) + "/" + row.filename,
                    "return_code": "no_url",
                    "error": "The manifest row has no video URL.",
                }
            )
            if args.stop_on_error:
                break
            continue

        remote_file, cmd = build_copy_cmd(row)
        if args.dry_run:
            print(" ".join(cmd))
            continue
        pending.append((index, row, remote_file, cmd))

    if args.dry_run:
        if failures:
            print(f"cloud copyurl dry-run found failures: failed={len(failures)}")
            return 1
        return 0

    def run_one(
        item: tuple[int, ManifestRow, str, list[str]]
    ) -> tuple[int, ManifestRow, str, subprocess.CompletedProcess[str], float]:
        index, row, remote_file, cmd = item
        started = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=args.transfer_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            result = subprocess.CompletedProcess(
                cmd,
                124,
                stdout=exc.stdout or "",
                stderr=f"transfer timed out after {args.transfer_timeout}s",
            )
        elapsed = time.monotonic() - started
        return index, row, remote_file, result, elapsed

    if args.parallel <= 1:
        completed_iter: Iterable[tuple[int, ManifestRow, str, subprocess.CompletedProcess[str], float]] = []
        sequential_results: list[tuple[int, ManifestRow, str, subprocess.CompletedProcess[str], float]] = []
        for item_index, item in enumerate(pending, start=1):
            index, _row, remote_file, _cmd = item
            print(f"[{index}/{len(selected)}] cloud copyurl -> {remote_file}")
            completed = run_one(item)
            print(f"[{index}/{len(selected)}] finished in {completed[4]:.1f}s -> {remote_file}")
            sequential_results.append(completed)
            if args.sleep_between > 0 and item_index < len(pending):
                time.sleep(args.sleep_between)
        completed_iter = sequential_results
    else:
        print(f"cloud copyurl workers: {args.parallel} parallel transfers")
        completed: list[tuple[int, ManifestRow, str, subprocess.CompletedProcess[str], float]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = []
            for submit_index, item in enumerate(pending, start=1):
                index, _row, remote_file, _cmd = item
                print(f"[{index}/{len(selected)}] queue copyurl -> {remote_file}")
                futures.append(pool.submit(run_one, item))
                if args.sleep_between > 0 and submit_index < len(pending):
                    time.sleep(args.sleep_between)
            for future in concurrent.futures.as_completed(futures):
                result_item = future.result()
                completed.append(result_item)
                index, _row, remote_file, result, elapsed = result_item
                status = "ok" if result.returncode == 0 else "failed"
                print(f"[{index}/{len(selected)}] {status} in {elapsed:.1f}s -> {remote_file}")
                if args.stop_on_error and completed[-1][3].returncode:
                    break
        completed_iter = sorted(completed, key=lambda item: item[0])

    for index, row, remote_file, result, elapsed in completed_iter:
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        rc = result.returncode
        transfer_rows.append(
            {
                "csv_row": str(row.source_row),
                "delivery_folder": row.folder_name,
                "filename": row.filename,
                "remote_path": remote_file,
                "return_code": str(rc),
                "seconds": f"{elapsed:.1f}",
                "status": "ok" if rc == 0 else "failed",
            }
        )
        if rc:
            print(f"failed: CSV row {row.source_row} {row.filename}", file=sys.stderr)
            failures.append(
                {
                    "csv_row": str(row.source_row),
                    "delivery_folder": row.folder_name,
                    "filename": row.filename,
                    "remote_path": remote_file,
                    "return_code": str(rc),
                    "error": (result.stderr or result.stdout).strip()[-1000:],
                }
            )
            if args.stop_on_error:
                break
        else:
            copied += 1
            existing.add(f"{row.folder_name}/{row.filename}")

    if failures:
        transfer_report = write_cloud_transfer_report(
            transfer_rows,
            delivery_name,
            Path(args.out_dir).expanduser(),
        )
        report = write_cloud_failure_report(
            failures,
            delivery_name,
            Path(args.out_dir).expanduser(),
        )
        print(f"cloud copyurl complete with failures: copied={copied} skipped={skipped} failed={len(failures)}")
        print(f"transfer report: {transfer_report}")
        print(f"failure report: {report}")
        return 1
    transfer_report = write_cloud_transfer_report(
        transfer_rows,
        delivery_name,
        Path(args.out_dir).expanduser(),
    )
    print(f"cloud copyurl complete: copied={copied} skipped={skipped} failed=0")
    print(f"transfer report: {transfer_report}")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    rows, _columns, missing = load_manifest(Path(args.manifest).expanduser())
    delivery_name, out_dir = safe_delivery_dir_from_args(args)
    if missing:
        print(f"missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2
    absent = [row for row in rows if not target_for(out_dir, row).exists()]
    if absent and not args.allow_incomplete:
        print(f"upload stopped: {len(absent)} manifest files are missing", file=sys.stderr)
        print("run report for details, or pass --allow-incomplete", file=sys.stderr)
        return 1

    dst = remote_target(args.remote, args.remote_base, delivery_name)
    cmd = [
        "rclone",
        "copy",
        str(out_dir),
        dst,
        "--progress",
        "--stats-one-line",
        "--create-empty-src-dirs",
        "--exclude",
        "_delivery_status.json",
        "--exclude",
        "_download_queue.html",
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    print("running:", " ".join(cmd))
    return subprocess.call(cmd)


def cmd_link(args: argparse.Namespace) -> int:
    dst = remote_target(args.remote, args.remote_base, effective_delivery_name(args))
    cmd = ["rclone", "link", dst]
    print("running:", " ".join(cmd), file=sys.stderr)
    return subprocess.call(cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Amy DVD-folder deliveries from a manifest CSV.")
    parser.add_argument("--manifest", default=str(DEFAULT_ROOT / "manifest.csv"))
    parser.add_argument(
        "--delivery-name",
        default="",
        help="Defaults to the delivery name inferred from the manifest filename, e.g. 'Web VOD - Delivery 4.csv' -> 'Delivery 4'.",
    )
    parser.add_argument("--delivery-prefix", default="", help="Optional text before the inferred/provided delivery name.")
    parser.add_argument("--delivery-suffix", default="", help="Optional text after the inferred/provided delivery name.")
    parser.add_argument("--out-dir", default=str(DEFAULT_DELIVERIES_DIR))

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate").set_defaults(func=cmd_validate)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--allow-problems", action="store_true")
    prepare.set_defaults(func=cmd_prepare)

    download = sub.add_parser("download")
    download.add_argument("--timeout", type=int, default=120)
    download.add_argument("--allow-problems", action="store_true")
    download.set_defaults(func=cmd_download)

    download_assets = sub.add_parser("download-assets")
    download_assets.add_argument("--timeout", type=int, default=120)
    download_assets.add_argument("--limit", type=int, default=0)
    download_assets.add_argument("--stop-on-error", action="store_true")
    download_assets.set_defaults(func=cmd_download_assets)

    browser_download = sub.add_parser("browser-download")
    browser_download.add_argument("--profile-dir", default=str(DEFAULT_BROWSER_PROFILE_DIR))
    browser_download.add_argument("--login-url", default="")
    browser_download.add_argument("--pause-for-login", action="store_true")
    browser_download.add_argument("--limit", type=int, default=0)
    browser_download.add_argument("--overwrite", action="store_true")
    browser_download.add_argument("--stop-on-error", action="store_true")
    browser_download.add_argument("--nav-timeout", type=int, default=60)
    browser_download.add_argument("--download-timeout", type=int, default=300)
    browser_download.set_defaults(func=cmd_browser_download)

    queue = sub.add_parser("queue")
    queue.set_defaults(func=cmd_queue)

    importer = sub.add_parser("import")
    importer.add_argument("--source-dir", default=str(DEFAULT_INTAKE_DIR))
    importer.add_argument("--copy", action="store_true", help="Copy files instead of moving them.")
    importer.add_argument("--move-unmatched", action="store_true", help="Move unmatched files into _unmatched.")
    importer.set_defaults(func=cmd_import)

    watch_import = sub.add_parser("watch-import")
    watch_import.add_argument("--source-dir", default=str(DEFAULT_INTAKE_DIR))
    watch_import.add_argument("--copy", action="store_true", help="Copy files instead of moving them.")
    watch_import.add_argument("--move-unmatched", action="store_true", help="Move unmatched files into _unmatched.")
    watch_import.add_argument("--interval", type=int, default=5)
    watch_import.add_argument("--settle-seconds", type=int, default=10)
    watch_import.set_defaults(func=cmd_watch_import)

    report = sub.add_parser("report")
    report.add_argument("--limit", type=int, default=25)
    report.set_defaults(func=cmd_report)

    audit_data = sub.add_parser("audit-data")
    audit_data.set_defaults(func=cmd_audit_data)

    audit_assets = sub.add_parser("audit-assets")
    audit_assets.add_argument("--limit", type=int, default=25)
    audit_assets.set_defaults(func=cmd_audit_assets)

    audit_cloud_layout = sub.add_parser("audit-cloud-layout")
    audit_cloud_layout.add_argument("--remote", default=DEFAULT_REMOTE)
    audit_cloud_layout.add_argument("--remote-base", default=DEFAULT_REMOTE_BASE)
    audit_cloud_layout.set_defaults(func=cmd_audit_cloud_layout)

    audit_drive_roots = sub.add_parser("audit-drive-roots")
    audit_drive_roots.add_argument("--remote", default=DEFAULT_REMOTE)
    audit_drive_roots.add_argument("--remote-base", default=DEFAULT_REMOTE_BASE)
    audit_drive_roots.add_argument("--limit", type=int, default=25)
    audit_drive_roots.set_defaults(func=cmd_audit_drive_roots)

    audit_video_specs = sub.add_parser("audit-video-specs")
    audit_video_specs.add_argument("--limit", type=int, default=0)
    audit_video_specs.add_argument("--timeout", type=int, default=60)
    audit_video_specs.add_argument("--local", action="store_true")
    audit_video_specs.set_defaults(func=cmd_audit_video_specs)

    review = sub.add_parser("review")
    review.set_defaults(func=cmd_review)

    upload = sub.add_parser("upload")
    upload.add_argument("--remote", default=DEFAULT_REMOTE)
    upload.add_argument("--remote-base", default=DEFAULT_REMOTE_BASE)
    upload.add_argument("--dry-run", action="store_true")
    upload.add_argument("--allow-incomplete", action="store_true")
    upload.set_defaults(func=cmd_upload)

    cloud_plan = sub.add_parser("cloud-plan")
    cloud_plan.add_argument("--remote", default=DEFAULT_REMOTE)
    cloud_plan.add_argument("--remote-base", default=DEFAULT_REMOTE_BASE)
    cloud_plan.set_defaults(func=cmd_cloud_plan)

    cloud_mkdirs = sub.add_parser("cloud-mkdirs")
    cloud_mkdirs.add_argument("--remote", default=DEFAULT_REMOTE)
    cloud_mkdirs.add_argument("--remote-base", default=DEFAULT_REMOTE_BASE)
    cloud_mkdirs.add_argument("--dry-run", action="store_true")
    cloud_mkdirs.set_defaults(func=cmd_cloud_mkdirs)

    cloud_copyurls = sub.add_parser("cloud-copyurls")
    cloud_copyurls.add_argument("--remote", default=DEFAULT_REMOTE)
    cloud_copyurls.add_argument("--remote-base", default=DEFAULT_REMOTE_BASE)
    cloud_copyurls.add_argument("--dry-run", action="store_true")
    cloud_copyurls.add_argument("--limit", type=int, default=0)
    cloud_copyurls.add_argument("--stop-on-error", action="store_true")
    cloud_copyurls.add_argument(
        "--no-upload-metadata",
        dest="upload_metadata",
        action="store_false",
        help="Skip uploading root and per-folder CSV/HTML metadata package.",
    )
    cloud_copyurls.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Do not pre-scan the remote delivery folder for already-landed files.",
    )
    cloud_copyurls.add_argument(
        "--require-resume-scan",
        action="store_true",
        help="Stop if the remote existing-file scan fails instead of attempting all rows.",
    )
    cloud_copyurls.add_argument("--drive-chunk-size", default="128M")
    cloud_copyurls.add_argument("--retries", type=int, default=8)
    cloud_copyurls.add_argument("--low-level-retries", type=int, default=20)
    cloud_copyurls.add_argument("--tpslimit", type=float, default=4)
    cloud_copyurls.add_argument("--tpslimit-burst", type=int, default=4)
    cloud_copyurls.add_argument(
        "--allow-http2",
        dest="disable_http2",
        action="store_false",
        help="Allow HTTP/2. Default keeps HTTP/2 disabled because this workflow has seen reset-by-peer failures.",
    )
    cloud_copyurls.add_argument(
        "--sleep-between",
        type=float,
        default=2,
        help="Seconds to wait between files to avoid Drive API bursts.",
    )
    cloud_copyurls.add_argument("--stats", default="30s")
    cloud_copyurls.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Number of rclone copyurl transfers to run at once. Use 2-4 for faster cloud-only delivery builds.",
    )
    cloud_copyurls.add_argument(
        "--transfer-timeout",
        type=int,
        default=900,
        help="Maximum seconds to allow one cloud copyurl transfer before reporting it as failed.",
    )
    cloud_copyurls.set_defaults(skip_existing=True, disable_http2=True, upload_metadata=True)
    cloud_copyurls.set_defaults(func=cmd_cloud_copyurls)

    link = sub.add_parser("link")
    link.add_argument("--remote", default=DEFAULT_REMOTE)
    link.add_argument("--remote-base", default=DEFAULT_REMOTE_BASE)
    link.set_defaults(func=cmd_link)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
