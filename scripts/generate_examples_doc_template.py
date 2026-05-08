#!/usr/bin/env python3
"""Generate a ready-to-fill metadata examples template document."""

from __future__ import annotations

import argparse
from pathlib import Path


HEADER = """# Metadata Examples Intake Template

Use one block per successful scene metadata example.
Leave one blank line between examples.
Keep labels exactly as written.
"""


def _example_block(n: int) -> str:
    return f"""Example {n}
Studio:
Scene Type:
Genres:
Performers:
Winning Title:
Winning Long Description:
Winning Tags:
Winning Categories:
Why It Worked:
"""


def build_template(*, example_count: int) -> str:
    count = max(1, int(example_count))
    blocks = [_example_block(i) for i in range(1, count + 1)]
    return HEADER.rstrip() + "\n\n" + "\n".join(blocks).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate examples intake template (.md/.txt)")
    parser.add_argument(
        "output_path",
        type=Path,
        help="Output file path (.md or .txt)",
    )
    parser.add_argument(
        "--example-count",
        type=int,
        default=25,
        help="Number of example blocks to pre-create (default: 25)",
    )
    args = parser.parse_args()

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = build_template(example_count=args.example_count)
    output_path.write_text(text, encoding="utf-8")
    print(f"Wrote template: {output_path}")
    print(f"Example blocks: {max(1, int(args.example_count))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
