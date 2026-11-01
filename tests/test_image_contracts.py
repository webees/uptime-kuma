#!/usr/bin/env python3
"""Image-level contracts that need the real container, not a shell case."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import unittest


IMAGE = os.environ.get("UPTIME_KUMA_IMAGE", "uptime-kuma:dev")
ROOT = Path(__file__).resolve().parents[1]

MIN_RUNTIME_DEPENDENCIES = {
    "@azure/identity": (4, 14, 1),
    "@azure/msal-node": (7, 1, 2),
    "@grpc/grpc-js": (1, 14, 7),
    "axios": (0, 34, 0),
    "body-parser": (1, 20, 9),
    "follow-redirects": (1, 16, 2),
    "liquidjs": (10, 30, 1),
    "mysql2": (3, 24, 6),
    "nodemailer": (10, 3, 1),
    "protobufjs": (8, 8, 0),
    "redbean-node": (0, 4, 0),
    "tar": (7, 5, 22),
    "ws": (8, 22, 1),
}


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(("docker", *args), capture_output=True, text=True, check=False)


def image_available() -> bool:
    if docker("image", "inspect", IMAGE).returncode != 0:
        return False
    return True


@unittest.skipUnless(image_available(), f"{IMAGE} is not built; run docker build first")
class ImageContractTest(unittest.TestCase):
    def test_entrypoint_writes_smtp_config_as_the_service_user(self) -> None:
        result = docker(
            "run", "--rm",
            "--env", "SMTP_HOST=smtp.example.org",
            "--env", "SMTP_TO=ops@example.org",
            "--env", "SMTP_FROM=from@example.org",
            "--env", "SMTP_USERNAME=user",
            "--env", "SMTP_PASSWORD=secret",
            "--entrypoint", "bash", IMAGE, "-c",
            "set -e; RESTIC_LIB_ONLY=1 . /restic.sh; initCfg; "
            "stat -c '%a %U' /etc/msmtprc; grep -qx 'host smtp.example.org' /etc/msmtprc",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "600 node")

    def test_unprivileged_user_owns_runtime_paths(self) -> None:
        result = docker(
            "run", "--rm", "--entrypoint", "bash", IMAGE, "-c",
            "stat -c '%U' /app/data /var/log/restic; "
            "setpriv --reuid=node --regid=node --clear-groups "
            "bash -c 'id -u; mkdir -p /var/log/restic/probe && : > /app/data/probe'",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["node", "node", "1000"])

    def test_stale_package_lock_metadata_is_not_in_the_runtime(self) -> None:
        result = docker(
            "run", "--rm", "--entrypoint", "bash", IMAGE, "-c",
            "test ! -e /app/package-lock.json; "
            "test -e /app/node_modules/.package-lock.json; "
            "test -z \"$(find /app/node_modules -type f -name package-lock.json "
            "-print -quit)\"",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_npm_is_build_time_only(self) -> None:
        result = docker(
            "run", "--rm", "--entrypoint", "bash", IMAGE, "-c",
            "if command -v npm >/dev/null; then echo 'npm is present'; exit 9; fi; "
            "if command -v npx >/dev/null; then echo 'npx is present'; exit 9; fi",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_dependencies_are_not_downgraded(self) -> None:
        script = r'''
const fs = require("fs");
const path = require("path");
const names = JSON.parse(process.env.PACKAGE_NAMES);
const versions = {};
for (const name of names) {
  let dir = path.dirname(require.resolve(name));
  while (dir !== "/") {
    const manifest = path.join(dir, "package.json");
    if (fs.existsSync(manifest)) {
      const parsed = JSON.parse(fs.readFileSync(manifest, "utf8"));
      if (parsed.name === name) {
        versions[name] = parsed.version;
        break;
      }
    }
    dir = path.dirname(dir);
  }
}
process.stdout.write(JSON.stringify(versions));
'''
        result = docker(
            "run", "--rm",
            "--env", "PACKAGE_NAMES=" + json.dumps(list(MIN_RUNTIME_DEPENDENCIES)),
            "--entrypoint", "node", IMAGE, "-e", script,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        versions = json.loads(result.stdout)
        for package, minimum in MIN_RUNTIME_DEPENDENCIES.items():
            with self.subTest(package=package):
                self.assertIn(package, versions)
                installed = tuple(int(part) for part in versions[package].split("."))
                self.assertGreaterEqual(installed, minimum)

    def test_smtp_without_a_peer_field_leaves_a_private_empty_file(self) -> None:
        result = docker(
            "run", "--rm", "--entrypoint", "bash", IMAGE, "-c",
            "set -e; RESTIC_LIB_ONLY=1 . /restic.sh; initCfg; "
            "test ! -s /etc/msmtprc; stat -c '%a %U' /etc/msmtprc",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "600 node")

    def test_caddyfile_adapts_with_and_without_an_allowlist(self) -> None:
        for value in ("", "status.example.org other.example.org"):
            result = docker(
                "run", "--rm", "--env", f"CADDY_DOMAINS={value}",
                "--entrypoint", "bash", IMAGE, "-c",
                "set -e; . /entrypoint/config.sh; audit_addresses; "
                "caddy validate --config /Caddyfile 2>&1 | tail -n 1",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Valid configuration", result.stdout)

    def test_caddy_rejects_an_unaudited_empty_value(self) -> None:
        """Negative control for the `unset`: Caddy alone cannot take it."""
        result = docker(
            "run", "--rm", "--env", "CADDY_DOMAINS=",
            "--entrypoint", "bash", IMAGE, "-c",
            "if caddy validate --config /Caddyfile >/tmp/caddy.log 2>&1; then exit 9; fi; "
            "tail -n 2 /tmp/caddy.log",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("module value cannot be null", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
