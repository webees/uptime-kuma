#!/usr/bin/env python3
"""Deploy drift: production must still run what `latest` was promoted from."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/deploy_drift.py"
WORKFLOW = ROOT / ".github/workflows/deploy-drift.yml"

SELF = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
REPO = "ghcr.io/webees/uptime-kuma"


def image(digest: str) -> str:
    return f"{REPO}@{digest}"


def releases(digest: str, status: str = "complete", version: int = 7) -> list[dict]:
    return [{"Version": version - 1, "Status": "complete", "ImageRef": image(OTHER)},
            {"Version": version, "Status": status, "ImageRef": image(digest)}]


def status(digest: str, **machine: object) -> dict:
    entry = {
        "state": "started",
        "host_status": "ok",
        "checks": [{"name": "health", "status": "passing"}],
        "image_ref": {"registry": "ghcr.io", "repository": "webees/uptime-kuma", "digest": digest},
    }
    entry.update(machine)
    return {"Status": "deployed", "Machines": [entry]}


def index(amd64: str = SELF, *, extra: list[dict] | None = None) -> dict:
    manifests = [
        {"mediaType": "application/vnd.oci.image.manifest.v1+json",
         "digest": amd64, "platform": {"architecture": "amd64", "os": "linux"}},
        {"mediaType": "application/vnd.oci.image.manifest.v1+json",
         "digest": "sha256:" + "c" * 64, "platform": {"architecture": "arm64", "os": "linux"}},
        {"mediaType": "application/vnd.oci.image.manifest.v1+json",
         "digest": "sha256:" + "d" * 64, "platform": {"architecture": "unknown", "os": "unknown"}},
    ]
    return {"schemaVersion": 2, "manifests": manifests + (extra or [])}


def run(release_doc: object, status_doc: object, index_doc: object) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, document in (("r.json", release_doc), ("s.json", status_doc), ("i.json", index_doc)):
            path = Path(tmp) / name
            path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
            paths.append(str(path))
        return subprocess.run([sys.executable, str(SCRIPT), *paths],
                              capture_output=True, text=True, check=False)


def assert_trusted_checkout(case: unittest.TestCase, workflow: str) -> None:
    """The post-publish job must run the default branch's own check code."""
    checkout = re.search(
        r"(?ms)^\s+- name: 📥 Checkout repository\n(.*?)(?=^\s+- name:|\Z)",
        workflow,
    )
    case.assertIsNotNone(checkout, "deploy-drift has no checkout step")
    case.assertNotIn("workflow_run.head_sha", checkout.group(0))
    case.assertNotIn("ref:", checkout.group(0), "checkout must use the trusted default ref")


def assert_public_probe(case: unittest.TestCase, workflow: str) -> None:
    """The public probe must cover both Caddy health and the application root."""
    for fragment in (
        'host="$(jq -r \'.Hostname // empty\' "$out/status.json")"',
        '"${base}/health"',
        'test "$health" = 200',
        '"${base}/"',
        'test "$root" = 302',
        'test "$location" = /dashboard',
    ):
        case.assertIn(fragment, workflow)


