"""CI guard for the versioned prompt registry.

Checks:
1) Registry filenames follow `<prompt_id>.<semver>.prompt.md`.
2) Every registry file is loadable via `load_prompt_registry`.
3) `amg/` source code contains no inline prompt-like string literals.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys
from typing import List, Tuple

from amg.prompts.loader import list_registered_prompts, load_prompt_registry


REPO_ROOT = Path(__file__).resolve().parents[1]
AMG_DIR = REPO_ROOT / "amg"
REGISTRY_DIR = AMG_DIR / "prompts" / "registry"
FILENAME_RE = re.compile(r"^[a-z0-9_]+\.\d+\.\d+\.\d+\.prompt\.md$")

PROMPTISH_MARKERS = (
    "OUTPUT FORMAT",
    "TIER_A_PASS:",
    "SCORE:",
    "Generate retail-optimized",
    "Classify the primary explicit sexual position",
    "Return strict JSON with keys",
    "You generate high-performing adult retail metadata",
)


def _iter_python_files() -> List[Path]:
    files: List[Path] = []
    for path in AMG_DIR.rglob("*.py"):
        rel = path.relative_to(AMG_DIR)
        if rel.parts[:2] == ("prompts", "registry"):
            continue
        if rel.parts[:1] == ("prompts",):
            # loader is infrastructure, not prompt text.
            continue
        files.append(path)
    return files


def _is_docstring(node: ast.Constant, parent: ast.AST | None) -> bool:
    if not isinstance(node.value, str) or not isinstance(parent, ast.Expr):
        return False
    grand = getattr(parent, "_parent", None)
    if not isinstance(grand, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return False
    body = grand.body if hasattr(grand, "body") else []
    return bool(body) and body[0] is parent


def _collect_inline_prompt_issues(path: Path) -> List[Tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    for parent_node in ast.walk(tree):
        for child in ast.iter_child_nodes(parent_node):
            setattr(child, "_parent", parent_node)

    issues: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        parent_obj = getattr(node, "_parent", None)
        parent = parent_obj if isinstance(parent_obj, ast.AST) else None
        if _is_docstring(node, parent):
            continue
        value = node.value
        if len(value) < 80:
            continue
        if any(marker in value for marker in PROMPTISH_MARKERS):
            issues.append((node.lineno, value.splitlines()[0][:120]))
    return issues


def main() -> int:
    errors: List[str] = []

    if not REGISTRY_DIR.exists():
        errors.append(f"Missing prompt registry directory: {REGISTRY_DIR}")
    else:
        for path in sorted(REGISTRY_DIR.glob("*.prompt.md")):
            if not FILENAME_RE.match(path.name):
                errors.append(f"Invalid registry filename: {path.relative_to(REPO_ROOT)}")

    registry = list_registered_prompts()
    if not registry:
        errors.append("Prompt registry is empty.")
    else:
        for prompt_id, versions in registry.items():
            for version in versions:
                try:
                    body = load_prompt_registry(prompt_id, version=version)
                    if not body.strip():
                        errors.append(f"Prompt body is empty: {prompt_id}.{version}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Failed loading prompt {prompt_id}.{version}: {exc}")

    for py_file in _iter_python_files():
        issues = _collect_inline_prompt_issues(py_file)
        rel = py_file.relative_to(REPO_ROOT)
        for line_no, snippet in issues:
            errors.append(f"Inline prompt-like string in {rel}:{line_no}: {snippet!r}")

    if errors:
        print("prompts registry linter failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("prompts registry linter passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
