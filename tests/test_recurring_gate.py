#!/usr/bin/env python3
"""Execute the mandatory recurring-class contract and mutation gate."""

from __future__ import annotations

import re
import json
import os
import stat
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recurring_issues.md"
TARGET_MODE = ROOT / "docs/target_mode.md"
ROUND_OPEN = ROOT / "scripts/round_open.py"
PR_TEMPLATE = ROOT / ".github/PULL_REQUEST_TEMPLATE.md"
README = ROOT / "README.md"
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOWS = ROOT / ".github/workflows"
REBUILD = ROOT / "scripts/rebuild.sh"

RECURRING_GOAL_GATE_CLAUSES = (
    "Every new, resumed, review, optimization, scoring, release, and post-merge round",
    "one evidence row per class",
    "positive, boundary, negative, and",
    "Run the named mutation control",
    "repair the invariant in the same round",
    "Do not optimize, score, merge,",
    "deploy, or claim completion",
    "without creating",
    "per-round files",
)

CLASS_EVIDENCE = {
    "Host Policy And Health Escape": (
        "config/Caddyfile:@denied",
        "tests/runtime_smoke.py:check_host_policy",
    ),
    "Client IP Attribution": (
        "config/Caddyfile:header_up",
        "tests/runtime_smoke.py:check_client_ip",
    ),
    "Consistent SQLite Backup": (
        "scripts/restic.sh:sqlite_snapshot",
        "tests/runtime_smoke.py:check_sqlite_consistency",
    ),
    "Restore Isolation And Confirmation": (
        "scripts/restic.sh:restore_test",
        "tests/runtime_smoke.py:check_restore_isolation",
    ),
    "Bounded Diagnostics": (
        "scripts/restic.sh:trim_logs",
        "tests/runtime_smoke.py:check_bounded_diagnostics",
    ),
    "State, Lock And Process Supervision": (
        "scripts/services.sh:supervise",
        "tests/runtime_smoke.py:check_process_supervision",
    ),
    "Address And Secret Input Audit": (
        "scripts/entrypoint/config.sh:valid_dns_name",
        "tests/runtime_smoke.py:check_address_audit",
    ),
    "Supply Chain And Public Surface": (
        ".github/workflows/ghcr.yaml:digest",
        "tests/test_recurring_gate.py:test_digest_mutation_allows_mutable_base",
    ),
    "Simplification Rule": (
        "tests/test_recurring_gate.py:assert_recurrence_contract",
        "tests/test_recurring_gate.py:test_contract_mutation_removes_authoritative_field",
    ),
}