class DriftWorkflowTrustTest(unittest.TestCase):
    def test_checkout_never_uses_the_publish_head(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        assert_trusted_checkout(self, workflow)

        # Mutation control: restoring the published-head checkout puts
        # privileged execution back on event-selected code.
        untrusted = workflow.replace(
            "          persist-credentials: false\n",
            "          ref: ${{ github.event.workflow_run.head_sha || github.sha }}\n"
            "          persist-credentials: false\n",
            1,
        )
        self.assertNotEqual(untrusted, workflow)
        with self.assertRaises(AssertionError):
            assert_trusted_checkout(self, untrusted)

    def test_public_probe_checks_the_application_not_only_caddy(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        assert_public_probe(self, workflow)

        # Mutation control: accepting a different / status lets a broken app
        # path read as healthy even though Caddy still answers /health.
        mutated = workflow.replace('test "$root" = 302', 'test "$root" = 200', 1)
        self.assertNotEqual(mutated, workflow)
        with self.assertRaises(AssertionError):
            assert_public_probe(self, mutated)


class DeployDriftTest(unittest.TestCase):
    def test_in_sync_is_reported(self) -> None:
        result = run(releases(SELF), status(SELF), index())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"in sync: {image(SELF)}", result.stdout)

    def test_capitalised_imagetools_keys_are_accepted(self) -> None:
        """`imagetools inspect` prints `Platform`/`Digest`; the raw API does not."""
        capitalised = {"Name": REPO, "Manifests": [
            {"Platform": {"os": "linux", "architecture": "amd64"}, "Digest": SELF},
        ]}
        self.assertEqual(run(releases(SELF), status(SELF), capitalised).returncode, 0)

    def test_attestation_children_never_become_the_reference(self) -> None:
        """Two `unknown/unknown` children ride along; only the amd64 one counts."""
        result = run(releases(SELF), status(SELF), index())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("unknown", result.stdout)

    def test_hand_deploy_is_drift(self) -> None:
        """The case this exists for: someone ran `fly deploy` without the gate."""
        result = run(releases(OTHER), status(OTHER), index(SELF))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("DRIFT", result.stderr)
        self.assertIn(image(OTHER), result.stderr)
        self.assertIn(SELF, result.stderr)

    def test_release_and_machine_must_agree(self) -> None:
        result = run(releases(SELF), status(OTHER), index(SELF))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("is not what the machine runs", result.stderr)

    def test_machines_must_agree_with_each_other(self) -> None:
        two = status(SELF)
        two["Machines"].append(status(OTHER)["Machines"][0])
        result = run(releases(SELF), two, index())
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("machines disagree", result.stderr)

    def test_machine_must_be_started_and_healthy(self) -> None:
        cases = (
            ("stopped", status(SELF, state="stopped"), "not started"),
            ("unhealthy host", status(SELF, host_status="degraded"), "host is not healthy"),
            ("missing checks", status(SELF, checks=None), "no single passing health check"),
            ("empty checks", status(SELF, checks=[]), "no single passing health check"),
            (
                "failing health",
                status(SELF, checks=[{"name": "health", "status": "critical"}]),
                "no single passing health check",
            ),
            (
                "duplicate health",
                status(
                    SELF,
                    checks=[
                        {"name": "health", "status": "passing"},
                        {"name": "health", "status": "passing"},
                    ],
                ),
                "no single passing health check",
            ),
        )
        for label, machine, message in cases:
            with self.subTest(label):
                result = run(releases(SELF), machine, index())
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(message, result.stderr)

    def test_unreadable_input_fails_closed(self) -> None:
        for label, args in (
            ("not json", ("{", json.dumps(status(SELF)), json.dumps(index()))),
            ("no machine", (json.dumps(releases(SELF)), json.dumps({"Machines": []}), json.dumps(index()))),
            ("no digest", (json.dumps(releases(SELF)), json.dumps({"Machines": [{}]}), json.dumps(index()))),
            ("no manifest", (json.dumps(releases(SELF)), json.dumps(status(SELF)), json.dumps({"manifests": []}))),
            ("no completed release",
             (json.dumps([{"Version": 1, "Status": "failed", "ImageRef": image(SELF)}]),
              json.dumps(status(SELF)), json.dumps(index()))),
        ):
            with self.subTest(label):
                result = run(*args)
                self.assertEqual(result.returncode, 2, f"{label}: {result.stdout}")
                self.assertIn("cannot compare", result.stderr)

    def test_two_amd64_children_fail_closed(self) -> None:
        twice = index(extra=[{"digest": "sha256:" + "e" * 64,
                              "platform": {"architecture": "amd64", "os": "linux"}}])
        result = run(releases(SELF), status(SELF), twice)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("2 linux/amd64 children", result.stderr)

    def test_missing_argument_is_a_usage_error(self) -> None:
        result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)


