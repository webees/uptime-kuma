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
# The protocol fixes the report order, and `docs/target_mode.md:Report order`
# is where a reader looks for it. A body of nothing but the matrix satisfies
# every per-cell rule and still leaves the conclusion, the changes, the run
# output and the residuals out — the four parts the matrix is read *after*.
REPORT = ("Conclusion", "Matrix", "Changes", "Verification", "Residual")
# A fence line: ```` ``` ```` or `~~~`, indented by at most three spaces.
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
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
# The span has to hold a non-blank character: matching the backticks alone let
# `` ` ` `` pass an evidence column without naming anything.
RUNNABLE = re.compile(r"`[^`]*\S[^`]*`|\btests?/[^\s:]+:")
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
# A verification is a list of things that ran, so every one of them has to be
# repeatable or linkable. "passed", "see the matrix above" and "all green"
# describe a run without letting a reader run or open it.
URL = re.compile(r"https?://\S+")
# A line that itself reports a ghost — `absent: \`tests/gone.py\`` — names
# something on purpose which is not there. Without the marker the section
# would flag every negative example it cites.
ABSENT = re.compile(r"\babsent:", re.IGNORECASE)
# A command the verification cites has to be typeable today: the script or the
# test file in it must still be there. A moved test and a deleted script read
# exactly like a passing one.
PATHLIKE = re.compile(
    r"(?<![\w./:-])((?:[\w.-]+/)+[\w.-]+|[\w-]+\.(?:py|sh|ya?ml|json|md|txt|toml))"
)
# A bullet may name a directory when the change spans it: `docs/` ends in the
# separator the file pattern requires an entry after, so it matched nothing and
# a whole-tree change was reported as "names no file".
DIRECTORY = re.compile(r"(?<![\w./:-])((?:[\w.-]+/)+)(?![^\s`])")
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


def unfenced(body: str) -> str:
    """The body with fenced code blanked out, one character for one.

    A PR that quotes an example body puts `## Matrix` inside a fence, and the
    plain `^## ` search then finds the example instead of the section. The
    mask keeps every offset, so a heading found in it is cut out of the body.
    """
    out, fence = [], None
    for line in body.split("\n"):
        mark = FENCE.match(line)
        if mark:
            fence = None if fence == mark.group(1)[0] else (fence or mark.group(1)[0])
        out.append(" " * len(line) if mark or fence else line)
    return "\n".join(out)


def report_sections(body: str) -> list[tuple[str, str]]:
    """Every `## Heading` outside a fence and its content, in order.

    The body may open with one, so the leading newline cannot be assumed.
    """
    placed = list(re.finditer(r"(?m)^## (.+)$", unfenced(body)))
    return [
        (
            match.group(1).strip(),
            body[match.end() : placed[number + 1].start() if number + 1 < len(placed) else len(body)],
        )
        for number, match in enumerate(placed)
    ]


def matrix_rows(body: str) -> list[str]:
    """The table lines of `## Matrix`, ignoring a table quoted in a fence."""
    for head, content in report_sections(body):
        if head != "Matrix":
            continue
        rows = [
            line for line in unfenced(content).split("\n") if line.strip().startswith("|")
        ]
        if len(rows) < 2:
            raise Unreadable("the matrix has no header")
        return rows
    raise Unreadable("the body has no `## Matrix` section")


def verification_problems(content: str) -> list[str]:
    """Every verification line has to carry a command or a link.

    The section is the one place a reader goes to repeat the round, so a word
    that says it happened ("passed", "see above") repeats nothing. Prose about
    a deployment is still prose; the bullets are the claims. A cited file that
    is gone is the same shape one step further: the command no longer runs.
    """
    text = unfenced(content)
    names_run = lambda line: bool(URL.search(line)) or any(
        COMMAND.search(span) for span in SPAN.findall(line)
    )
    problems: list[str] = []
    if not any(names_run(line) for line in text.splitlines()):
        problems.append("`## Verification` names no run")
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith(("- ", "* ")) and not names_run(line):
            problems.append(f"`## Verification` line {number} names no run: {line.strip()!r}")
        for issue in reference_issues(line, tolerate_missing=bool(ABSENT.search(line))):
            problems.append(f"`## Verification` line {number}: {issue}")
    return problems


