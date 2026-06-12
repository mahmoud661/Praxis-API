"""Library-boundary guard for `react_agent/`.

The package is destined for extraction as a standalone library, so
nothing inside it may import the host application. Two ways to escape
and both are checked per file:

  1. absolute imports of the app (`from app...` / `import app...`)
  2. relative imports with more dots than the file's depth inside
     `react_agent/` (a `from ....x` in `middlewares/foo.py` reaches
     OUTSIDE the package)

This is what keeps the "stay in place until extraction" decision safe:
the boundary is enforced by CI even though the folder still physically
lives under the app tree.
"""

from __future__ import annotations

import re
from pathlib import Path

REACT_AGENT_ROOT = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "application"
    / "services"
    / "agentic"
    / "react_agent"
)

_APP_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+app[.\s]", re.MULTILINE)
_RELATIVE_IMPORT_RE = re.compile(r"^\s*from\s+(\.+)", re.MULTILINE)


def _py_files() -> list[Path]:
    return [
        p
        for p in REACT_AGENT_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def test_react_agent_root_exists() -> None:
    assert REACT_AGENT_ROOT.is_dir(), REACT_AGENT_ROOT


def test_no_app_imports_inside_react_agent() -> None:
    offenders: list[str] = []
    for path in _py_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if _APP_IMPORT_RE.search(text):
            offenders.append(str(path.relative_to(REACT_AGENT_ROOT)))
    assert offenders == [], (
        "react_agent must not import the host app — offending files: "
        f"{offenders}"
    )


def test_no_relative_imports_escaping_react_agent() -> None:
    offenders: list[str] = []
    for path in _py_files():
        rel = path.relative_to(REACT_AGENT_ROOT)
        # Depth inside the package: a module at react_agent/x.py may
        # use at most 1 leading dot ("from . import"), one in
        # middlewares/ at most 2, etc. `len(rel.parts)` counts the
        # filename, which equals the max legal dot count.
        max_dots = len(rel.parts)
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _RELATIVE_IMPORT_RE.finditer(text):
            if len(match.group(1)) > max_dots:
                offenders.append(f"{rel} ({match.group(0).strip()})")
    assert offenders == [], (
        "relative import escapes the react_agent package — offending: "
        f"{offenders}"
    )
