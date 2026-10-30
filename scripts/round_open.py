#!/usr/bin/env python3
"""Print the round-entry skeleton: one empty matrix row per recorded class.

The matrix columns and the residual list are the two things a rushed round
drops first. Both come out of the repository, so a round cannot open on memory
alone.

    python3.13 scripts/round_open.py
"""

from __future__ import annotations

import re
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("ROUND_OPEN_ROOT") or Path(__file__).resolve().parents[1])
DOC = Path(os.environ.get("ROUND_OPEN_DOC") or ROOT / "docs/recurring_issues.md")
README = Path(os.environ.get("ROUND_OPEN_README") or ROOT / "README.md")

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
BASELINE = re.compile(r"^Audit baseline: every commit reachable through `([0-9a-f]{7,40})`", re.MULTILINE)


class Unreadable(Exception):
    """The baseline or the history cannot be read, so the gate must not pass."""


def _git(*args: str) -> str:
    try:
        done = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise Unreadable(f"git {args[0]}: {error}") from error
    if done.returncode != 0:
        raise Unreadable(f"git {args[0]}: {done.stderr.strip() or done.returncode}")
    return done.stdout.strip()


def audit(baseline: str) -> tuple[str, str]:
    """Total reachable commits, and how many of them are past the baseline.

    The second number is the work the round owes: everything after the
    baseline still has to be audited. It is derived rather than written down,
    because the commit that records a count makes that count wrong.
    """
    try:
        _git("rev-parse", "--verify", f"{baseline}^{{commit}}")
    except Unreadable as error:
        raise Unreadable(f"baseline {baseline} does not resolve: {error}") from error
    return _git("rev-list", "--count", "HEAD"), _git(
        "rev-list", "--count", f"{baseline}..HEAD"
    )


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

    match = BASELINE.search(text)
    if match is None:
        print("docs/recurring_issues.md carries no audit baseline", file=sys.stderr)
        return 1
    baseline = match.group(1)
    try:
        total, owed = audit(baseline)
    except Unreadable as error:
        print(f"cannot read the baseline: {error}", file=sys.stderr)
        return 2

    print("## Round entry")
    print()
    print(f"## Audit baseline")
    print()
    print(f"- baseline commit: `{baseline}`")
    print(f"- commits reachable from HEAD: {total}")
    print(f"- commits past the baseline, to audit this round: {owed}")
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
