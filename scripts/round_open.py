#!/usr/bin/env python3
"""Print the round-entry skeleton: one empty matrix row per recorded class.

The matrix columns and the residual list are the two things a rushed round
drops first. Both come out of the repository, so a round cannot open on memory
alone.

    python3.13 scripts/round_open.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recurring_issues.md"
README = ROOT / "README.md"

COLUMNS = (
    "Class",
    "History",
    "Authoritative guard",
    "Positive",
    "Boundary",
    "Negative",
    "Mutation control",
    "Observed result",
)
FRONT_MATTER = {"Target-Mode Prompt", "Every-Round Goal Gate"}


def classes(text: str) -> list[str]:
    return [
        title
        for title in re.findall(r"(?m)^## (.+)$", text)
        if title not in FRONT_MATTER
    ]


def residuals(text: str) -> list[str]:
    block = text.split("\n## ⚠️ Residual risk\n", 1)
    if len(block) != 2:
        return []
    body = block[1].split("\n## ", 1)[0]
    found: list[str] = []
    for line in body.splitlines():
        if line.startswith("- "):
            found.append(line.removeprefix("- "))
        elif found and line.strip():
            found[-1] = f"{found[-1]} {line.strip()}"
    return [" ".join(item.split()) for item in found]


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    found = classes(text)
    if not found:
        print("no classes found in docs/recurring_issues.md", file=sys.stderr)
        return 1

    print("## Round entry")
    print()
    print("| " + " | ".join(COLUMNS) + " |")
    print("|" + "---|" * len(COLUMNS))
    for title in found:
        print("| " + title + " |" + "  |" * (len(COLUMNS) - 1))

    print()
    open_residuals = residuals((README).read_text(encoding="utf-8"))
    print(f"## Open residuals ({len(open_residuals)})")
    for residual in open_residuals:
        print(f"- [ ] {residual}")
    print()
    print("Fill every cell with a command that ran; a green control proves nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
