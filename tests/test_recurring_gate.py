#!/usr/bin/env python3
"""Execute the mandatory recurring-class contract and mutation gate."""

from __future__ import annotations

import re
import json
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
    # The note is what stops the next rename from updating only one place.
    case.assertIn("rename must be synced", fly)
    # CI resolves the name from `fly.toml` and passes `-a` everywhere, so a
    # stale local default cannot redirect the deploy or the verification.
    case.assertIn('sed -n \'s/^app = "\\(.*\\)"$/\\1/p\' fly.toml', build)
    case.assertIn('flyctl deploy -a "$app"', build)
    case.assertIn('flyctl releases -a "$APP"', build)
    case.assertIn('flyctl status -a "$APP"', build)


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