def reference_issues(line: str, *, tolerate_missing: bool = False) -> list[str]:
    """Repository paths a verification line names that no longer run.

    URLs are dropped first, so the host and its path segments are not read as
    a file. A dotted unittest module is the third spelling of the same thing,
    and is only converted when its first segment is a directory here.

    `absent:` excuses a missing *file*, because that is the ghost the line is
    demonstrating. It does not excuse the rest: a command naming a file that is
    still here and a case that never was is a different defect, and skipping
    the whole line let the marker launder it.
    """
    bare = URL.sub(" ", line)
    problems = [
        f"names missing file {path.rstrip('.,;')}"
        for path in PATHLIKE.findall(bare)
        if not Path(path.rstrip(".,;")).exists()
    ]
    if tolerate_missing:
        problems = [issue for issue in problems if not issue.startswith("names missing file")]
    for token in re.findall(r"`([^`]+)`", bare):
        words = [word.strip("(),.;") for word in token.split()]
        for word in words:
            if not re.fullmatch(r"[\w-]+(?:\.[\w-]+)+", word):
                continue
            parts = word.split(".")
            if not Path(parts[0]).is_dir():
                continue
            # `tests.test_x.Case.case` — the file is the first segment that is
            # not a directory, plus `.py`; the rest names a symbol, not a file.
            cut = next(
                (index + 1 for index, _ in enumerate(parts) if not Path(
                    "/".join(parts[:index + 1])).is_dir()),
                len(parts),
            )
            path = "/".join(parts[:cut]) + ".py"
            if not Path(path).is_file():
                if not tolerate_missing:
                    problems.append(f"names missing file {path}")
                continue
            # The file existing is not the run: after `tests.test_x` the rest
            # of the dotted name is the case, and a renamed case runs nothing.
            source = Path(path).read_text(encoding="utf-8", errors="replace")
            for symbol in parts[cut:]:
                if not re.search(rf"(?<![\w-]){re.escape(symbol)}(?![\w-])", source):
                    problems.append(f"names {path} with no {symbol}")
    return sorted(set(problems))


def conclusion_problems(content: str) -> list[str]:
    """The conclusion stays prose but has to point at something checkable.

    "Why" cannot be resolved against a tree, so the section keeps its free
    form — `x` was the shape that passed with nothing to open. A reference is
    the smallest thing that lets the next reader start from the conclusion
    instead of the diff, and it is checked like every other one.
    """
    text = unfenced(content)
    problems: list[str] = []
    if not (URL.search(text) or PATHLIKE.search(text) or SPAN.search(text)):
        problems.append("`## Conclusion` names nothing checkable")
    for issue in reference_issues(text):
        problems.append(f"`## Conclusion`: {issue}")
    return problems


