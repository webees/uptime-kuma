#!/usr/bin/env python3
"""Credential preflight tests use synthetic values and never read real secrets."""

from __future__ import annotations

import datetime as dt
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import re
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_automation_credentials",
    ROOT / "scripts/check_automation_credentials.py",
)
assert SPEC and SPEC.loader
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)

NOW = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
WEBHOOK = "https://alerts.example.invalid/hook"
TOKEN = "FlyV1-synthetic"


class AutomationCredentialTest(unittest.TestCase):
    def call(self, **overrides: object) -> tuple[str, ...]:
        values = {
            "webhook": WEBHOOK,
            "fly_token": TOKEN,
            "expires_at": "2026-12-31T00:00:00Z",
            "now": NOW,
            "require_webhook": True,
            "require_fly": True,
        }
        values.update(overrides)
        return CHECK.check(**values)

    def test_valid_credentials_pass(self) -> None:
        self.assertEqual(self.call(), ())

    def test_missing_and_malformed_webhook_fail(self) -> None:
        for value in ("", "http://alerts.example.invalid/hook", "https://bad host"):
            with self.subTest(value=value):
                self.assertTrue(self.call(webhook=value))

    def test_missing_malformed_or_foreign_token_fails(self) -> None:
        for value in ("", "not-a-fly-token", "Bearer something"):
            with self.subTest(value=value):
                self.assertTrue(self.call(fly_token=value))

    def test_expiry_boundary_is_enforced(self) -> None:
        self.assertEqual(self.call(expires_at="2026-12-01T00:00:00Z"), ())
        self.assertTrue(self.call(expires_at="2026-11-30T23:59:59Z"))
        self.assertTrue(self.call(expires_at="2027-02-01T00:00:00Z"))

    def test_invalid_expiry_shapes_fail_closed(self) -> None:
        for value in ("", "tomorrow", "2026-12-31", "2026-12-31T00:00:00"):
            with self.subTest(value=value):
                self.assertTrue(self.call(expires_at=value))

    def test_check_can_be_scoped_to_one_credential(self) -> None:
        self.assertEqual(self.call(webhook="", require_webhook=False), ())
        self.assertEqual(self.call(fly_token="", require_fly=False), ())

    def test_webhook_anchor_mutation_turns_rejection_red(self) -> None:
        # An unanchored regex still finds the URL, so the hostname guard alone
        # would accept a value with trailing data. Removing the anchors must
        # break the negative assertion that protects the webhook input.
        value = "https://alerts.example.invalid/hook\nignored"
        self.assertTrue(self.call(webhook=value))

        class SearchInsteadOfFullMatch:
            def fullmatch(self, candidate: str) -> re.Match[str] | None:
                return re.search(r"https://\S+", candidate)

        with mock.patch.object(CHECK, "WEBHOOK", SearchInsteadOfFullMatch()):
            with self.assertRaises(AssertionError):
                self.assertTrue(self.call(webhook=value))

    def test_timezone_mutation_turns_rejection_red(self) -> None:
        # Attaching UTC to a naive timestamp recreates the old fail-open bug.
        # The security assertion must turn red when that guard is mutated.
        value = "2026-12-31T00:00:00"
        self.assertTrue(self.call(expires_at=value))

        def assume_utc(raw: str) -> dt.datetime:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=dt.timezone.utc)

        with mock.patch.object(CHECK, "timestamp", assume_utc):
            with self.assertRaises(AssertionError):
                self.assertTrue(self.call(expires_at=value))

    def test_failure_output_never_contains_credential_values(self) -> None:
        token = "Bearer super-secret-token-value"
        webhook = "http://alerts.example.invalid/private-hook"
        output = io.StringIO()
        error = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {
                "FLY_API_TOKEN": token,
                "FLY_API_TOKEN_EXPIRES_AT": "tomorrow",
                "ALERT_WEBHOOK_URL": webhook,
            },
            clear=False,
        ):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                result = CHECK.main(
                    [
                        "check_automation_credentials.py",
                        "--require-fly",
                        "--require-webhook",
                        "--now",
                        "2026-11-01T00:00:00Z",
                    ]
                )
        self.assertEqual(result, 1)
        rendered = output.getvalue() + error.getvalue()
        self.assertNotIn(token, rendered)
        self.assertNotIn(webhook, rendered)


if __name__ == "__main__":
    unittest.main()
