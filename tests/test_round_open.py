#!/usr/bin/env python3
"""The round gate derives the audit baseline instead of trusting a count.

A written "N commits" is already stale when the commit that records it merges,
so the script has to resolve the baseline and count what still lies past it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/round_open.py"
HEAD = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
).stdout.strip()


def _base() -> str:
    """A commit five back, or the deepest one a shallow clone still has.

    Actions checks out a merge commit with limited history, so `HEAD~5` can be
    absent; the count is always derived from whatever `BASE` really is.
    """
    for rev in ("HEAD~5", "HEAD~1"):
        done = subprocess.run(
            ["git", "rev-parse", "--verify", rev],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode == 0:
            return done.stdout.strip()
    return HEAD


BASE = _base()
TOTAL = subprocess.run(
    ["git", "rev-list", "--count", "HEAD"],
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()
# `BASE..HEAD` is not always five: CI checks out a merge commit for the PR, so
# the distance is computed rather than assumed.
OWED = subprocess.run(
    ["git", "rev-list", "--count", f"{BASE}..HEAD"],
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()


def doc(baseline: str | None) -> str:
    line = "" if baseline is None else f"Audit baseline: every commit reachable through `{baseline}` on a day.\n"
    return f"# Issues\n\n{line}\n## Host Policy\n\n### body\n"


def run(document: str, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "issues.md"
        path.write_text(document, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "ROUND_OPEN_DOC": str(path), "ROUND_OPEN_ROOT": str(ROOT)},
        )


class RoundOpenTest(unittest.TestCase):
    def test_counts_what_is_still_owed(self) -> None:
        result = run(doc(BASE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"baseline commit: `{BASE}`", result.stdout)
        self.assertIn(f"commits past the baseline, to audit this round: {OWED}", result.stdout)
        self.assertIn(f"commits reachable from HEAD: {TOTAL}", result.stdout)

    def test_current_head_owes_nothing(self) -> None:
        """Boundary: a baseline at HEAD leaves no commit to audit."""
        result = run(doc(HEAD))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("commits past the baseline, to audit this round: 0", result.stdout)

    def test_short_sha_is_accepted(self) -> None:
        result = run(doc(BASE[:7]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"to audit this round: {OWED}", result.stdout)

    def test_missing_baseline_fails(self) -> None:
        result = run(doc(None))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("no audit baseline", result.stderr)

    def test_unresolvable_baseline_fails_closed(self) -> None:
        result = run(doc("0" * 40))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("does not resolve", result.stderr)

    def test_matrix_and_residuals_are_still_printed(self) -> None:
        result = run(doc(BASE))
        self.assertIn("| Class | History |", result.stdout)
        self.assertIn("| Host Policy |", result.stdout)
        self.assertIn("## Open residuals", result.stdout)

    def test_baseline_resolves_to_a_full_sha(self) -> None:
        """Boundary: a short SHA still prints the commit it resolved to."""
        result = run(doc(BASE[:7]))
        self.assertIn(f"baseline commit: `{BASE[:7]}`", result.stdout)


class RoundOpenMutationTest(unittest.TestCase):
    """Remove the derivation in turn; the count stops meaning anything."""

    def setUp(self) -> None:
        self.source = SCRIPT.read_text(encoding="utf-8")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def mutate(
        self, *replacements: tuple[str, str], baseline: str = BASE
    ) -> subprocess.CompletedProcess[str]:
        text = self.source
        for old, new in replacements:
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        script = Path(self.tmp.name) / "mutated.py"
        script.write_text(text, encoding="utf-8")
        return run(doc(baseline), script)

    def test_without_the_baseline_lookup_nothing_is_owed(self) -> None:
        """Left alone, the range falls back to all of `HEAD`."""
        result = self.mutate(('f"{baseline}..HEAD"', '"HEAD"'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(f"to audit this round: {OWED}", result.stdout)
        self.assertIn(f"to audit this round: {TOTAL}", result.stdout)

    def test_without_the_regex_the_document_looks_baseline_free(self) -> None:
        result = self.mutate(
            (r"^Audit baseline: every commit reachable through `([0-9a-f]{7,40})`", "^nothing here")
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("no audit baseline", result.stderr)

    def test_without_the_resolve_check_the_typo_loses_its_name(self) -> None:
        """`rev-list` also fails on a typo, but only as `git rev-list`: the
        reader cannot tell a bad baseline from a missing repository."""
        guarded = run(doc("0" * 40))
        self.assertEqual(guarded.returncode, 2, guarded.stdout)
        self.assertIn("does not resolve", guarded.stderr)

        result = self.mutate(
            ('_git("rev-parse", "--verify", f"{baseline}^{{commit}}")', "''"),
            baseline="0" * 40,
        )
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertNotIn("does not resolve", result.stderr)


if __name__ == "__main__":
    unittest.main()
