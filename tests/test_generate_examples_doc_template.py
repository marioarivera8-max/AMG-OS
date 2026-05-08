from pathlib import Path


def test_build_template_has_expected_labels():
    from scripts.generate_examples_doc_template import build_template

    out = build_template(example_count=2)
    assert "Example 1" in out
    assert "Example 2" in out
    assert "Winning Title:" in out
    assert "Winning Long Description:" in out
    assert "Winning Tags:" in out
    assert "Winning Categories:" in out


def test_generator_script_writes_file(tmp_path):
    import subprocess
    import sys

    out_path = tmp_path / "template.md"
    cmd = [
        sys.executable,
        "scripts/generate_examples_doc_template.py",
        str(out_path),
        "--example-count",
        "3",
    ]
    completed = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True)
    assert completed.returncode == 0
    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "Example 3" in text
