#!/usr/bin/env python3
"""Validate automation credentials without printing their values.

The deployment token is intentionally short-lived and app-scoped. GitHub can
keep an expired secret indefinitely, so a scheduled preflight has to compare a
published expiry marker with the current time and fail closed before the deploy
job discovers the problem halfway through a release.

    check_automation_credentials.py --require-webhook --require-fly
    check_automation_credentials.py --require-fly --minimum-days 30 --maximum-days 90

Exit codes: `0` valid, `1` missing/invalid/expiring, `2` usage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from urllib.parse import urlparse

DEFAULT_MINIMUM_DAYS = 30
DEFAULT_MAXIMUM_DAYS = 90
WEBHOOK = re.compile(r"^https://\S+$")


class Invalid(Exception):
    """A credential is unusable, without carrying its secret value."""


def timestamp(raw: str) -> dt.datetime:
    if not raw.strip():
        raise Invalid("FLY_API_TOKEN_EXPIRES_AT is empty")
    value = raw.strip()
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Invalid("FLY_API_TOKEN_EXPIRES_AT is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise Invalid("FLY_API_TOKEN_EXPIRES_AT must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def webhook_problem(value: str) -> str | None:
    if not value:
        return "ALERT_WEBHOOK_URL is missing"
    if WEBHOOK.fullmatch(value) is None:
        return "ALERT_WEBHOOK_URL must be one HTTPS URL"
    parsed = urlparse(value)
    if not parsed.hostname:
        return "ALERT_WEBHOOK_URL has no host"
    return None


def check(
    *,
    webhook: str,
    fly_token: str,
    expires_at: str,
    now: dt.datetime,
    minimum_days: int = DEFAULT_MINIMUM_DAYS,
    maximum_days: int = DEFAULT_MAXIMUM_DAYS,
    require_webhook: bool = False,
    require_fly: bool = False,
) -> tuple[str, ...]:
    errors: list[str] = []
    if require_webhook:
        problem = webhook_problem(webhook)
        if problem:
            errors.append(problem)
    if require_fly:
        if not fly_token:
            errors.append("FLY_API_TOKEN is missing")
        elif not fly_token.startswith("FlyV1"):
            errors.append("FLY_API_TOKEN is not a Fly deploy token")
        try:
            expiry = timestamp(expires_at)
            remaining = expiry - now.astimezone(dt.timezone.utc)
            if remaining < dt.timedelta(days=minimum_days):
                errors.append(
                    f"FLY_API_TOKEN expires in fewer than {minimum_days} days"
                )
            if remaining > dt.timedelta(days=maximum_days):
                errors.append(
                    f"FLY_API_TOKEN expiry exceeds the {maximum_days}-day policy"
                )
        except Invalid as error:
            errors.append(str(error))
    return tuple(errors)


def parse_now(raw: str | None) -> dt.datetime:
    if raw is None:
        return dt.datetime.now(dt.timezone.utc)
    try:
        return timestamp(raw)
    except Invalid as error:
        raise Invalid(f"--now: {error}") from error


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webhook-env", default="ALERT_WEBHOOK_URL")
    parser.add_argument("--fly-token-env", default="FLY_API_TOKEN")
    parser.add_argument("--expires-env", default="FLY_API_TOKEN_EXPIRES_AT")
    parser.add_argument("--minimum-days", type=int, default=DEFAULT_MINIMUM_DAYS)
    parser.add_argument("--maximum-days", type=int, default=DEFAULT_MAXIMUM_DAYS)
    parser.add_argument("--require-webhook", action="store_true")
    parser.add_argument("--require-fly", action="store_true")
    parser.add_argument("--now", help="testing override, ISO-8601 UTC")
    args = parser.parse_args(argv[1:])

    if not args.require_webhook and not args.require_fly:
        parser.error("select --require-webhook and/or --require-fly")
    if not 1 <= args.minimum_days <= args.maximum_days <= 365:
        parser.error("require 1 <= minimum-days <= maximum-days <= 365")
    try:
        now = parse_now(args.now)
    except Invalid as error:
        print(f"credential check failed: {error}", file=sys.stderr)
        return 1

    errors = check(
        webhook=os.environ.get(args.webhook_env, ""),
        fly_token=os.environ.get(args.fly_token_env, ""),
        expires_at=os.environ.get(args.expires_env, ""),
        now=now,
        minimum_days=args.minimum_days,
        maximum_days=args.maximum_days,
        require_webhook=args.require_webhook,
        require_fly=args.require_fly,
    )
    if errors:
        for error in errors:
            print(f"::error title=Automation credential check::{error}")
        return 1
    print("automation credentials are configured and within the expiry policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
