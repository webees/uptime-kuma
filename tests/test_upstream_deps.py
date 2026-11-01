#!/usr/bin/env python3
"""Contract tests for the scheduled base-image and npm dependency lane."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/upstream_deps.py"
WORKFLOW = ROOT / ".github/workflows/upstream-updates.yml"


def load_module():
    spec = importlib.util.spec_from_file_location("upstream_deps", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


upstream = load_module()


class NpmPinTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dockerfile = Path(self.temp.name) / "Dockerfile"
        self.dockerfile.write_text((ROOT / "Dockerfile").read_text(encoding="utf-8"), encoding="utf-8")
        self.original_dockerfile = upstream.DOCKERFILE
        upstream.DOCKERFILE = self.dockerfile
        self.addCleanup(setattr, upstream, "DOCKERFILE", self.original_dockerfile)

    def test_every_direct_package_and_tar_override_is_discovered(self) -> None:
        direct, overrides = upstream.current_npm()
        self.assertEqual(set(direct), set(upstream.DIRECT_NPM_PACKAGES))
        self.assertEqual(set(overrides), set(upstream.TAR_OVERRIDE_PACKAGES))
        self.assertEqual(set(overrides.values()), {direct["tar"]})
        parsed = upstream.parse_npm_versions(
            json.dumps(direct, separators=(",", ":"), sort_keys=True)
        )
        self.assertEqual(parsed, direct)

    def test_compare_marks_direct_and_override_updates(self) -> None:
        want = upstream.current()
        npm_want = upstream.current_npm()[0]
        npm_want["axios"] = "1.0.0"
        npm_want["tar"] = "7.5.23"
        output = Path(self.temp.name) / "github-output"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            upstream.compare(want, npm_want, output)
        emitted = output.read_text(encoding="utf-8")
        self.assertIn("changed=true", emitted)
        self.assertIn("bump=major", emitted)
        self.assertIn("diff npm axios:", stdout.getvalue())
        self.assertIn("diff npm override @louislam/sqlite3.tar:", stdout.getvalue())

    def test_unchanged_pins_do_not_claim_an_update(self) -> None:
        output = Path(self.temp.name) / "github-output"
        with contextlib.redirect_stdout(io.StringIO()):
            upstream.compare(upstream.current(), upstream.current_npm()[0], output)
        emitted = output.read_text(encoding="utf-8")
        self.assertIn("changed=false", emitted)
        self.assertIn("bump=patch", emitted)

    def test_compare_and_apply_do_not_downgrade_an_ahead_pin(self) -> None:
        npm_want = upstream.current_npm()[0]
        npm_want["tar"] = "7.5.21"
        output = Path(self.temp.name) / "github-output"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            upstream.compare(upstream.current(), npm_want, output)
        emitted = output.read_text(encoding="utf-8")
        self.assertIn("changed=false", emitted)
        self.assertIn("bump=patch", emitted)
        self.assertIn("ahead of latest 7.5.21", stdout.getvalue())
        with self.assertRaises(upstream.Refused):
            upstream.apply(upstream.current(), npm_want, None)

    def test_apply_rewrites_only_known_npm_pins(self) -> None:
        npm_want = upstream.current_npm()[0]
        npm_want["axios"] = "1.0.0"
        npm_want["tar"] = "7.5.23"
        output = Path(self.temp.name) / "github-output"
        with contextlib.redirect_stdout(io.StringIO()):
            upstream.apply(upstream.current(), npm_want, output)
        text = self.dockerfile.read_text(encoding="utf-8")
        self.assertIn("        axios@1.0.0 \\", text)
        for package in upstream.TAR_OVERRIDE_PACKAGES:
            self.assertIn(f'        "overrides.{package}.tar=7.5.23" \\', text)
        self.assertNotIn("        axios@0.34.0 \\", text)
        self.assertNotIn('"overrides.@louislam/sqlite3.tar=7.5.22"', text)
        self.assertIn("ARG UPTIME_KUMA_VERSION=2.5.9", text)
        self.assertIn("HEALTHCHECK --interval=60s", text)

    def test_divergent_override_fails_closed(self) -> None:
        text = self.dockerfile.read_text(encoding="utf-8")
        text = text.replace('"overrides.cacache.tar=7.5.22"', '"overrides.cacache.tar=7.5.21"', 1)
        with self.assertRaises(upstream.Refused):
            upstream.current_npm(text)

    def test_apply_refuses_a_missing_package_anchor(self) -> None:
        text = self.dockerfile.read_text(encoding="utf-8")
        text = text.replace("        ws@8.22.1 \\", "        ws@8.22.1", 1)
        self.dockerfile.write_text(text, encoding="utf-8")
        with self.assertRaises(upstream.Refused):
            upstream.apply(upstream.current(), upstream.current_npm()[0], None)

    def test_npm_json_rejects_missing_extra_and_prerelease_values(self) -> None:
        direct = upstream.current_npm()[0]
        mutations = []
        missing = dict(direct)
        missing.pop("axios")
        mutations.append(missing)
        extra = dict(direct)
        extra["unexpected"] = "1.0.0"
        mutations.append(extra)
        prerelease = dict(direct)
        prerelease["axios"] = "1.0.0-beta.1"
        mutations.append(prerelease)
        for value in mutations:
            with self.subTest(value=value):
                with self.assertRaises(upstream.Refused):
                    upstream.parse_npm_versions(json.dumps(value))

    def test_npm_latest_rejects_deprecated_prerelease_and_identity_drift(self) -> None:
        responses = (
            {"name": "axios", "version": "1.2.3", "deprecated": "unsafe"},
            {"name": "axios", "version": "1.2.3-beta.1"},
            {"name": "not-axios", "version": "1.2.3"},
        )
        for response in responses:
            with self.subTest(response=response):
                with mock.patch.object(upstream, "fetch_json", return_value=response):
                    with self.assertRaises(upstream.Refused):
                        upstream.npm_latest("axios")

    def test_workflow_carries_the_npm_resolution_to_compare_and_apply(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(
            workflow.count("NPM_VERSIONS_JSON: ${{ steps.pins.outputs.npm_versions }}"),
            2,
        )
        self.assertEqual(workflow.count('--npm-versions-json "$NPM_VERSIONS_JSON"'), 2)
        self.assertIn("timeout 300 python3 scripts/upstream_deps.py", workflow)
        self.assertIn("tee \"$RUNNER_TEMP/pins.diff\"", workflow)


if __name__ == "__main__":
    unittest.main()
