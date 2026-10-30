#!/usr/bin/env python3
"""The round gate derives the audit baseline instead of trusting a count.

A written "N commits" is already stale when the commit that records it merges,
so the script has to resolve the baseline and count what still lies past it.
"""

from __future__ import annotations

import os
import re
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


def recorded(ref: str) -> str:
    """The baseline committed at `ref` — what a real round has to extend."""
    shown = subprocess.run(
        ["git", "show", f"{ref}:docs/recurring_issues.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = re.search(
        r"(?m)^Audit baseline: every commit reachable through `([0-9a-f]{7,40})`", shown
    )
    assert found is not None
    return found.group(1)


# The predecessor a round opening on `BASE` would legitimately name.
RECORDED = recorded(BASE)


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


def doc(baseline: str | None, previous: str | None = RECORDED) -> str:
    """A real baseline paragraph, including the one it claims to extend."""
    if baseline is None:
        return "# Issues\n\n## Host Policy\n\n### body\n"
    at = "" if previous is None else f", extending the previous baseline at `{previous}`"
    line = f"Audit baseline: every commit reachable through `{baseline}` on a day{at}.\n"
    return f"# Issues\n\n{line}\n## Host Policy\n\n### body\n"


def run(
    document: str, script: Path = SCRIPT, *args: str
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "issues.md"
        path.write_text(document, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(script), *args],
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

    def test_require_closed_rejects_an_open_round(self) -> None:
        """The count only gated anything once this exit code existed."""
        result = run(doc(BASE), SCRIPT, "--require-closed")
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn("still reachable past the baseline", result.stderr)
        self.assertIn(f"commits past the baseline, to audit this round: {OWED}", result.stdout)

    def test_require_closed_accepts_a_closed_round(self) -> None:
        result = run(doc(HEAD), SCRIPT, "--require-closed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("to audit this round: 0", result.stdout)

    def test_require_closed_still_fails_closed_on_a_bad_baseline(self) -> None:
        self.assertEqual(run(doc(None), SCRIPT, "--require-closed").returncode, 1)
        self.assertEqual(run(doc("0" * 40), SCRIPT, "--require-closed").returncode, 2)

    def test_unknown_argument_is_refused(self) -> None:
        result = run(doc(BASE), SCRIPT, "--require-closd")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown argument", result.stderr)

    def test_entry_at_the_baseline_is_closed(self) -> None:
        """The shape a clean round has: the baseline is the base commit and the
        base records exactly the predecessor it extends."""
        result = run(doc(BASE, recorded(BASE)), SCRIPT, "--require-closed", "--entry", BASE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("which this round cannot cover: 0", result.stdout)

    def test_a_baseline_past_the_base_still_covers_it(self) -> None:
        """A round that audited further than its base owes nothing there."""
        result = run(doc(HEAD, RECORDED), SCRIPT, "--require-closed", "--entry", BASE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("which this round cannot cover: 0", result.stdout)

    def test_entry_ahead_of_the_baseline_fails(self) -> None:
        """Unaudited commits already on the base branch cannot be covered by
        this round's matrix, so the gate has to refuse the merge."""
        result = run(doc(RECORDED, RECORDED), SCRIPT, "--require-closed", "--entry", BASE)
        self.assertEqual(result.returncode, 3, result.stdout)
        gap = subprocess.run(
            ["git", "rev-list", "--count", f"{RECORDED}..{BASE}"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertIn(f"which this round cannot cover: {gap}", result.stdout)
        self.assertIn(f"{gap} commit(s) still reachable past the baseline", result.stderr)

    def test_entry_without_a_value_is_refused(self) -> None:
        result = run(doc(BASE), SCRIPT, "--require-closed", "--entry")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--entry needs a ref", result.stderr)

    def test_unresolvable_entry_fails_closed(self) -> None:
        result = run(doc(BASE), SCRIPT, "--require-closed", "--entry", "0" * 40)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("entry", result.stderr)

    def test_baseline_must_descend_from_the_previous_one(self) -> None:
        """Boundary: the ordinary case, a baseline further along the same line."""
        result = run(doc(HEAD, BASE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"- baseline extends `{BASE}`", result.stdout)

    def test_baseline_may_equal_the_previous_one(self) -> None:
        """A round that audits nothing still records the chain unchanged."""
        self.assertEqual(run(doc(BASE, BASE)).returncode, 0)

    def test_baseline_missing_its_predecessor_exits_four(self) -> None:
        """A bare hash proves nothing about what was read, so it is refused
        even though the gate itself would pass."""
        result = run(doc(BASE, None), SCRIPT, "--require-closed", "--entry", BASE)
        self.assertEqual(result.returncode, 4, result.stdout)
        self.assertIn("no previous baseline", result.stderr)

    def test_baseline_forked_off_the_chain_is_refused(self) -> None:
        """The exact hole: paste an unrelated newer hash and the gap closes
        without auditing a single commit, so the commons in between never get
        a row."""
        result = run(doc(BASE, HEAD), SCRIPT, "--require-closed", "--entry", BASE)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("does not descend from the previous baseline", result.stderr)

    def test_unresolvable_predecessor_fails_closed(self) -> None:
        result = run(doc(BASE, "0" * 40), SCRIPT, "--require-closed", "--entry", BASE)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("does not descend", result.stderr)

    def test_predecessor_further_down_the_file_is_not_used(self) -> None:
        """The chain must be read from the baseline paragraph, not from the
        first hash in the file that happens to follow those words."""
        document = doc(BASE, None) + (
            f"\n## Later\n\nHistory: extending the previous baseline at `{BASE}`.\n"
        )
        result = run(document)
        self.assertEqual(result.returncode, 4, result.stdout)

    def test_predecessor_must_be_the_one_the_base_records(self) -> None:
        """Both hashes are written in one edit, so the document can only
        certify itself. The base commit has to record the predecessor too."""
        # A real ancestor of `BASE`, but not the one the base records.
        older = subprocess.run(
            ["git", "rev-parse", f"{BASE}~1"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        result = run(doc(BASE, older), SCRIPT, "--require-closed", "--entry", BASE)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("is not the baseline recorded at the base commit", result.stderr)

    def test_report_mode_still_checks_the_recorded_predecessor(self) -> None:
        """Without `--entry` there is no base to compare, so any ancestor pair
        is accepted — the check is the gate's, not the printer's."""
        self.assertEqual(run(doc(HEAD, BASE)).returncode, 0)

    def test_base_without_a_recorded_baseline_exits_five(self) -> None:
        """The first commit of a repository has nothing to extend, and a round
        opening on it cannot infer one."""
        root = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()[0]
        result = run(doc(BASE, recorded(BASE)), SCRIPT, "--require-closed", "--entry", root)
        self.assertEqual(result.returncode, 5, result.stdout)
        self.assertIn("records no audit baseline", result.stderr)


class RoundOpenMutationTest(unittest.TestCase):
    """Remove the derivation in turn; the count stops meaning anything."""

    def setUp(self) -> None:
        self.source = SCRIPT.read_text(encoding="utf-8")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def mutate(
        self,
        *replacements: tuple[str, str],
        baseline: str = BASE,
        previous: str | None = BASE,
        args: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        text = self.source
        for old, new in replacements:
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        script = Path(self.tmp.name) / "mutated.py"
        script.write_text(text, encoding="utf-8")
        return run(doc(baseline, previous), script, *args)

    def test_without_the_gate_the_open_round_exits_zero(self) -> None:
        """Mutation control for the gate itself: drop the exit code, not the count."""
        dropped = self.mutate(
            ("if require_closed:", "if False:"),
            args=("--require-closed",),
        )
        self.assertEqual(dropped.returncode, 0, dropped.stderr)
        self.assertIn(f"to audit this round: {OWED}", dropped.stdout)

    def test_gate_that_ignores_the_count_never_fires(self) -> None:
        inverted = self.mutate(
            ('if missing != "0":', 'if missing == "0":'),
            args=("--require-closed",),
            baseline=HEAD,
        )
        self.assertEqual(inverted.returncode, 3, inverted.stdout)

    def test_gate_measuring_head_would_block_every_round(self) -> None:
        """Mutation control for the entry ref: count the round's own commits.

        They are past the baseline by design, so a gate on them never passes
        and a reader deletes the step instead of fixing the baseline.
        """
        result = self.mutate(
            ("missing = gap if gap is not None else owed", "missing = owed"),
            args=("--require-closed", "--entry", BASE),
            baseline=BASE,
            previous=recorded(BASE),
        )
        self.assertEqual(result.returncode, 3, result.stdout)

    def test_without_the_lineage_check_any_hash_is_a_baseline(self) -> None:
        """Mutation control for the chain: make the check pass unconditionally.

        The fork case is the one that matters — a hash from another line closes
        the gap exactly as well as an audited one, so the gate stays green
        while the commits in between are never given a row.
        """
        forked = self.mutate(
            ('_git("merge-base", "--is-ancestor", previous, baseline)', "''"),
            baseline=BASE,
            previous=HEAD,
        )
        self.assertEqual(forked.returncode, 0, forked.stderr)

        unlinked = self.mutate(
            ("        lineage(baseline, previous.group(1))\n", ""),
            ("            file=sys.stderr,\n        )\n        return 4\n",
             "            file=sys.stderr,\n        )\n"),
        )
        self.assertEqual(unlinked.returncode, 0, unlinked.stderr)

    def test_lineage_compared_the_wrong_way_round_is_refused(self) -> None:
        """Mutation control: swap the two hashes so ancestry is checked
        backwards — a legitimate forward baseline then looks forked."""
        reversed_ = self.mutate(
            ('_git("merge-base", "--is-ancestor", previous, baseline)',
             '_git("merge-base", "--is-ancestor", baseline, previous)'),
            baseline=HEAD,
            previous=BASE,
        )
        self.assertEqual(reversed_.returncode, 2, reversed_.stdout)

    def test_without_the_recorded_check_the_pair_certifies_itself(self) -> None:
        """Mutation control for the base comparison: drop it and any ancestor
        becomes an acceptable predecessor, including one nobody audited."""
        older = subprocess.run(
            ["git", "rev-parse", f"{BASE}~1"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        # Blanked rather than deleted: the `except Missing` clause must keep a
        # body, and an unreachable `pass` expresses "checked nothing" better.
        self_check = self.mutate(
            ("            continues(previous.group(1), recorded_baseline(entry))",
             "            pass"),
            baseline=BASE,
            previous=older,
            args=("--require-closed", "--entry", BASE),
        )
        self.assertEqual(self_check.returncode, 0, self_check.stderr)

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