class DeployDriftMutationTest(unittest.TestCase):
    """Remove each guard in turn; the drift case must stop being detected."""

    def setUp(self) -> None:
        self.source = SCRIPT.read_text(encoding="utf-8")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def mutate(
        self, *replacements: tuple[str, str],
        release_doc: object | None = None,
        status_doc: object | None = None,
        index_doc: object | None = None,
    ) -> subprocess.CompletedProcess[str]:
        text = self.source
        for old, new in replacements:
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        path = Path(self.tmp.name) / "mutated.py"
        path.write_text(text, encoding="utf-8")
        documents = (
            ("r.json", releases(OTHER) if release_doc is None else release_doc),
            ("s.json", status(OTHER) if status_doc is None else status_doc),
            ("i.json", index(SELF) if index_doc is None else index_doc),
        )
        with tempfile.TemporaryDirectory() as tmp:
            docs = []
            for name, document in documents:
                target = Path(tmp) / name
                target.write_text(json.dumps(document), encoding="utf-8")
                docs.append(str(target))
            return subprocess.run([sys.executable, str(path), *docs],
                                  capture_output=True, text=True, check=False)

    def test_without_the_comparison_drift_passes(self) -> None:
        result = self.mutate(("if not running.endswith(f\"@{published}\"):", "if False:"))
        # The same drift inputs that fail above now exit clean and say "in sync".
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("in sync", result.stdout)
        self.assertNotIn("DRIFT", result.stderr)

    def test_without_the_attestation_filter_the_count_breaks(self) -> None:
        result = self.mutate(('str(platform.get("architecture", "")).lower() == "amd64"', "True"))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("children", result.stderr)

    def test_without_the_release_cross_check_drift_is_missed(self) -> None:
        """The machine and `latest` agree, but the newest release disagrees."""
        drift = dict(release_doc=releases(SELF), status_doc=status(OTHER))
        guarded = self.mutate(**drift)
        self.assertEqual(guarded.returncode, 2, guarded.stderr)
        self.assertIn("is not what the machine runs", guarded.stderr)

        # Without the cross-check the run reads as healthy: machine and latest
        # agree, so only the release comparison can see the gap.
        moved = dict(drift, index_doc=index(OTHER))
        result = self.mutate(("if newest.get(\"ImageRef\") != running:", "if False:"), **moved)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_without_the_machine_state_guard_a_stopped_machine_passes(self) -> None:
        stopped = dict(
            release_doc=releases(SELF),
            status_doc=status(SELF, state="stopped"),
            index_doc=index(SELF),
        )
        guarded = self.mutate(**stopped)
        self.assertEqual(guarded.returncode, 2, guarded.stderr)

        result = self.mutate(
            ('if str(machine.get("state", "")).lower() != "started":', "if False:"),
            **stopped,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_without_the_host_guard_an_unhealthy_machine_passes(self) -> None:
        unhealthy = dict(
            release_doc=releases(SELF),
            status_doc=status(SELF, host_status="degraded"),
            index_doc=index(SELF),
        )
        guarded = self.mutate(**unhealthy)
        self.assertEqual(guarded.returncode, 2, guarded.stderr)

        result = self.mutate(
            ('if str(machine.get("host_status", "")).lower() != "ok":', "if False:"),
            **unhealthy,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_without_the_health_guard_a_failing_machine_passes(self) -> None:
        failing = dict(
            release_doc=releases(SELF),
            status_doc=status(
                SELF,
                checks=[{"name": "health", "status": "critical"}],
            ),
            index_doc=index(SELF),
        )
        guarded = self.mutate(**failing)
        self.assertEqual(guarded.returncode, 2, guarded.stderr)

        result = self.mutate(('if health != ["passing"]:', "if False:"), **failing)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
