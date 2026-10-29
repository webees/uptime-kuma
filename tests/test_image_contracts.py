#!/usr/bin/env python3
"""Image-level contracts that need the real container, not a shell case."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest


IMAGE = os.environ.get("UPTIME_KUMA_IMAGE", "uptime-kuma:dev")
ROOT = Path(__file__).resolve().parents[1]


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
