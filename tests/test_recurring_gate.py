#!/usr/bin/env python3
"""Execute the mandatory recurring-class contract and mutation gate."""

from __future__ import annotations

import re
import json
import os
from pathlib import Path
import subprocess
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recurring_issues.md"
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOWS = ROOT / ".github/workflows"

RECURRING_GOAL_GATE_CLAUSES = (
    "Every new, resumed, review, optimization, scoring, release, and post-merge round",
    "nine-row evidence matrix",
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
        self.assertIn("needs: validate", build)
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
        self.assertEqual(len(workflows), 3)
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
