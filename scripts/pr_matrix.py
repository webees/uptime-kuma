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
SPAN = re.compile(r"`([^`]+)`")
# `History` and `Mutation control` are prose columns a row can satisfy with a
# word. History must carry its classification *and* the commit that supports
# it; a control must name something runnable, not assert one exists.
HISTORY = re.compile(r"\b(never fixed|never established|regressed|unchanged|partial(?:ly)?|not established)\b")
COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b")
RUNNABLE = re.compile(r"`[^`]+`|\btests?/[^\s:]+:")


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
    """Every span each class names as its authority, by class title.

    A span is a `path` or a `path:symbol`; both spellings appear in the file,
    so the path before the first `:` is what a row is compared on.
    """
    found: dict[str, list[str]] = {}
    for title in classes(document):
        body = document.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]
        # The authority paragraph can wrap and several classes continue with
        # prose full of inline code that is not a path (`main`, `load_status`).
        # The authority itself is the first sentence, up to the next field.
        block = body.split("\nAuthority: ", 1)[-1].split("\nMutation control:", 1)[0]
        found[title] = SPAN.findall(re.split(r"\.(?:\s|$)", block, maxsplit=1)[0])
    return found


def paths(spans: list[str]) -> set[str]:
    """The path part of `path` or `path:symbol`, for comparison."""
    return {span.split(":")[0] for span in spans}


def guard_problems(title: str, guard: str, recorded: list[str]) -> list[str]:
    """One problem per guard that is not the authority this class records.

    The path alone is not the invariant: `config/Caddyfile` is the authority of
    two classes and `scripts/restic.sh` of four, so a correct path with a
    neighbour's symbol reads as audited while pointing at nothing. A span must
    therefore be recorded as written, and its symbol must still exist in that
    file — a rename that leaves the document behind is the same defect. A class
    that records only the bare file (`Dockerfile`) accepts a symbol, which must
    still occur in it; a class that records symbols requires the written one.
    """
    named = SPAN.findall(guard) or [guard]
    colon = {other.split(":")[0] for other in recorded if ":" in other}
    owned = paths(recorded)
    problems: list[str] = []
    for span in named:
        path, _, symbol = span.partition(":")
        if path not in owned:
            problems.append(
                f"{title!r} guard {guard!r} is not the recorded "
                f"authority {sorted(set(recorded))}"
            )
            continue
        # A recorded bare path accepts any symbol on it. Where symbols are
        # recorded only the written one passes — a missing symbol names both
        # gates of a shared file, a neighbour's directive names the wrong one.
        if path in colon and span not in recorded:
            problems.append(
                f"{title!r} guard {guard!r} is not the recorded authority "
                f"{sorted(set(recorded))}"
            )
            continue
        file = Path(path)
        if symbol and not file.is_file():
            problems.append(f"{title!r} guard {guard!r} names a missing file {path}")
            continue
        if symbol and file.is_file() and not re.search(
            rf"(?<![\w-]){re.escape(symbol)}(?![\w-])",
            file.read_text(encoding="utf-8", errors="replace"),
        ):
            problems.append(
                f"{title!r} guard {guard!r} symbol {symbol!r} does not occur in {path}"
            )
    return problems


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
        history, control = found[COLUMNS.index("History")], found[COLUMNS.index("Mutation control")]
        if history and not (HISTORY.search(history) and COMMIT.search(history)):
            problems.append(
                f"line {number}: {title!r} History needs a classification and "
                f"the commit it rests on, not {history!r}"
            )
        if control and not RUNNABLE.search(control):
            problems.append(
                f"line {number}: {title!r} Mutation control names nothing "
                f"runnable: {control!r}"
            )
        guard = found[COLUMNS.index("Authoritative guard")]
        if guard:
            for problem in guard_problems(title, guard, owned.get(title, [])):
                problems.append(f"line {number}: {problem}")
        if found[-1] and UNVERIFIED.match(found[-1]) and len(found[-1].split()) < 3:
            problems.append(f"line {number}: {title!r} says 未验证 without a reason")
        if touched and found[-1] and UNVERIFIED.match(found[-1]):
            hit = sorted(paths(owned.get(title, [])) & set(touched))
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
