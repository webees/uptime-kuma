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
import subprocess
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
# History names the command that produced its evidence: `git log -Sx` or
# `git log -Gx`. The needle is taken whole — to the closing backtick, quotes
# and all — because re-running a truncated prefix searches a wider set than the
# row claims: a hash that search never reported would read as the output of the
# command that was quoted. A needle may therefore hold `/` (a path), `@` (a
# Caddy matcher) or a space (a phrase). Where the command is not backticked the
# `→` that introduces the commits ends it.
LOG = re.compile(r"git log -(S|G)`?([^`\n→]*)")


def search(history: str) -> tuple[str, str] | None:
    """The `-S`/`-G` flag and needle a History cell names, quotes removed."""
    found = LOG.search(history)
    if found is None:
        return None
    needle = found.group(2).strip().strip("'\"").strip()
    return found.group(1), needle
RUNNABLE = re.compile(r"`[^`]+`|\btests?/[^\s:]+:")
# A span is only a run if something in it can be typed: a `path:symbol`, a file
# name, an argument list or a command word. Backticks were the whole test
# before, so `` `yes` ``, `` `n/a` `` and `` `ok` `` passed every column while
# naming nothing and proving nothing.
COMMAND = re.compile(
    r"[.:]|\s|\d|^(git|python3|python3\.13|python|docker|bash|sh|curl|gh|flyctl|restic|jq|wget|make)\b"
)
# An observation is output, and output carries a number or a verdict a run
# printed; a bare `ok` is a mood, not a line anyone can compare.
OUTPUT = re.compile(r"\d|\b(OK|passed|failed|refused|denied|in sync|RULE DRIFT|matches)\b")
# A control is only runnable if the file it names still exists; a `path:symbol`
# is the protocol's spelling, so both halves are checked.
# The symbol may start with `@`: Caddy's named matchers are spelled that way,
# and they are exactly the non-runnable references a control has to avoid.
REFERENCE = re.compile(r"`([\w./-]+):([\w@][\w.@-]*)`")
# The three evidence columns are prose the protocol says must name the command
# that ran. "yes" and "ran" are the shapes that read like proof without one.
EVIDENCE = ("Positive", "Boundary", "Negative")


class Unreadable(Exception):
    """The body has no matrix section to check."""