SENSITIVE_PATTERNS = (
    ("provider token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("AWS key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bot token", re.compile(r"(?<![0-9])[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}")),
    ("R2 endpoint", re.compile(r"s3://[a-f0-9]{20,}\.r2\.cloudflarestorage\.com")),
    ("Slack webhook", re.compile(r"hooks\.slack\.com/services/T[A-Z0-9]{8,}")),
)


def sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


def assert_recurrence_contract(case: unittest.TestCase, text: str) -> None:
    found = sections(text)
    for required in ("Target-Mode Prompt", "Every-Round Goal Gate"):
        case.assertIn(required, found)
    # The document wraps for readability, so compare on normalised whitespace.
    gate = " ".join((found["Target-Mode Prompt"] + found["Every-Round Goal Gate"]).split())
    for clause in RECURRING_GOAL_GATE_CLAUSES:
        case.assertIn(" ".join(clause.split()), gate)

    classes = set(found) - {"Target-Mode Prompt", "Every-Round Goal Gate"}
    case.assertEqual(classes, set(CLASS_EVIDENCE))
    for title, (authority, mutation) in CLASS_EVIDENCE.items():
        body = found[title]
        for field in (
            "History:",
            "Final decision:",
            "Authority:",
            "Mutation control:",
            "Regression gate:",
        ):
            case.assertIn(field, body, f"{title} is missing {field}")
        case.assertIn(authority, body, f"{title} names the wrong authority")
        case.assertIn(mutation, body, f"{title} names the wrong mutation control")


TARGET_MODE_SECTIONS = (
    "Role",
    "Hard rules",
    "Round entry gate",
    "Matrix",
    "Report order",
    "Rejection criteria",
    "Residual list",
)

TARGET_MODE_COLUMNS = (
    "Class",
    "History",
    "Authoritative guard",
    "Positive",
    "Boundary",
    "Negative",
    "Mutation control",
    "Observed result",
)


def sections_of(text: str) -> dict[str, str]:
    """Split a markdown document into `## ` sections, dropping code fences."""
    found: dict[str, str] = {}
    matches = list(re.finditer(r"(?m)^## (.+)$", text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        found[match.group(1)] = text[match.end():end]
    return found


def assert_target_mode_is_actionable(
    case: unittest.TestCase, mode: str, issues: str, round_open: str
) -> None:
    """A protocol only in a chat message has to be carried by hand every round.

    The procedures are therefore pinned here: the invariants must point at the
    protocol, the protocol must keep every section, the matrix columns must
    match the generated skeleton, and the entry script must exist and be the
    thing the round is told to run.
    """
    case.assertIn("docs/target_mode.md", issues)
    case.assertIn("scripts/round_open.py", issues)

    found = sections_of(mode)
    for required in TARGET_MODE_SECTIONS:
        case.assertIn(required, found, f"docs/target_mode.md is missing {required}")

    # The shipped skeleton and the written columns are one contract, in order.
    header = "| " + " | ".join(TARGET_MODE_COLUMNS) + " |"
    case.assertIn(header, found["Matrix"])
    listed = re.search(r"(?s)COLUMNS = \((.*?)\n\)", round_open)
    case.assertIsNotNone(listed, "scripts/round_open.py lost its column tuple")
    case.assertEqual(
        tuple(re.findall(r'"([^"]+)"', listed.group(1))), TARGET_MODE_COLUMNS
    )
    case.assertIn('" | ".join(COLUMNS)', round_open, "the template is never printed")

    # The rejection list is the thing that voids a round; a subset is not one.
    for clause in ("a matrix column or row is missing",
                   "no control turned red",
                   "presented as this round's evidence",
                   "without the post-deploy reads",
                   "reported as a successful deployment",
                   "written through the shell",
                   "residual list grew"):
        case.assertIn(clause, found["Rejection criteria"])


def assert_residuals_live_in_one_place(case: unittest.TestCase, readme: str) -> None:
    """A residual written down nowhere is indistinguishable from a closed one."""
    case.assertIn("\n## ⚠️ Residual risk\n", readme)


def assert_pr_template_carries_the_matrix(case: unittest.TestCase, template: str) -> None:
    """The matrix is required in every PR, so it must arrive prefilled.

    The protocol says the matrix goes into the PR body. Without a template that
    is a habit; with one, the table and the four report headings are already in
    the editor and only the evidence is missing.
    """
    header = "| " + " | ".join(TARGET_MODE_COLUMNS) + " |"
    case.assertIn(header, template, "the PR template lost the matrix header")
    for heading in ("## Conclusion", "## Matrix", "## Changes", "## Verification", "## Residual"):
        case.assertIn(f"{heading}\n", template)


def assert_drift_check_exists(case: unittest.TestCase, drift: str, build: str) -> None:
    """A hand deploy reaches production first, so the detector must exist.

    It has to run without being asked (a push and a cron), read a pinned CLI,
    bound every read, and hand the decision to the script instead of deciding
    inline. A missing read must abort — "cannot tell" is not "in sync".
    """
    case.assertIn("    branches:\n      - main\n", drift)
    crons = schedule_entries(drift)
    case.assertEqual(len(crons), 1, "one daily slot is expected")
    case.assertEqual(crons[0].split()[2:], ["*", "*", "*"], "drift must run every day")
    case.assertIn("  workflow_dispatch: {}", drift)

    # The CLI is a pinned GitHub release, never the action that asks Fly for
    # `latest` while Fly is the thing being tested.
    case.assertNotIn("superfly/flyctl-actions", drift)
    case.assertRegex(drift, r"FLYCTL_VERSION: \d+\.\d+\.\d+")
    case.assertRegex(drift, r"FLYCTL_SHA256: [0-9a-f]{64}")
    version = re.search(r"FLYCTL_VERSION: (\d+\.\d+\.\d+)", drift).group(1)
    case.assertIn(f"FLYCTL_ASSET: flyctl_{version}_Linux_x86_64.tar.gz", drift)
    case.assertIn("github.com/superfly/flyctl/releases/download/v${FLYCTL_VERSION}", drift)
    case.assertIn("sha256sum -c -", drift)

    # Every read is bounded, redirected, and aborts before the comparison.
    case.assertIn("timeout-minutes: ", drift)
    case.assertIn("--retry 3 --max-time 120", drift)
    case.assertEqual(drift.count("timeout 60 flyctl "), 2)
    case.assertIn("timeout 120 docker buildx imagetools inspect", drift)
    case.assertIn("--raw >", drift)
    case.assertIn("set -euo pipefail", drift)
    case.assertIn('"${FLY_API_TOKEN:?', drift)

    # The app comes from `fly.toml`, never from a local default.
    case.assertIn("sed -n 's/^app = \"\\(.*\\)\"$/\\1/p' fly.toml", drift)
    case.assertIn('flyctl releases -a "$app"', drift)
    case.assertIn('flyctl status -a "$app"', drift)

    # An undefined `REGISTRY`/`IMAGE_NAME` silently probes `/latest`, which
    # docker resolves as a local image and every run then reads as unreadable.
    case.assertIn("  REGISTRY: ghcr.io\n", drift)
    case.assertIn("  IMAGE_NAME: ${{ github.repository }}\n", drift)
    case.assertIn('"${REGISTRY}/${IMAGE_NAME}:latest"', drift)

    # The decision and its failure modes live in the tested script.
    case.assertIn("python3 scripts/deploy_drift.py", drift)
    case.assertNotIn("jq -e", drift, "the comparison must not fork into inline jq")

    # The publish trigger must not filter out the workflow that watches it.
    ignored = push_paths_ignore(build)
    case.assertIn(".github/**", ignored, "workflow edits must still rebuild")
    # The script is not copied into the image either, so editing it must not
    # rebuild; both lists have to agree on that.
    case.assertIn("scripts/deploy_drift.py", PUSH_IGNORABLE)
    case.assertIn("scripts/deploy_drift.py", ignored)


def assert_drift_workflow_is_registered(case: unittest.TestCase, workflows: dict[str, str]) -> None:
    """A detector nothing runs is a document, not a gate."""
    case.assertIn("deploy-drift.yml", workflows)


def scan_for_sensitive_data(text: str) -> list[str]:
    return [label for label, pattern in SENSITIVE_PATTERNS if pattern.search(text)]


def assert_base_image_is_pinned(case: unittest.TestCase, text: str) -> None:
    instructions = [line for line in text.splitlines() if line.startswith("FROM ")]
    case.assertEqual(len(instructions), 1, "exactly one FROM is expected")
    case.assertRegex(
        instructions[0],
        r"^FROM [^\s@]+@sha256:[0-9a-f]{64}$",
        "the base image must be pinned by index digest",
    )


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def assert_app_name_is_recorded(
    case: unittest.TestCase, fly: str, readme: str, build: str
) -> None:
    """The Fly app name is one value, checked against every place that uses it."""
    app = tomllib.loads(fly).get("app")
    case.assertIsInstance(app, str)
    case.assertRegex(app, r"^[a-z0-9][a-z0-9-]*$")
    case.assertIn(f"fly apps create {app}", readme)
    # The deploy token is scoped to the same app, so it is a third rename site.
    case.assertIn(f"fly tokens create deploy -a {app}", readme)
    # The note is what stops the next rename from updating only one place.
    case.assertIn("rename must be synced", fly)
    # CI resolves the name from `fly.toml` and passes `-a` everywhere, so a
    # stale local default cannot redirect the deploy or the verification.
    case.assertIn('sed -n \'s/^app = "\\(.*\\)"$/\\1/p\' fly.toml', build)
    case.assertIn('flyctl deploy -a "$app"', build)
    case.assertIn('flyctl releases -a "$APP"', build)
    case.assertIn('flyctl status -a "$APP"', build)


def unpinned_uses(text: str) -> list[str]:
    """Return ``line: reference`` for every ``uses:`` that is not a 40-char SHA."""
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"\s*(?:-\s+)?uses:\s*(\S+)", line)
        if not match:
            continue
        reference = match.group(1)
        if reference.startswith("./"):
            continue
        if not re.search(r"@[0-9a-f]{40}$", reference):
            problems.append(f"{number}: {reference}")
    return problems


def assert_actions_are_pinned(case: unittest.TestCase, workflows: dict[str, str]) -> None:
    for name, text in workflows.items():
        for problem in unpinned_uses(text):
            case.fail(f"{name}:{problem} is a mutable action reference")
        for problem in unlabelled_uses(text):
            case.fail(f"{name}:{problem} is pinned but carries no version comment")


def unlabelled_uses(text: str) -> list[str]:
    """A pin without its human version is unreadable and cannot be reviewed.

    A bare 40-character hash hides which release is in use, so an auditor can
    neither spot an outdated action nor confirm that a bump keeps the same
    major. Every pin carries its tag as a trailing comment.
    """
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"\s*(?:-\s+)?uses:\s*(\S+)", line)
        if not match:
            continue
        reference = match.group(1)
        if reference.startswith("./"):
            continue
        if not re.search(r"@[0-9a-f]{40}\s+#\s*\S+", line):
            problems.append(f"{number}: {reference}")
    return problems


def assert_digest_names_one_image_manifest(case: unittest.TestCase, build: str) -> None:
    """Provenance adds attestation children; the digest must stay the amd64 image."""
    case.assertNotIn("provenance: false", build)
    case.assertRegex(build, r"(?m)^\s*provenance: (true|mode=max)$")
    step = build.split("Record the published amd64 digest")[1].split("Smoke test the published digest")[0]
    # Both halves of the platform filter, so an attestation child (whose
    # platform is `unknown/unknown`) can never be published as the digest.
    case.assertIn('eq .Platform.Architecture "amd64"', step)
    case.assertIn('eq .Platform.OS "linux"', step)
    # Exactly one match is required rather than taking whatever comes back; an
    # unfiltered index yields every child (image + attestation), which fails.
    case.assertIn("wc -l)\" -eq 1", step)
    case.assertIn('[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]', step)


def schedule_entries(text: str) -> list[str]:
    """Return the `cron:` values declared under the top-level `on:` key."""
    head = text.split("\nenv:", 1)[0]
    return re.findall(r"^\s*-\s*cron:\s*'([^']+)'\s*$", head, re.MULTILINE)


def assert_weekly_rebuild_is_scheduled(case: unittest.TestCase, build: str) -> None:
    crons = schedule_entries(build)
    case.assertEqual(len(crons), 1, "exactly one rebuild schedule is expected")
    minute, hour, dom, month, dow = crons[0].split()
    case.assertRegex(minute, r"^\d{1,2}$")
    case.assertRegex(hour, r"^\d{1,2}$")
    case.assertEqual(dom, "*", "a weekly rebuild must not pin a day of month")
    case.assertEqual(month, "*")
    case.assertEqual(dow, "1", "the rebuild must run once a week")
    # A weekly schedule changes nothing if the chain is still gated on `main`.
    case.assertIn("github.ref == 'refs/heads/main'", build)


def step_script(workflow_text: str, name: str) -> str:
    """Return one step's shell body, so its guard can actually be executed."""
    block = workflow_text.split(f"- name: {name}", 1)[1]
    return block.split("- name:", 1)[0].split("run: |\n", 1)[1]


PUSH_IGNORABLE = {
    ".gitignore",
    "README.md",
    "LICENSE",
    ".github/**",
    "docs/**",
    "tests/**",
    "scripts/rebuild.sh",
    "scripts/round_open.py",
    "scripts/deploy_drift.py",
}


def push_paths_ignore(build: str) -> set[str]:
    """Return the paths excluded from the deploy trigger."""
    section = build.split("\n  push:\n", 1)[1].split("\n  schedule:\n", 1)[0]
    block = section.split("    paths-ignore:\n", 1)
    if len(block) != 2:
        return set()
    return set(re.findall(r"^      - (\S+)$", block[1], re.MULTILINE))


def assert_docs_do_not_rebuild(
    case: unittest.TestCase, build: str, dockerfile: str, ignore: str
) -> None:
    """A docs edit cannot change the image, yet it rebuilt and redeployed.

    Ignoring a path the Dockerfile copies would turn a real change into a silent
    no-deploy, so the ignored set is checked against the build inputs: every
    ignored path must be absent from the context, and every copy source must
    stay triggerable.
    """
    ignored = push_paths_ignore(build)
    case.assertEqual(ignored, PUSH_IGNORABLE, "the ignored set drifted")

    for production in ("Dockerfile", "fly.toml", "config/**", "scripts/entrypoint/**",
                       "scripts/entrypoint.sh", "scripts/restic.sh", "scripts/services.sh",
                       ".dockerignore"):
        case.assertNotIn(production, ignored, f"{production} is a production input")

    copies = [line for line in dockerfile.splitlines() if line.startswith(("COPY ", "ADD "))]
    case.assertTrue(copies, "the Dockerfile must have been parsed")
    for copy in copies:
        for source in copy.split()[1:-1]:
            if "*" in source or source in (".", "./"):
                continue
            case.assertNotIn(source.rstrip("/"), ignored, f"the Dockerfile copies {source}")

    # Only paths the build context already dropped may be ignored; a `/**`
    # suffix is the same exclusion as the bare directory name.
    dropped = {
        line.strip().removesuffix("/**")
        for line in ignore.splitlines()
        if line.strip() and not line.startswith("#")
    }
    for path in ignored:
        case.assertIn(path.removesuffix("/**"), dropped, f"{path} is not in .dockerignore")


def assert_latest_follows_verification(case: unittest.TestCase, build: str) -> None:
    """A mutable `latest` published by the build is not a verified reference.

    The metadata step runs before the image exists, so a tag written there moves
    even when the digest smoke test, the deploy or the readiness verification
    fails. `latest` therefore stays out of the metadata and is retagged from the
    run tag only after the verifier has passed.
    """
    metadata = build.split("Extract Docker metadata", 1)[1].split("Build and push Docker image", 1)[0]
    case.assertNotIn("type=raw,value=latest", metadata)

    order = [
        "Build and push Docker image (Multi-Arch)",
        "Record the published amd64 digest",
        "Smoke test the published digest",
        "Verify the deployment",
        "Promote the verified image to latest",
    ]
    positions = [build.index(name) for name in order]
    case.assertEqual(positions, sorted(positions), "promotion must follow every gate")
    case.assertIn("packages: write", build.split("  deploy:", 1)[1])

    promote = step_script(build, "⬆️ Promote the verified image to latest")
    case.assertIn('--tag "${image}:latest" "${image}@${index_digest}"', promote)
    guard = 'if [ "$index_amd64" != "$AMD64_DIGEST" ]; then'
    case.assertIn(guard, promote)
    final = 'if [ "$latest_index" != "$index_digest" ] || [ "$latest_amd64" != "$AMD64_DIGEST" ]; then'
    case.assertIn(final, promote)

    def block(text: str, start: str) -> str:
        begin = text.index(start)
        end = re.search(r"\n\s*fi\s*\n", text[begin:])
        assert end is not None
        return text[begin:begin + end.end()]

    index = "sha256:" + "b" * 64
    script = (
        f'index_amd64={index}; index_digest={index}; AMD64_DIGEST={index}; '
        'latest_index=$LATEST_INDEX; latest_amd64=$LATEST_AMD64\n'
    ) + block(promote, final)
    env = os.environ | {"LATEST_INDEX": index, "LATEST_AMD64": index}
    case.assertEqual(subprocess.run(["/bin/bash", "-c", script], env=env).returncode, 0)
    other = "sha256:" + "c" * 64
    for key in ("LATEST_INDEX", "LATEST_AMD64"):
        case.assertNotEqual(
            subprocess.run(["/bin/bash", "-c", script], env=env | {key: other}).returncode, 0,
            f"{key} must be compared",
        )


def assert_scheduled_rebuild_needs_drift(case: unittest.TestCase, build: str, script: str) -> None:
    """A cron that rebuilds unconditionally spends a deploy on a current image.

    The scheduled run must therefore probe the *published* image and continue
    only on a positive, well-formed upgrade count; a failed probe has to fail
    closed instead of silently reading as "no changes".
    """
    # Only scheduled runs fan out through the decision job.
    case.assertIn("needs: [rebuild_check, validate]", build)
    gate = build.split("rebuild_check:", 1)[1].split("  build:", 1)[0]
    case.assertIn('[ "$GITHUB_EVENT_NAME" = schedule ]', gate)
    # A manual run cannot normally reach the probe, so it carries a `probe`
    # mode; the default stays `force` and must not be probed.
    case.assertIn("MODE: ${{ inputs.mode || 'force' }}", build)
    case.assertIn('"$GITHUB_EVENT_NAME" = workflow_dispatch ] && [ "$MODE" = probe ]', gate)
    case.assertIn('"${REGISTRY}/${IMAGE_NAME}:latest"', gate)
    case.assertIn('[[ "$count" =~ ^[0-9]+$ ]]', gate)
    case.assertIn('if [ "$count" -ne 0 ]; then', gate)
    case.assertIn("needed=true", gate.split("if [ \"$GITHUB_EVENT_NAME\" = schedule ]", 1)[0],
                  "a push build must not depend on the probe")
    case.assertIn("packages: read", build.split("rebuild_check:", 1)[1].split("  build:", 1)[0])
    case.assertIn("if: needs.rebuild_check.outputs.needed == 'true'",
                  build.split("  build:", 1)[1].split("runs-on:", 1)[0])
    # The probe counts only real `<pkg>/<suite> <version>` rows.
    case.assertIn("$1 ~ /\\//", script)
    case.assertIn("apt-get update failed", script)

    def probe(payload: str, *, status: str = "0") -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            fake = bin_dir / "docker"
            fake.write_text(
                "#!/bin/bash\n"
                f'if [ "{status}" != 0 ]; then printf "{payload}" >&2; exit {status}; fi\n'
                f"printf '%b\\n' {json.dumps(payload)}\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            return subprocess.run(
                ["bash", str(REBUILD), "count", "image:test"],
                capture_output=True, text=True, check=False,
                env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
            )

    banner = "Listing...\n"
    libc6 = "libc6/oldstable 2.36-9+deb12u14 arm64 [upgradable from: 2.36-9+deb12u12]\n"
    bash = "bash/oldstable 5.2.15-2+b13 arm64 [upgradable from: 5.2.15-2+b9]\n"
    rows = libc6 + bash
    clean = probe(banner)
    case.assertEqual(clean.returncode, 0, clean.stderr)
    case.assertIn("upgradable packages: 0", clean.stdout)
    case.assertNotIn("libc6/oldstable", clean.stdout)

    drift = probe(banner + rows)
    case.assertEqual(drift.returncode, 0, drift.stderr)
    case.assertIn("upgradable packages: 2", drift.stdout)
    case.assertIn("libc6/oldstable 2.36-9+deb12u14", drift.stdout)

    # Boundary: only one row, and a row-count that ignores noise lines.
    single = probe(banner + libc6)
    case.assertIn("upgradable packages: 1", single.stdout)
    noisy = probe(banner + "WARNING: apt does not have a stable CLI interface.\n" + rows)
    case.assertIn("upgradable packages: 2", noisy.stdout)

    # A failed update must not read as "nothing to do".
    failed = probe("apt-get update failed\n", status="1")
    case.assertNotEqual(failed.returncode, 0)
    case.assertIn("package probe failed", failed.stderr)

    # Mutation control: count every line instead of only package rows, so the
    # banner turns a clean image into a rebuild.
    mutated = script.replace("awk 'NF >= 2 && $1 ~ /\\// {print $1, $2}'", "awk 'NF >= 1 {print $1, $2}'")
    case.assertNotEqual(mutated, script)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "rebuild.sh"
        copy.write_text(mutated, encoding="utf-8")
        again = subprocess.run(
            ["bash", str(copy), "count", "image:test"],
            capture_output=True, text=True, check=False,
            env=_with_fake_docker(banner),
        )
    # The banner alone becomes one bogus "package", so zero drift reads as 1.
    case.assertIn("upgradable packages: 1", again.stdout)


def _with_fake_docker(payload: str) -> dict[str, str]:
    tmp = tempfile.mkdtemp()
    bin_dir = Path(tmp) / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(f"#!/bin/bash\nprintf '%b\\n' {json.dumps(payload)}\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


CRONTAB = ROOT / "config/crontab"


def cron_entries(text: str) -> list[list[str]]:
    """Return the parsed schedule and command of every active crontab line."""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line.split(None, 5))
    return entries


def assert_backup_schedule_is_guarded(case: unittest.TestCase, text: str) -> None:
    """A schedule nobody verifies can quietly stop being a schedule.

    The backup, the freshness gate and the restore drill are three separate
    duties: collapsing them back into a single `@hourly backup` leaves a dead
    scheduler indistinguishable from a fresh one, and leaves the drill manual.
    """
    commands = [entry[5] for entry in cron_entries(text) if len(entry) == 6]
    case.assertEqual(
        sorted(commands),
        ["/restic.sh backup", "/restic.sh check-freshness", "/restic.sh restore-test"],
    )
    for entry in cron_entries(text):
        case.assertEqual(len(entry), 6, f"unparseable crontab line: {' '.join(entry)}")
        # Minute zero collides with the deploy restart on every hour.
        case.assertNotEqual(entry[0], "0", "a job must not run at minute zero")
    hourly = {entry[0] for entry in cron_entries(text) if entry[2:5] == ["*", "*", "*"]}
    case.assertEqual(len(hourly), 2, "backup and freshness must both be hourly")
    case.assertEqual(
        len({entry[0] for entry in cron_entries(text) if entry[5].endswith("backup")}), 1
    )
    drills = [entry for entry in cron_entries(text) if entry[5].endswith("restore-test")]
    case.assertEqual(len(drills), 1, "the drill needs exactly one schedule")
    case.assertEqual(drills[0][2:], ["1", "*", "*", "/restic.sh restore-test"])


def assert_deploy_retries_only_transient_errors(case: unittest.TestCase, build: str) -> None:
    """Fly answers 5xx on the release path independently of our commit.

    Retrying is only safe because the digest is fixed, so a retry can never
    ship a different image. A bounded attempt count keeps a real outage from
    hanging the job, and the transient filter keeps a genuine failure from
    being masked by two more identical attempts.
    """
    step = build.split("Deploy the exact digest")[1].split("Verify the deployment")[0]
    case.assertIn("attempt=1", step)
    case.assertRegex(step, r"attempt.*-ge 3", "the retry need not be unbounded")
    case.assertIn("status (502|503|504)", step)
    case.assertIn("sleep", step)
    # Backoff grows, so a recovering control plane is not hammered.
    case.assertRegex(step, r"delay=\$\(\(attempt \* \d+\)\)")
    # The digest is what makes a retry idempotent; a tag would not be.
    case.assertIn('--image "$EXPECTED_IMAGE"', step)
    # Fail closed: the last attempt exits even when the text still matches.
    case.assertRegex(step, r"exit \"\$status\"")


def assert_flyctl_comes_from_github(case: unittest.TestCase, build: str) -> None:
    """The setup action resolves `latest` through Fly's own release manifest.

    That endpoint answered 503 and killed the job before the retry could run
    (run 40959357266), and it made the CLI version float besides. Install the
    pinned tarball from the GitHub release and verify its sha256 instead.
    """
    case.assertNotIn("superfly/flyctl-actions", build)
    case.assertRegex(build, r"FLYCTL_VERSION: \d+\.\d+\.\d+")
    case.assertRegex(build, r"FLYCTL_SHA256: [0-9a-f]{64}")
    # The asset name must carry the pinned version, or the checksum is for
    # another build than the one downloaded.
    version = re.search(r"FLYCTL_VERSION: (\d+\.\d+\.\d+)", build).group(1)
    case.assertIn(f"FLYCTL_ASSET: flyctl_{version}_Linux_x86_64.tar.gz", build)
    case.assertIn("github.com/superfly/flyctl/releases/download/v${FLYCTL_VERSION}", build)
    case.assertIn("sha256sum -c -", build)
    case.assertIn("flyctl version", build)


def assert_cleanup_runs_and_cannot_race(case: unittest.TestCase, cleanup: str, build: str) -> None:
    """A cleanup that never fires, or fires mid-publish, is worse than none.

    GitHub disables a scheduled workflow after a long quiet period (observed
    state: `disabled_inactivity`), so a schedule alone silently stops pruning.
    And because the publish workflow records the digest from a
    `build-<run>-<attempt>` tag, a concurrent cleanup can delete the very tag
    being verified.
    """
    case.assertEqual(len(schedule_entries(cleanup)), 1, "one cleanup slot is expected")
    case.assertIn("  push:\n    branches:\n      - main\n", cleanup)

    def group(text: str) -> str | None:
        match = re.search(r"(?m)^concurrency:\n(?:.*\n)*?\s*group:\s*(\S+)$", text)
        return match.group(1) if match else None

    case.assertEqual(
        group(cleanup),
        group(build),
        "cleanup and publish must share one concurrency group",
    )
    # `cancel-in-progress: false` queues instead of killing a running publish.
    case.assertIn("cancel-in-progress: false", cleanup)

    # The publish tag is the digest source, so the window must outlive a stalled
    # review; `latest` survives on its own because it is retagged every build.
    window = re.search(r"(?m)^\s+older-than:\s+([1-9]\d*)\s+(day|days)$", cleanup)
    case.assertIsNotNone(window, "cleanup needs a day-based older-than window")
    case.assertGreaterEqual(int(window.group(1)), 7, "a shorter window can drop a tag still in review")
    case.assertRegex(cleanup, r"(?m)^\s+keep-n-tagged:\s+[1-9]\d*$")
    case.assertIn("delete-untagged: true", cleanup)


def assert_validate_concurrency_is_distinct(case: unittest.TestCase, validate: str, build: str) -> None:
    """A reused workflow must not share its caller's group key.

    The same push triggers a standalone Validate run and the copy nested in the
    publish workflow. With a group keyed only on the ref, both land in one
    group under `cancel-in-progress: true` and the nested copy is cancelled by
    its own twin, which cancels the whole publish run before `build`.
    """
    match = re.search(r"(?m)^concurrency:\n\s*group:\s*(.+?)\s*$", validate)
    case.assertIsNotNone(match, "validate needs an explicit concurrency group")
    group = match.group(1)
    case.assertIn("github.workflow", group, "the group must distinguish callers")
    case.assertNotEqual(group, "validate-${{ github.ref }}")

    # The publish side keys on the registry; validate must not equal it.
    caller = re.search(r"(?m)^concurrency:\n\s*group:\s*(.+?)\s*$", build)
    case.assertIsNotNone(caller)
    case.assertNotEqual(group, caller.group(1))


def assert_listen_port_is_consistent_and_unprivileged(
    case: unittest.TestCase, caddyfile: str, fly: str
) -> None:
    """Caddy runs as `node`, so a privileged port fails only on a real machine.

    Fly's kernel refuses an unprivileged bind to :80 while a local Docker
    Desktop sandbox allows it, so every local test stayed green and the first
    real deploy timed out on a crash loop. The listener, `internal_port` and the
    health-check port must be one unprivileged value.
    """
    listener = re.search(r"(?m)^:(\d+) \{$", caddyfile)
    case.assertIsNotNone(listener, "the Caddyfile needs one explicit listener port")
    port = int(listener.group(1))
    case.assertGreater(port, 1024, "an unprivileged process cannot bind a privileged port")

    parsed = tomllib.loads(fly)
    case.assertEqual(parsed["http_service"]["internal_port"], port)
    case.assertEqual(parsed["checks"]["health"]["port"], port)


IMAGE = "ghcr.io/example/uptime-kuma@sha256:" + "a" * 64


def deploy_verification(step: str) -> tuple[str, str]:
    """Extract the two ``jq`` programs the deploy job feeds to the Fly API."""
    return tuple(
        block.group(1)
        for block in re.finditer(
            r"jq -e --arg image \"\$EXPECTED_IMAGE\" '\n(.*?)\n\s*'", step, re.DOTALL
        )
    )


def run_jq(program: str, document: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["jq", "-e", "--arg", "image", IMAGE, program],
        input=document, capture_output=True, text=True, check=False,
    )


def fly_releases(image: str, status: str = "complete") -> str:
    return json.dumps([
        {"Version": 3, "ImageRef": IMAGE, "Status": "complete"},
        {"Version": 4, "ImageRef": image, "Status": status},
    ])


def fly_status(image: str, **machine) -> str:
    entry = {
        "state": "started",
        "host_status": "ok",
        "image_ref": {"registry": "ghcr.io", "repository": "example/uptime-kuma",
                      "digest": image.split("@")[-1]},
        "checks": [{"status": "passing"}],
    }
    entry.update(machine)
    return json.dumps({"Status": "deployed", "Machines": [entry]})


class RecurringContractTest(unittest.TestCase):
    def test_registry_references_existing_evidence(self) -> None:
        for references in CLASS_EVIDENCE.values():
            for reference in references:
                path, symbol = reference.split(":", 1)
                source = (ROOT / path).read_text(encoding="utf-8")
                self.assertRegex(
                    source,
                    rf"(^|[^A-Za-z0-9_-]){re.escape(symbol)}([^A-Za-z0-9_-]|$)",
                    reference,
                )

    def test_recurring_classes_are_complete(self) -> None:
        assert_recurrence_contract(self, DOC.read_text(encoding="utf-8"))

    def test_target_mode_is_a_repository_artifact(self) -> None:
        """The round protocol must outlive the chat message that introduced it."""
        mode = TARGET_MODE.read_text(encoding="utf-8")
        issues = DOC.read_text(encoding="utf-8")
        round_open = ROUND_OPEN.read_text(encoding="utf-8")
        assert_target_mode_is_actionable(self, mode, issues, round_open)
        assert_residuals_live_in_one_place(self, README.read_text(encoding="utf-8"))

        # Mutation control: delete the rejection rule that voids a round, and
        # the protocol becomes advice again.
        weakened = mode.replace("- a matrix column or row is missing;\n", "", 1)
        self.assertNotEqual(weakened, mode)
        with self.assertRaises(AssertionError):
            assert_target_mode_is_actionable(self, weakened, issues, round_open)

        # Second control: the invariants stop pointing at the protocol, so a
        # round can still open from memory.
        detached = issues.replace("`docs/target_mode.md`", "the protocol", 1)
        self.assertNotEqual(detached, issues)
        with self.assertRaises(AssertionError):
            assert_target_mode_is_actionable(self, mode, detached, round_open)

        # Third control: drop a matrix column from the generator only. The
        # written table and the printed skeleton silently diverge.
        drifted = round_open.replace('"Observed result",\n', "", 1)
        self.assertNotEqual(drifted, round_open)
        with self.assertRaises(AssertionError):
            assert_target_mode_is_actionable(self, mode, issues, drifted)

        # Fourth control: the residual list moves, so new risks land elsewhere.
        homeless = README.read_text(encoding="utf-8").replace(
            "\n## ⚠️ Residual risk\n", "\n## Known issues\n", 1
        )
        with self.assertRaises(AssertionError):
            assert_residuals_live_in_one_place(self, homeless)

    def test_pr_template_carries_the_matrix(self) -> None:
        """A required section that is not prefilled is the one that gets skipped."""
        template = PR_TEMPLATE.read_text(encoding="utf-8")
        assert_pr_template_carries_the_matrix(self, template)

        # Mutation control: the section stays named but the table disappears,
        # which is exactly the "matrix" PRs that only list changes.
        headerless = "\n".join(
            line for line in template.splitlines() if not line.startswith("|")
        )
        with self.assertRaises(AssertionError):
            assert_pr_template_carries_the_matrix(self, headerless)

        # Second control: a renamed heading no longer matches the protocol.
        renamed = template.replace("## Residual\n", "## Notes\n", 1)
        with self.assertRaises(AssertionError):
            assert_pr_template_carries_the_matrix(self, renamed)

    def test_contract_mutation_removes_authoritative_field(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        assert_recurrence_contract(self, text)
        with self.assertRaises(AssertionError):
            assert_recurrence_contract(self, text.replace("Mutation control:", "Check:", 1))
        with self.assertRaises(AssertionError):
            assert_recurrence_contract(self, text.replace("Client IP Attribution", "Client IP", 1))
        with self.assertRaises(AssertionError):
            assert_recurrence_contract(self, text.replace("Regression gate:", "Gate:", 1))
        with self.assertRaises(AssertionError):
            assert_recurrence_contract(self, text.replace("repair the", "fix the", 1))

    def test_required_check_is_validate_and_deploy_needs_it(self) -> None:
        build = workflow("ghcr.yaml")
        self.assertIn("needs: [rebuild_check, validate]", build)
        self.assertIn("uses: ./.github/workflows/validate.yml", build)
        self.assertIn("needs: build", build)
        self.assertIn("--image \"$EXPECTED_IMAGE\"", build)
        validate = workflow("validate.yml")
        self.assertIn("workflow_call: {}", validate)
        self.assertIn("python3 tests/runtime_smoke.py", validate)
        self.assertIn("caddy validate --config /Caddyfile", validate)

    def test_deploy_verification_rejects_every_mismatch(self) -> None:
        """The recorded evidence must be about *this* digest, on every machine."""
        step = workflow("ghcr.yaml").split("Verify the deployment")[1]
        releases, status = deploy_verification(step)

        self.assertEqual(run_jq(releases, fly_releases(IMAGE)).returncode, 0)
        self.assertEqual(run_jq(status, fly_status(IMAGE)).returncode, 0)

        other = IMAGE.replace("a" * 64, "b" * 64)
        for label, program, document in (
            ("an older release", releases, fly_releases(other)),
            ("an unfinished release", releases, fly_releases(IMAGE, "failed")),
            ("a stale machine digest", status, fly_status(other)),
            ("a stopped machine", status, fly_status(IMAGE, state="stopped")),
            ("a failing check", status, fly_status(IMAGE, checks=[{"status": "critical"}])),
            ("no check at all", status, fly_status(IMAGE, checks=[])),
            ("a host that is not ok", status, fly_status(IMAGE, host_status="bad")),
            ("an undeployed app", status, json.dumps({"Status": "pending", "Machines": []})),
        ):
            self.assertNotEqual(run_jq(program, document).returncode, 0, label)

        # Mutation control: drop the digest comparison and the stale machine
        # becomes acceptable again.
        weakened = re.sub(
            r'\n\s*and \(\(\(\.image_ref\.registry.*?== \$image\)', "", status, count=1, flags=re.DOTALL
        )
        self.assertNotEqual(weakened, status)
        self.assertEqual(run_jq(weakened, fly_status(IMAGE)).returncode, 0)
        self.assertEqual(run_jq(weakened, fly_status(other)).returncode, 0)

    def test_fly_toml_has_no_mutable_image(self) -> None:
        fly = (ROOT / "fly.toml").read_text(encoding="utf-8")
        parsed = tomllib.loads(fly)
        self.assertNotIn("build", parsed)
        self.assertNotIn("image", parsed)
        self.assertIn('path = "/health"', fly)

    def test_fly_app_name_matches_readme_and_rename_note(self) -> None:
        """A wrong app name makes the deploy job fail before any image runs."""
        fly = (ROOT / "fly.toml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        build = workflow("ghcr.yaml")
        assert_app_name_is_recorded(self, fly, readme, build)

        # Mutation control: rename only the app in `fly.toml`; the README record
        # and the rename note must then disagree with it.
        renamed = fly.replace('app = "bxx-uptime-kuma"', 'app = "renamed-app"', 1)
        self.assertNotEqual(renamed, fly)
        with self.assertRaises(AssertionError):
            assert_app_name_is_recorded(self, renamed, readme, build)

        # Second mutation: drop the `-a` flag from the deploy invocation, so an
        # implicit default app takes over again.
        explicit = build.replace('flyctl deploy -a "$app"', "flyctl deploy", 1)
        self.assertNotEqual(explicit, build)
        with self.assertRaises(AssertionError):
            assert_app_name_is_recorded(self, fly, readme, explicit)

        # Third mutation: leave `fly.toml` alone and scope the documented token
        # to another app. The deploy then fails after its own guard passed.
        token = readme.replace("fly tokens create deploy -a bxx-uptime-kuma",
                               "fly tokens create deploy -a other-app", 1)
        self.assertNotEqual(token, readme)
        with self.assertRaises(AssertionError):
            assert_app_name_is_recorded(self, fly, token, build)

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        """A floating tag can be retargeted under a green, unchanged repo."""
        workflows = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
        }
        self.assertEqual(len(workflows), 4)
        assert_actions_are_pinned(self, workflows)

        # Mutation control: one floating tag is enough to fail, and the message
        # must name the offending line so the fix is mechanical.
        name = "ghcr.yaml"
        weakened = {**workflows, name: workflows[name].replace(
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262", "actions/checkout@v4", 1
        )}
        self.assertNotEqual(weakened[name], workflows[name])
        with self.assertRaises(AssertionError) as caught:
            assert_actions_are_pinned(self, weakened)
        self.assertIn("actions/checkout@v4", str(caught.exception))

        # Second control: keep the pin but delete its version comment. The SHA
        # alone is still immutable, so only the readability rule can catch it.
        unlabelled = {**workflows, name: workflows[name].replace(
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4",
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262", 1,
        )}
        self.assertNotEqual(unlabelled[name], workflows[name])
        self.assertEqual(unpinned_uses(unlabelled[name]), [])
        with self.assertRaises(AssertionError) as caught:
            assert_actions_are_pinned(self, unlabelled)
        self.assertIn("no version comment", str(caught.exception))

    def test_digest_cannot_resolve_to_an_attestation(self) -> None:
        build = workflow("ghcr.yaml")
        assert_digest_names_one_image_manifest(self, build)

        # Mutation control: drop the whole platform filter, so the index hands
        # back every child and the exactly-one check has to reject it.
        loose = build.replace(
            '{{if and (eq .Platform.Architecture "amd64") (eq .Platform.OS "linux")}}{{.Digest}}{{"\\n"}}{{end}}',
            '{{.Digest}}{{"\\n"}}',
            1,
        )
        self.assertNotEqual(loose, build)
        with self.assertRaises(AssertionError):
            assert_digest_names_one_image_manifest(self, loose)

    def test_weekly_rebuild_is_scheduled(self) -> None:
        build = workflow("ghcr.yaml")
        assert_weekly_rebuild_is_scheduled(self, build)

        # Mutation control: a schedule that only ever rebuilds one day a month
        # is not a weekly rebuild.
        monthly = build.replace("    - cron: '17 3 * * 1'", "    - cron: '17 3 1 * *'", 1)
        self.assertNotEqual(monthly, build)
        with self.assertRaises(AssertionError):
            assert_weekly_rebuild_is_scheduled(self, monthly)

    def test_docs_only_changes_skip_the_production_build(self) -> None:
        """Bumping a doc must not cost a build, a deploy and a `latest` move."""
        build = workflow("ghcr.yaml")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        assert_docs_do_not_rebuild(self, build, dockerfile, ignore)

        # Mutation control: drop the filter and every docs merge rebuilds again.
        unfiltered = build.replace("    paths-ignore:\n", "    branches-ignore:\n", 1)
        self.assertNotEqual(unfiltered, build)
        with self.assertRaises(AssertionError):
            assert_docs_do_not_rebuild(self, unfiltered, dockerfile, ignore)

        # Second control: ignore an input the image is built from. The image
        # silently stops rebuilding when only that file changes.
        script = build.replace("      - tests/**\n", "      - tests/**\n      - scripts/services.sh\n", 1)
        self.assertNotEqual(script, build)
        with self.assertRaises(AssertionError):
            assert_docs_do_not_rebuild(self, script, dockerfile, ignore)

        # Third control: keep the filter but ignore something the build context
        # really needs, so the two lists disagree in the other direction.
        context = ignore.replace("tests\n", "", 1)
        self.assertNotEqual(context, ignore)
        with self.assertRaises(AssertionError):
            assert_docs_do_not_rebuild(self, build, dockerfile, context)

    def test_latest_is_promoted_only_after_verification(self) -> None:
        """A failed build must leave the mutable tag on the last good image."""
        build = workflow("ghcr.yaml")
        assert_latest_follows_verification(self, build)

        # Mutation control: put `latest` back into the build metadata, which is
        # exactly the pre-fix state a canceled run exposes.
        early = build.replace(
            "            type=raw,value=build-${{ github.run_id }}-${{ github.run_attempt }}\n",
            "            type=raw,value=build-${{ github.run_id }}-${{ github.run_attempt }}\n"
            "            type=raw,value=latest,enable={{is_default_branch}}\n",
            1,
        )
        self.assertNotEqual(early, build)
        with self.assertRaises(AssertionError):
            assert_latest_follows_verification(self, early)

        # Second control: keep the promotion but stop comparing the re-read
        # tag, so a retag that silently pointed elsewhere would pass.
        unchecked = build.replace(
            'if [ "$latest_index" != "$index_digest" ] || [ "$latest_amd64" != "$AMD64_DIGEST" ]; then',
            "if false; then",
            1,
        )
        self.assertNotEqual(unchecked, build)
        with self.assertRaises(AssertionError):
            assert_latest_follows_verification(self, unchecked)

    def test_scheduled_rebuild_requires_package_drift(self) -> None:
        """A weekly rebuild must be earned by real upgrades, not by the clock."""
        assert_scheduled_rebuild_needs_drift(self, workflow("ghcr.yaml"), REBUILD.read_text(encoding="utf-8"))

        # Mutation control: drop the zero-check, so a clean image rebuilds.
        clean = workflow("ghcr.yaml").replace(
            'if [ "$count" -ne 0 ]; then\n              needed=true\n            fi\n',
            "needed=true\n",
            1,
        )
        self.assertNotEqual(clean, workflow("ghcr.yaml"))
        with self.assertRaises(AssertionError):
            assert_scheduled_rebuild_needs_drift(self, clean, REBUILD.read_text(encoding="utf-8"))

        # Second control: remove the numeric guard, so a malformed count reads
        # as zero instead of failing the job.
        unchecked = workflow("ghcr.yaml").replace(
            '[[ "$count" =~ ^[0-9]+$ ]] || {\n'
            '              echo "::error::published package count is missing or malformed" >&2\n'
            "              exit 1\n"
            "            }\n",
            "",
            1,
        )
        self.assertNotEqual(unchecked, workflow("ghcr.yaml"))
        with self.assertRaises(AssertionError):
            assert_scheduled_rebuild_needs_drift(self, unchecked, REBUILD.read_text(encoding="utf-8"))

        # Third control: drop the `probe` branch, so the manual mode becomes a
        # no-op and the decision code is never exercised outside the cron.
        unprobeable = workflow("ghcr.yaml").replace(
            '{ [ "$GITHUB_EVENT_NAME" = workflow_dispatch ] && [ "$MODE" = probe ]; }',
            "false",
            1,
        )
        self.assertNotEqual(unprobeable, workflow("ghcr.yaml"))
        with self.assertRaises(AssertionError):
            assert_scheduled_rebuild_needs_drift(self, unprobeable, REBUILD.read_text(encoding="utf-8"))

    def test_deploy_retries_only_transient_errors(self) -> None:
        build = workflow("ghcr.yaml")
        assert_deploy_retries_only_transient_errors(self, build)

        # Mutation control: drop the transient filter, so a permanent failure
        # is retried anyway and only the attempt bound ends it.
        unfiltered = build.replace(
            "! grep -qE 'status (502|503|504)|Service Unavailable' \"$RUNNER_TEMP/deploy.log\"",
            "false",
            1,
        )
        self.assertNotEqual(unfiltered, build)
        with self.assertRaises(AssertionError):
            assert_deploy_retries_only_transient_errors(self, unfiltered)

        # Second control: make the retry unbounded, so a dead control plane
        # keeps the job until its timeout instead of exiting.
        unbounded = build.replace('[ "$attempt" -ge 3 ]', 'false', 1)
        self.assertNotEqual(unbounded, build)
        with self.assertRaises(AssertionError):
            assert_deploy_retries_only_transient_errors(self, unbounded)

    def test_flyctl_is_pinned_and_not_fetched_through_fly(self) -> None:
        build = workflow("ghcr.yaml")
        assert_flyctl_comes_from_github(self, build)

        # Mutation control: back to the action, whose `latest` lookup is the
        # thing that failed. Everything else can stay intact.
        restored = build.replace(
            "      - name: 🛫 Install pinned flyctl",
            "      - name: 🛫 Set up Flyctl\n"
            "        uses: superfly/flyctl-actions/setup-flyctl@fc53c09e1bc3be6f54706524e3b82c4f462f77be # 1.5\n"
            "      - name: 🛫 Install pinned flyctl",
            1,
        )
        self.assertNotEqual(restored, build)
        with self.assertRaises(AssertionError):
            assert_flyctl_comes_from_github(self, restored)

        # Second control: a drifting version in the asset name while the pin
        # stays put would verify a checksum against the wrong file.
        drifted = build.replace("flyctl_0.5.1_Linux_x86_64.tar.gz",
                                "flyctl_0.5.2_Linux_x86_64.tar.gz", 1)
        self.assertNotEqual(drifted, build)
        with self.assertRaises(AssertionError):
            assert_flyctl_comes_from_github(self, drifted)

    def test_deploy_drift_workflow_is_wired(self) -> None:
        """A hand deploy bypasses the digest gate; this is the only detector."""
        drift = workflow("deploy-drift.yml")
        build = workflow("ghcr.yaml")
        workflows = {path.name: path.read_text(encoding="utf-8")
                     for path in sorted(WORKFLOWS.glob("*.yml"))}
        assert_drift_workflow_is_registered(self, workflows)
        assert_drift_check_exists(self, drift, build)

        # Mutation control: drop the push trigger, so only the daily cron is
        # left and a hand deploy goes unnoticed for up to a day.
        unsolicited = drift.replace(
            "  push:\n    branches:\n      - main\n", "", 1
        )
        self.assertNotEqual(unsolicited, drift)
        with self.assertRaises(AssertionError):
            assert_drift_check_exists(self, unsolicited, build)

        # Second control: a monthly cron is not a daily one.
        monthly = drift.replace("'41 5 * * *'", "'41 5 1 * *'", 1)
        self.assertNotEqual(monthly, drift)
        with self.assertRaises(AssertionError):
            assert_drift_check_exists(self, monthly, build)

        # Third control: the manual escape hatch disappears, so the path can
        # only be exercised at the cron's convenience.
        undispatchable = drift.replace("  workflow_dispatch: {}\n", "", 1)
        self.assertNotEqual(undispatchable, drift)
        with self.assertRaises(AssertionError):
            assert_drift_check_exists(self, undispatchable, build)

        # Fourth control: an unbounded read hangs the job until the workflow
        # timeout instead of failing closed.
        unbounded = drift.replace("timeout 60 flyctl status", "flyctl status", 1)
        self.assertNotEqual(unbounded, drift)
        with self.assertRaises(AssertionError):
            assert_drift_check_exists(self, unbounded, build)

        # Fifth control: decide inline with jq instead of the tested script, so
        # the fail-closed exit codes and the fixtures stop applying.
        inline = drift.replace(
            'python3 scripts/deploy_drift.py \\\n'
            '            "$out/releases.json" "$out/status.json" "$out/latest.json"',
            'jq -e . "$out/status.json" > /dev/null',
            1,
        )
        self.assertNotEqual(inline, drift)
        with self.assertRaises(AssertionError):
            assert_drift_check_exists(self, inline, build)

        # Sixth control: drop the repository name, so the probe addresses
        # `/latest` and docker answers from whatever local image exists.
        unnamed = drift.replace("  IMAGE_NAME: ${{ github.repository }}\n", "", 1)
        self.assertNotEqual(unnamed, drift)
        with self.assertRaises(AssertionError):
            assert_drift_check_exists(self, unnamed, build)

    def test_one_process_model(self) -> None:
        """The supervisor replaced Overmind; a second model cannot linger."""
        runtime = "\n".join(
            (ROOT / path).read_text(encoding="utf-8")
            for path in ("Dockerfile", "scripts/entrypoint.sh", "scripts/services.sh")
        )
        for forbidden in ("overmind", "Procfile", "tmux", "OVERMIND_"):
            self.assertNotIn(forbidden, runtime)
        service = (ROOT / "scripts/services.sh").read_text(encoding="utf-8")
        for command in ("caddy run --config /Caddyfile", "supercronic /crontab",
                        "node server/server.js"):
            self.assertIn(command, service)
        self.assertIn('ENTRYPOINT ["/entrypoint.sh"]', runtime)

    def test_restic_schedule_covers_backup_freshness_and_drill(self) -> None:
        """`@hourly backup` alone never drills and hides a dead scheduler."""
        text = CRONTAB.read_text(encoding="utf-8")
        assert_backup_schedule_is_guarded(self, text)

        # Mutation control: collapsing back to one `@hourly backup` is exactly
        # the state this test was added for, and must fail.
        collapsed = "# backup\n@hourly /restic.sh backup\n"
        with self.assertRaises(AssertionError):
            assert_backup_schedule_is_guarded(self, collapsed)

        # Second control: keep all three duties but move the backup to minute
        # zero, where a deploy restart takes the machine down mid-backup.
        zero = text.replace("25 * * * * /restic.sh backup", "0 * * * * /restic.sh backup", 1)
        self.assertNotEqual(zero, text)
        with self.assertRaises(AssertionError):
            assert_backup_schedule_is_guarded(self, zero)

        # Third control: make the drill weekly. It still parses, but the
        # "first of the month" promise in the README would be a lie.
        weekly = text.replace("15 4 1 * * /restic.sh restore-test",
                              "15 4 * * 1 /restic.sh restore-test", 1)
        self.assertNotEqual(weekly, text)
        with self.assertRaises(AssertionError):
            assert_backup_schedule_is_guarded(self, weekly)

    def test_cleanup_keeps_running_and_cannot_race_a_publish(self) -> None:
        cleanup = workflow("ghcr-cleanup.yml")
        build = workflow("ghcr.yaml")
        assert_cleanup_runs_and_cannot_race(self, cleanup, build)

        # Mutation control 1: drop the `main` trigger, leaving only the schedule
        # GitHub eventually disables.
        scheduled_only = cleanup.replace(
            "  push:\n    branches:\n      - main\n", "", 1
        )
        self.assertNotEqual(scheduled_only, cleanup)
        with self.assertRaises(AssertionError):
            assert_cleanup_runs_and_cannot_race(self, scheduled_only, build)

        # Mutation control 2: give cleanup its own concurrency group, so it can
        # delete the tag the publish job is still smoke-testing.
        own_group = cleanup.replace("  group: ghcr-registry", "  group: ghcr-clean", 1)
        self.assertNotEqual(own_group, cleanup)
        with self.assertRaises(AssertionError):
            assert_cleanup_runs_and_cannot_race(self, own_group, build)

        # Mutation control 3: a one-day window can delete a tag whose deploy is
        # still awaiting review, because the tag is the recorded digest source.
        one_day = cleanup.replace("older-than: 7 days", "older-than: 1 day", 1)
        self.assertNotEqual(one_day, cleanup)
        with self.assertRaises(AssertionError):
            assert_cleanup_runs_and_cannot_race(self, one_day, build)

    def test_validate_does_not_cancel_the_publish_run(self) -> None:
        validate = workflow("validate.yml")
        build = workflow("ghcr.yaml")
        assert_validate_concurrency_is_distinct(self, validate, build)

        # Mutation control: the exact key that caused the cancelled publish.
        shared = validate.replace(
            "  group: validate-${{ github.workflow }}-${{ github.ref }}",
            "  group: validate-${{ github.ref }}",
            1,
        )
        self.assertNotEqual(shared, validate)
        with self.assertRaises(AssertionError):
            assert_validate_concurrency_is_distinct(self, shared, build)

    def test_listen_port_is_unprivileged_and_matches_fly(self) -> None:
        caddyfile = (ROOT / "config/Caddyfile").read_text(encoding="utf-8")
        fly = (ROOT / "fly.toml").read_text(encoding="utf-8")
        assert_listen_port_is_consistent_and_unprivileged(self, caddyfile, fly)

        # Mutation control 1: the observed failure, back on :80.
        privileged = caddyfile.replace("\n:8080 {\n", "\n:80 {\n", 1)
        self.assertNotEqual(privileged, caddyfile)
        with self.assertRaises(AssertionError):
            assert_listen_port_is_consistent_and_unprivileged(self, privileged, fly)

        # Mutation control 2: internal_port drifting from the listener means the
        # proxy never reaches Caddy even though everything starts.
        drifted = fly.replace("internal_port = 8080", "internal_port = 8081", 1)
        self.assertNotEqual(drifted, fly)
        with self.assertRaises(AssertionError):
            assert_listen_port_is_consistent_and_unprivileged(self, caddyfile, drifted)


class RecurringMutationTest(unittest.TestCase):
    def test_digest_mutation_allows_mutable_base(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        assert_base_image_is_pinned(self, text)
        mutable = re.sub(r"^FROM (\S+?)@sha256:[0-9a-f]{64}$", r"FROM \1", text, count=1, flags=re.MULTILINE)
        self.assertNotEqual(mutable, text)
        with self.assertRaises(AssertionError):
            assert_base_image_is_pinned(self, mutable)

    def test_tampered_binary_digest_is_detected(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        for name in ("SUPERCRONIC_SHA256_amd64", "SUPERCRONIC_SHA256_arm64"):
            self.assertRegex(text, rf"ARG {name}=[0-9a-f]{{64}}")
        self.assertIn("sha256sum -c -", text)
        original = re.search(r"ARG SUPERCRONIC_SHA256_arm64=([0-9a-f]{64})", text)
        self.assertIsNotNone(original)
        tampered = text.replace(original.group(1), "0" * 64, 1)
        self.assertNotEqual(tampered, text)
        self.assertRegex(
            tampered,
            r"ARG SUPERCRONIC_SHA256_arm64=[0-9a-f]{64}",
        )
        self.assertIn("ARG SUPERCRONIC_SHA256_arm64=" + "0" * 64, tampered)

    def test_privacy_mutation_detects_planted_identifier(self) -> None:
        self.assertEqual(scan_for_sensitive_data("documentation-only value"), [])
        planted = "s3://" + "a" * 32 + ".r2.cloudflarestorage.com/bucket"
        self.assertEqual(scan_for_sensitive_data(planted), ["R2 endpoint"])
        self.assertEqual(
            scan_for_sensitive_data("gh" + "p_" + "b" * 36), ["provider token"]
        )

    def test_tracked_files_and_history_are_private(self) -> None:
        # Include untracked-but-not-ignored files so a value is caught before
        # it is ever committed.
        listing = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
        self.assertIn("Dockerfile", listing)
        current = "\n".join(
            (ROOT / name).read_text(encoding="utf-8", errors="replace")
            for name in listing
            if (ROOT / name).is_file()
        )
        self.assertEqual(scan_for_sensitive_data(current), [])
        history = subprocess.run(
            ["git", "log", "--all", "-p", "--", ".", ":(exclude).github"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        self.assertGreater(len(history), 0)
        self.assertEqual(scan_for_sensitive_data(history), [])


if __name__ == "__main__":
    unittest.main()