def residual_problems(content: str) -> list[str]:
    """Every open residual names when it fires and how it would be seen.

    A residual outlives the round it was written in, so it is the section a
    later reader comes back for. Being non-empty is not enough: `None.` reads as
    a closed list with no way to check, and "the deploy might fail" says
    neither when it fires nor how anyone would notice. The registry in
    `README.md` is the one place a residual may live, so a body that claims
    none has to name it.
    """
    text = unfenced(content)
    problems: list[str] = []
    # The claim is the section itself, not one occurrence of the word: a bullet
    # may open "none of the retries survive…" and still need its own fields.
    claims_none = re.search(r"(?m)^\s*none\b", text, re.IGNORECASE)
    if claims_none:
        # Naming the registry is a pointer, so it has to resolve. A literal
        # substring test let `README2.md` and `docs/gone.md` through: the
        # sentence still said `README.md` inside the wrong name.
        named = {path.rstrip(".,;") for path in PATHLIKE.findall(URL.sub(" ", text))}
        if "README.md" not in named:
            problems.append("`## Residual risk` does not name README.md, where they are kept")
        problems.extend(reference_issues(text))
        return problems
    # A bullet owns its continuation lines, which is where the two fields sit.
    blocks: list[list[str]] = []
    for line in text.splitlines():
        if line.lstrip().startswith(("- ", "* ")):
            blocks.append([line])
        elif blocks and line.strip():
            blocks[-1].append(line)
    if not blocks:
        problems.append("`## Residual risk` is prose with no `none` claim")
        return problems
    for number, block in enumerate(blocks, start=1):
        label = f"`## Residual risk` bullet {number}"
        if not re.search(r"Trigger:", " ".join(block), re.IGNORECASE):
            problems.append(f"{label} names no trigger")
        else:
            # The field itself can be empty, and a wrapped value is on the next
            # line; only the field's own remainder counts towards emptiness.
            at = next(
                number for number, line in enumerate(block)
                if re.search(r"Trigger:", line, re.IGNORECASE)
            )
            same_line = re.split(r"Trigger:", block[at], maxsplit=1, flags=re.IGNORECASE)[1]
            # Stop at the next field: the observation belongs to its own check.
            rest = [same_line]
            for line in block[at + 1 :]:
                if re.search(r"Observed by:", line, re.IGNORECASE):
                    break
                rest.append(line)
            if not " ".join(rest).strip():
                problems.append(f"{label} names an empty trigger")
        observed = next(
            (line for line in block if re.search(r"Observed by:", line, re.IGNORECASE)), None
        )
        if observed is None:
            problems.append(f"{label} names no observation")
        else:
            # The field is empty as soon as nothing follows its colon, on that
            # line or a wrapped one; a value is then still checked for a run.
            at = block.index(observed)
            same_line = re.split(r"Observed by:", observed, maxsplit=1, flags=re.IGNORECASE)[1]
            value = " ".join([same_line, *block[at + 1 :]]).strip()
            if not value:
                problems.append(f"{label} names an empty observation")
            elif not (URL.search(value) or any(COMMAND.search(span) for span in SPAN.findall(value))):
                problems.append(f"{label} observation names no command")
    for issue in reference_issues(text):
        problems.append(f"`## Residual risk`: {issue}")
    return problems


def changes_problems(content: str, touched: list[str] | None) -> list[str]:
    """Every change bullet names a file that exists, and the diff is covered.

    `## Changes` is the list a reviewer maps onto the diff, so "made things
    better" and a bullet for a file that is not in the PR both cost the reader
    the same work. Each bullet carries a repository path; with `--touched`,
    every changed file also has to be named somewhere in the section.

    A directory with a trailing slash is a path too: a rule changed across a
    tree is one bullet, and it covers the files under it. Before this it matched
    no file at all, so the bullet read as "made things better" and every file
    under it counted as unnamed.
    """
    text = unfenced(content)
    problems: list[str] = []

    def named_paths(line: str) -> list[tuple[str, bool]]:
        """`(path, is_directory)` pairs on one line, in reading order."""
        bare = URL.sub(" ", line)
        trees = DIRECTORY.findall(bare)
        # `docs/gone/` also satisfies the file pattern as `docs/gone`; the
        # directory is the reading the slash asks for, so it is not counted
        # twice and the message names one thing.
        found = [
            (path.rstrip(".,;"), False)
            for path in PATHLIKE.findall(bare)
            if f"{path.rstrip('.,;')}/" not in trees
        ]
        found.extend((path, True) for path in trees)
        return found

    for number, line in enumerate(text.splitlines(), start=1):
        if not line.lstrip().startswith(("- ", "* ")):
            continue
        paths = named_paths(line)
        if not paths:
            problems.append(f"`## Changes` line {number} names no file: {line.strip()!r}")
        for path, directory in paths:
            if directory and not Path(path).is_dir():
                problems.append(f"`## Changes` line {number} names missing directory {path}")
            elif not directory and not Path(path).exists():
                problems.append(f"`## Changes` line {number} names missing file {path}")
    if touched:
        named = [path for line in text.splitlines() for path, _ in named_paths(line)]
        trees = [path for line in text.splitlines() for path, directory in named_paths(line) if directory]
        for path in touched:
            if path not in named and not any(path.startswith(tree) for tree in trees):
                problems.append(f"`## Changes` does not name the touched file {path}")
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.lstrip().startswith(("- ", "* ")):
                continue
            for path, directory in named_paths(line):
                inside = directory and any(item.startswith(path) for item in touched)
                if Path(path).exists() and path not in touched and not inside:
                    problems.append(f"`## Changes` line {number} names a file the PR did not change: {path}")
    return problems


