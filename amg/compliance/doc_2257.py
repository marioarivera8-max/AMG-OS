"""
2257 documentation verification.

Per US 2257 record-keeping requirements, every adult VOD scene must have
documentation on file proving performers were 18+ at time of recording.

v11 only verifies presence of the documentation. Full age verification
is operator's responsibility.

Looks for files like:
    2257.pdf, 2257.jpg, 2257.png, id.pdf, ID.pdf

In the same folder as the video.
"""
from pathlib import Path
from typing import Optional

from amg.config import COMPLIANCE_2257_FILENAMES, REQUIRE_2257_DOC
from amg.utils.logging import get_logger

log = get_logger("compliance.doc_2257")


def verify_2257(video_path: Path) -> dict:
    """
    Check for 2257 documentation alongside the video.

    Returns:
        {
            'present': bool,
            'path': Path or None,
            'should_block': bool,  # True if missing AND REQUIRE_2257_DOC is True
            'error_code': str or None,
        }
    """
    parent = video_path.parent

    # Check standard filenames
    for name in COMPLIANCE_2257_FILENAMES:
        candidate = parent / name
        if candidate.exists():
            log.info("2257 doc found", path=str(candidate))
            return {
                "present": True,
                "path": candidate,
                "should_block": False,
                "error_code": None,
            }

    # Also check case-insensitive variations
    for entry in parent.iterdir():
        if entry.is_file() and entry.name.lower() in [n.lower() for n in COMPLIANCE_2257_FILENAMES]:
            log.info("2257 doc found (case variant)", path=str(entry))
            return {
                "present": True,
                "path": entry,
                "should_block": False,
                "error_code": None,
            }

    # Also check for any file with "2257" in the name
    for entry in parent.iterdir():
        if entry.is_file() and "2257" in entry.name:
            log.info("2257 doc found (pattern match)", path=str(entry))
            return {
                "present": True,
                "path": entry,
                "should_block": False,
                "error_code": None,
            }

    log.warn("No 2257 documentation found", scene_dir=str(parent))
    return {
        "present": False,
        "path": None,
        "should_block": REQUIRE_2257_DOC,
        "error_code": "E_COMPLIANCE_NO_2257" if REQUIRE_2257_DOC else None,
    }
