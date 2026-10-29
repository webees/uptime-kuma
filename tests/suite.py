#!/usr/bin/env python3
"""Shared harness: docker helpers and one isolated, lifecycle-marked shell case.

The image carries no Python, so every product assertion is a shell snippet that
runs inside a throwaway container. A snippet only counts when it prints both
lifecycle markers: a missing end marker is treated as harness/infra failure and
retried, an end marker with a nonzero status is a product failure and is not
retried.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS = REPO_ROOT / "scripts"
SOURCE = REPO_ROOT

STARTED = "__case_started__"
ENDED = "__case_ended__="
PASSED = "__case_passed__"


class Suite:
    def __init__(self, name: str) -> None:
        self.name = name
        self.failures = 0

    def ok(self, message: str) -> None:
        print(f"ok   {message}", flush=True)

    def bad(self, message: str) -> None:
        self.failures += 1
        print(f"FAIL {message}", file=sys.stderr, flush=True)

    def same(self, message: str, expected: object, actual: object) -> None:
        if expected == actual:
            self.ok(message)
        else:
            self.bad(f"{message} (expected [{expected}], got [{actual}])")

    def report(self) -> None:
        if self.failures:
            print(f"{self.failures} {self.name} failed", file=sys.stderr, flush=True)
            raise SystemExit(1)
        print(f"{self.name} passed", flush=True)


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"{result.stdout or ''}{result.stderr or ''}"
        )
    return result


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run("docker", *args, check=check)


def combined(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout or ''}{result.stderr or ''}"


class Case:
    """Outcome of one isolated container case."""

    def __init__(self, status: int | None, output: str) -> None:
        # ``status`` is None when the attempt never printed an end marker.
        self.status = status
        self.output = output

    @property
    def marked(self) -> bool:
        return self.status is not None

    @property
    def passed(self) -> bool:
        return self.status == 0 and PASSED in self.output

    def body(self) -> list[str]:
        """Output without the lifecycle markers."""
        keep = []
        for line in self.output.splitlines():
            if line == STARTED or line == PASSED or line.startswith(ENDED):
                continue
            keep.append(line)
        return keep


def case(
    image: str,
    script: str,
    env: dict[str, str] | None = None,
    binds: tuple[str, ...] = (),
    prepare: str = "",
    source: Path | None = None,
    attempts: int = 3,
) -> Case:
    """Run one isolated container case, retrying only an unmarked attempt.

    ``binds`` are extra ``host:container`` mounts for fixtures; ``prepare`` runs
    inside the same marker scope; ``source`` is a repository-root-shaped
    directory (``scripts/`` plus ``config/``), mounted instead of the working
    tree so a mutation control can prove that a guard is actually live.

    The staging step mirrors the Dockerfile ``COPY`` lines exactly and runs
    inside the marker scope, so a path the image needs but the harness forgets
    is a product-visible failure instead of a silently swallowed ``cp``.
    """
    source = source or SOURCE
    body = (
        "set -e\n"
        "cp -a /src/config/crontab /src/config/Caddyfile /src/scripts/restic.sh /src/scripts/services.sh /\n"
        "chmod +x /services.sh\n"
        "mkdir -p /entrypoint && cp -a /src/scripts/entrypoint/. /entrypoint/\n"
        "cp -a /src/scripts/entrypoint.sh /entrypoint.sh\n"
        "chmod +x /restic.sh /entrypoint.sh\n"
    ) + "\n".join(
        (
            f"printf '%s\\n' '{STARTED}'",
            "status=0",
            "(",
            "set -e",
            prepare,
            script,
            ") || status=$?",
            f"printf '%s%s\\n' '{ENDED}' \"$status\"",
            f"[ \"$status\" -ne 0 ] || printf '%s\\n' '{PASSED}'",
            "exit \"$status\"",
            "",
        )
    )
    env_args = [a for k, v in (env or {}).items() for a in ("--env", f"{k}={v}")]
    bind_args = [a for b in binds for a in ("-v", b)]
    last = Case(None, "")
    for _ in range(attempts):
        result = docker(
            "run", "--rm", "--entrypoint", "bash",
            "-v", f"{source}:/src:ro",
            *bind_args, *env_args,
            image, "-c", body, check=False,
        )
        output = combined(result)
        match = re.search(rf"^{re.escape(ENDED)}([0-9]+)$", output, re.MULTILINE)
        last = Case(None if match is None else int(match.group(1)), output)
        if last.marked:
            return last
    return last


def expect(suite: Suite, accepted: bool, result: Case, success: str, failure: str) -> None:
    """Record one case, converting an unmarked attempt into a harness failure."""
    if not result.marked:
        suite.bad(f"{failure}: case was interrupted before its end marker")
    elif result.passed != accepted:
        suite.bad(failure)
    else:
        suite.ok(success)
        return
    print(result.output, file=sys.stderr)


def expect_pass(suite: Suite, result: Case, success: str, failure: str) -> None:
    expect(suite, True, result, success, failure)


def expect_red(suite: Suite, result: Case, success: str, failure: str) -> None:
    """A mutation control: the case must fail once the guard is removed."""
    expect(suite, False, result, success, failure)


def mutate(relative: str, replacements: tuple[tuple[str, str], ...], into: Path) -> Path:
    """Copy ``scripts/`` and ``config/`` with a deliberately weakened file."""
    target = into / "src"
    for name in ("scripts", "config"):
        shutil.copytree(REPO_ROOT / name, target / name)
    path = target / relative
    text = path.read_text(encoding="utf-8")
    for old, new in replacements:
        if old not in text:
            raise SystemExit(f"mutation anchor missing in {relative}: {old!r}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    return target