def report_problems(body: str, touched: list[str] | None = None) -> list[str]:
    """The report sections, in order, each present once and carrying content.

    A section heading is a promise to write something under it. Heading-only
    `## Conclusion` and `## Verification` are what the template leaves behind,
    and a repeated `## Verification` reads as a second run while the order
    check counts the sections twice.
    """
    placed = report_sections(body)
    problems: list[str] = []
    # "No preamble" is the protocol's first sentence: the conclusion is the
    # thing a reader opens on. Everything above the first section heading is
    # therefore an abstract the conclusion has to repeat. Blank lines are not
    # a preamble — the template starts that way — so only text counts.
    opening = re.search(r"(?m)^## ", unfenced(body))
    if opening and body[: opening.start()].strip():
        problems.append("the body opens with a preamble before `## Conclusion`")
    for name in REPORT:
        matching = [head for head, _ in placed if head == name or head.startswith(f"{name} ")]
        if not matching:
            if name == "Residual":
                problems.append("the body has no residual section")
            else:
                problems.append(f"the body has no `## {name}` section")
        elif len(matching) > 1:
            problems.append(f"`## {name}` appears {len(matching)} times")
        else:
            head, content = next(item for item in placed if item[0] == matching[0])
            if not content.strip():
                problems.append(f"`## {head}` is empty")
            elif name == "Verification":
                problems.extend(verification_problems(content))
            elif name == "Changes":
                problems.extend(changes_problems(content, touched))
            elif name == "Conclusion":
                problems.extend(conclusion_problems(content))
            elif name == "Residual":
                problems.extend(residual_problems(content))
    # One entry per section: a duplicate is already named above, and counting it
    # again here would report the same heading twice as an order problem too.
    ordered = list(
        dict.fromkeys(
            head for head, _ in placed if head in REPORT or head.startswith("Residual")
        )
    )
    rank = lambda head: REPORT.index(head.split()[0])
    if ordered != sorted(ordered, key=rank):
        problems.append(
            f"the report order is {', '.join(REPORT)}, got {', '.join(ordered)}"
        )
    return problems


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


def controls(document: str) -> dict[str, set[str]]:
    """The `path:symbol` runs each class records as its own controls.

    The guard column is pinned to the authority for the same reason: a test
    that resolves is not necessarily *this* class's test, so a neighbour's
    control reads as an audit of an invariant it never touches.
    """
    found: dict[str, set[str]] = {}
    for title in classes(document):
        body = document.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]
        block = body.split("\nMutation control:", 1)[-1].split("\n\n", 1)[0]
        found[title] = {
            span for span in SPAN.findall(block) if "/" in span and ":" in span
        }
    return found


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


def run_problems(
    title: str, column: str, text: str, recorded: set[str]
) -> list[str]:
    """Everything wrong with a cell that is supposed to name a run.

    A span has to name something typeable, the named file and symbol have to
    still exist, a named `path:symbol` has to be one the class records, and a
    gesture at a directive file has to become the test that drives it.
    """
    if not text:
        return []
    if not RUNNABLE.search(text):
        return [f"{title!r} {column} names nothing runnable: {text!r}"]
    problems: list[str] = []
    for span in SPAN.findall(text):
        if not COMMAND.search(span):
            problems.append(f"{title!r} {column} names no command: {span!r}")
        elif "/" in span and ":" in span and span not in recorded:
            problems.append(
                f"{title!r} {column} is not this class's control: {span!r} is "
                f"not recorded under its `Mutation control:`"
            )
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
    controlled = controls(document)
    problems: list[str] = []
    problems.extend(report_problems(body, touched))
    rows = matrix_rows(body)

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
                title, column, found[COLUMNS.index(column)], controlled.get(title, set())
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
    if seen != [title for title in expected if title in seen]:
        problems.append("the rows are not in registry order")
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