def _git(*args: str) -> str:
    done = subprocess.run(args, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise Unreadable(f"{' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def unresolved(hashes: list[str]) -> list[str]:
    """The hashes git cannot turn into a commit, so the row cites a ghost."""
    if not hashes:
        return []
    probe = "\n".join(f"{sha}^{{commit}}" for sha in hashes) + "\n"
    done = subprocess.run(
        ["git", "cat-file", "--batch-check"],
        input=probe,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise Unreadable(f"git cat-file failed: {done.stderr.strip()}")
    gone = []
    for sha, answer in zip(hashes, done.stdout.splitlines()):
        if answer.endswith(" missing"):
            gone.append(sha)
    return gone


_REACHABLE: set[str] | None = None


def reachable() -> set[str]:
    """Every commit on this branch, read once: the list is long and stable."""
    global _REACHABLE
    if _REACHABLE is None:
        _REACHABLE = set(_git("git", "rev-list", "HEAD").split())
    return _REACHABLE


def unreachable(hashes: list[str]) -> list[str]:
    """The hashes that resolve here but are not in this branch's history.

    A squash merge deletes the branch commit, so a row that cites it passes on
    the author's clone and fails in CI against the merged `main`. Reachability
    from `HEAD` is what both places share.
    """
    if not hashes:
        return []
    full = _git("git", "rev-parse", *[f"{sha}^{{commit}}" for sha in hashes])
    return [
        sha
        for sha, commit in zip(hashes, full.split())
        if commit not in reachable()
    ]


_LOG: dict[tuple[str, str], set[str]] = {}


def log_output(flag: str, needle: str) -> set[str]:
    """The commits `git log -S/-G<needle>` reports, cached per needle."""
    key = (flag, needle)
    if key not in _LOG:
        done = subprocess.run(
            ["git", "log", f"-{flag}{needle}", "--format=%h"],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise Unreadable(
                f"git log -{flag}{needle} failed: {done.stderr.strip()}"
            )
        _LOG[key] = set(done.stdout.split())
    return _LOG[key]


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


def reference_problems(title: str, column: str, text: str) -> list[str]:
    """One problem per backticked `path:symbol` that cannot be run today.

    A named file that is gone is a command nobody can type, and a symbol that
    was renamed away is a command that runs nothing.
    """
    problems: list[str] = []
    for path, symbol in REFERENCE.findall(text):
        file = Path(path)
        if not file.is_file():
            problems.append(
                f"{title!r} {column} names missing file {path}"
            )
        elif not re.search(
            rf"(?<![\w-]){re.escape(symbol)}(?![\w-])",
            file.read_text(encoding="utf-8", errors="replace"),
        ):
            problems.append(
                f"{title!r} {column} names {symbol!r}, which is not in {path}"
            )
    return problems


# A control has to name a run, and `config/Caddyfile` is a file of directives:
# its "symbols" are not commands, so pointing a control at one reads as proof
# while nothing can be executed.
NON_RUNNABLE = {"config/Caddyfile"}


def run_problems(title: str, column: str, text: str) -> list[str]:
    """Everything wrong with a cell that is supposed to name a run.

    A span has to name something typeable, the named file and symbol have to
    still exist, and a gesture at a directive file has to become the test that
    drives it.
    """
    if not text:
        return []
    if not RUNNABLE.search(text):
        return [f"{title!r} {column} names nothing runnable: {text!r}"]
    problems: list[str] = []
    for span in SPAN.findall(text):
        if not COMMAND.search(span):
            problems.append(f"{title!r} {column} names no command: {span!r}")
    problems.extend(reference_problems(title, column, text))
    directives = sorted({path for path, _ in REFERENCE.findall(text) if path in NON_RUNNABLE})
    if directives:
        problems.append(
            f"{title!r} {column} is a bare directive on {directives}; "
            f"name the test that runs it"
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
        # A hash that does not resolve is worse than none: it reads as proof.
        cited = COMMIT.findall(history)
        named = search(history)
        if cited and not named:
            problems.append(
                f"line {number}: {title!r} History names no `git log` command, "
                f"so nobody can tell which commits it looked at"
            )
        # A hash is only evidence of *this* class if the named command reported
        # it; any reachable commit would otherwise read as history.
        produced = log_output(*named) if named else set()
        for sha in cited:
            if named and sha not in produced:
                problems.append(
                    f"line {number}: {title!r} History cites {sha}, which is not "
                    f"in the output of `git log -{named[0]}{named[1]}`"
                )
        gone = unresolved(cited)
        alive = [sha for sha in cited if sha not in gone]
        stale = set(unreachable(alive))
        for sha in cited:
            if sha in gone:
                problems.append(
                    f"line {number}: {title!r} History cites {sha}, which is not a commit"
                )
            elif sha in stale:
                problems.append(
                    f"line {number}: {title!r} History cites {sha}, which is not "
                    f"reachable from HEAD, so only the clone that still holds it "
                    f"can show it; a squash merge drops the branch commit this way"
                )
        # The control and the three evidence columns must name a run, not
        # describe one, and whatever they name has to still be there.
        for column in ("Mutation control", *EVIDENCE):
            for problem in run_problems(
                title, column, found[COLUMNS.index(column)]
            ):
                problems.append(f"line {number}: {problem}")
        guard = found[COLUMNS.index("Authoritative guard")]
        if guard:
            for problem in guard_problems(title, guard, owned.get(title, [])):
                problems.append(f"line {number}: {problem}")
        if found[-1] and UNVERIFIED.match(found[-1]) and len(found[-1].split()) < 3:
            problems.append(f"line {number}: {title!r} says 未验证 without a reason")
        # `未验证` is the one legal empty answer; anything else claims a run
        # happened and has to quote what it printed.
        elif found[-1] and not UNVERIFIED.match(found[-1]) and not SPAN.search(found[-1]):
            problems.append(
                f"line {number}: {title!r} Observed result quotes nothing; "
                f"a word is not output"
            )
        elif found[-1] and not UNVERIFIED.match(found[-1]) and not OUTPUT.search(found[-1]):
            problems.append(
                f"line {number}: {title!r} Observed result quotes no output: "
                f"{found[-1]!r} carries no result"
            )
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
