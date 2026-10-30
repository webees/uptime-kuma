#!/usr/bin/env python3
"""Check that a PR body carries the round matrix, filled in.

`docs/target_mode.md` makes the matrix a rejection criterion and names the PR
body as its home, but until now nothing read the body: a table missing a class,
a column, or an observation passed review by eye only. The protocol says the
matrix is a review artifact and forbids per-round files, so the PR body is the
only place it can be checked from.

    gh api repos/<owner>/<repo>/pulls/<n> --jq .body > body.md
    pr_matrix.py body.md <docs/recurring_issues.md> [--touched FILE...]

`--touched` names the files the pull request changes. A class whose authority
file is among them has to carry an observed result: `未验证` with a reason is
legal, but not for the guard the diff just edited, because the reader has that
diff in hand and no later round will look again.

Exit codes: `0` complete, `1` a violation (named by line), `2` unreadable.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

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
# `Observed result` is the one column a row may leave unproven, but only with a
# stated reason: the protocol's word for "not run" is `未验证`.
UNVERIFIED = re.compile(r"^(未验证|n/a)\b")
FRONT_MATTER = {"Target-Mode Prompt", "Every-Round Goal Gate"}
AUTHORITY = re.compile(r"`([^`:]+):")


class Unreadable(Exception):
    """The body has no matrix section to check."""


def classes(document: str) -> list[str]:
    return [
        title.strip()
        for title in re.findall(r"(?m)^## (.+)$", document)
        if title.strip() not in FRONT_MATTER
    ]


def cells(line: str) -> list[str]:
    """Split a table row on unescaped pipes, keeping `\\|` inside a cell."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.replace("\\|", "|").strip() for cell in re.split(r"(?<!\\)\|", body)]


def matrix_rows(body: str) -> list[str]:
    # The section may open the body, so the leading newline cannot be assumed.
    section = body.split("\n## Matrix\n", 1)
    if len(section) == 1 and body.startswith("## Matrix\n"):
        section = ["", body[len("## Matrix\n"):]]
    if len(section) != 2:
        raise Unreadable("the body has no `## Matrix` section")
    rows = [line for line in section[1].split("\n## ", 1)[0].splitlines() if line.strip().startswith("|")]
    if len(rows) < 2:
        raise Unreadable("the matrix has no header")
    return rows


def authorities(document: str) -> dict[str, list[str]]:
    """The files each class names as its authority, by class title."""
    found: dict[str, list[str]] = {}
    for title in classes(document):
        block = document.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]
        line = block.split("\nAuthority: ", 1)[-1].split("\n", 1)[0]
        found[title] = AUTHORITY.findall(line)
    return found


def check(
    body: str,
    document: str,
    *,
    minimum_rows: int = 1,
    touched: list[str] | None = None,
) -> list[str]:
    """Return one problem per violation; empty means the matrix is complete."""
    expected = classes(document)
    owned = authorities(document)
    rows = matrix_rows(body)
    problems: list[str] = []

    header = cells(rows[0])
    if tuple(header) != COLUMNS:
        problems.append(f"header is {header}, expected {list(COLUMNS)}")

    seen: list[str] = []
    for number, row in enumerate(rows[2:], start=3):
        found = cells(row)
        if len(found) != len(COLUMNS):
            problems.append(f"line {number}: {len(found)} columns, expected {len(COLUMNS)}")
            continue
        title = found[0]
        if title not in expected:
            problems.append(f"line {number}: {title!r} is not a recorded class")
            continue
        if title in seen:
            problems.append(f"line {number}: {title!r} appears twice")
        seen.append(title)
        for column, value in zip(COLUMNS, found):
            if value:
                continue
            if column == "Observed result":
                problems.append(f"line {number}: {title!r} has no observed result")
            else:
                problems.append(f"line {number}: {title!r} leaves {column} empty")
        if found[-1] and UNVERIFIED.match(found[-1]) and len(found[-1].split()) < 3:
            problems.append(f"line {number}: {title!r} says 未验证 without a reason")
        if touched and found[-1] and UNVERIFIED.match(found[-1]):
            hit = sorted(set(owned.get(title, [])) & set(touched))
            if hit:
                problems.append(
                    f"line {number}: {title!r} is 未验证 but this PR touches its "
                    f"authority {', '.join(hit)}"
                )

    for title in expected:
        if title not in seen:
            problems.append(f"missing class {title!r}")
    if len(seen) < minimum_rows:
        problems.append(f"only {len(seen)} row(s) filled")
    return problems


def main(argv: list[str]) -> int:
    rest = list(argv[1:])
    touched: list[str] = []
    positional: list[str] = []
    while rest:
        item = rest.pop(0)
        if item == "--touched":
            if not rest:
                print("--touched needs at least one path", file=sys.stderr)
                return 2
            touched.extend(rest)
            rest.clear()
        else:
            positional.append(item)
    if len(positional) != 2:
        print(
            f"usage: {argv[0]} <pr-body> <docs/recurring_issues.md> [--touched FILE...]",
            file=sys.stderr,
        )
        return 2
    try:
        body = Path(positional[0]).read_text(encoding="utf-8")
        document = Path(positional[1]).read_text(encoding="utf-8")
        problems = check(body, document, touched=touched)
    except (OSError, Unreadable) as error:
        print(f"cannot check: {error}", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(f"MATRIX: {problem}", file=sys.stderr)
        return 1
    note = f", {len(touched)} touched" if touched else ""
    print(f"matrix complete: {len(classes(document))} classes, {len(COLUMNS)} columns{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
