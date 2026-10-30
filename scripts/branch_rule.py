#!/usr/bin/env python3
"""Compare the live `main` branch rule with the copy recorded in the repo.

The rule is the only guard that stops an unvalidated commit from reaching
`main`, and it lives outside the repository: `GITHUB_TOKEN` cannot read it
(there is no `administration` scope), and nothing edits it on merge. So the
rule is recorded here, the required context is tied to a real job by a test,
and this script makes re-reading it a single command instead of memory.

    gh api repos/<owner>/<repo>/branches/main/protection > live.json
    branch_rule.py check live.json

Exit codes: `0` matches, `1` differs, `2` unreadable.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RECORDED = Path(
    os.environ.get("BRANCH_RULE_RECORDED")
    or Path(__file__).resolve().parents[1] / "docs/branch_protection.json"
)


class Unreadable(Exception):
    """A document does not carry the fields the comparison needs."""


def summary(document: object) -> dict[str, object]:
    """The part of a protection payload that actually gates a merge."""
    if not isinstance(document, dict):
        raise Unreadable("protection is not an object")
    checks = document.get("required_status_checks")
    if not isinstance(checks, dict):
        raise Unreadable("no required_status_checks")
    contexts = checks.get("contexts")
    if not isinstance(contexts, list) or not all(isinstance(c, str) for c in contexts):
        raise Unreadable("required_status_checks.contexts is not a list of strings")
    admins = document.get("enforce_admins")
    if not isinstance(admins, dict) or not isinstance(admins.get("enabled"), bool):
        raise Unreadable("enforce_admins.enabled is not a boolean")
    strict = checks.get("strict")
    if not isinstance(strict, bool):
        raise Unreadable("required_status_checks.strict is not a boolean")
    return {"contexts": sorted(contexts), "strict": strict, "enforce_admins": admins["enabled"]}


def compare(recorded: object, live: object) -> list[str]:
    """Return one line per difference; empty means the rule is unchanged."""
    want, have = summary(recorded), summary(live)
    problems = []
    if want["contexts"] != have["contexts"]:
        problems.append(
            f"required contexts are {have['contexts']}, recorded {want['contexts']}"
        )
    for field in ("strict", "enforce_admins"):
        if want[field] != have[field]:
            problems.append(f"{field} is {have[field]}, recorded {want[field]}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <live-protection-json>", file=sys.stderr)
        return 2
    try:
        recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
        live = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        problems = compare(recorded, live)
    except (OSError, json.JSONDecodeError, Unreadable) as error:
        print(f"cannot compare: {error}", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(f"RULE DRIFT: {problem}", file=sys.stderr)
        return 1
    summary_doc = summary(live)
    print(
        "rule matches: contexts={} strict={} enforce_admins={}".format(
            ",".join(summary_doc["contexts"]), summary_doc["strict"], summary_doc["enforce_admins"]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
