#!/usr/bin/env python3
"""Fail when a scheduled workflow has gone silent or been disabled.

GitHub exposes a disabled workflow as metadata, not as a failed run. A cron
that simply stops firing produces no run at all. This check reads both states
so "nothing to update" cannot hide a dead schedule.

    check_schedules.py --workflow ghcr.yaml=192 --workflow deploy-drift.yml=48

The repository and token come from `GITHUB_REPOSITORY` and `GITHUB_TOKEN`.
Each workflow gets a grace period from its creation or last file update until
its first successful scheduled run. Unreadable or malformed API data fails
closed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HTTP_TIMEOUT = int(os.environ.get("SCHEDULE_CHECK_TIMEOUT") or 30)
MAX_JSON_BYTES = 2 * 1024 * 1024
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml$")
FUTURE_SKEW_SECONDS = 300


class Refused(Exception):
    """A workflow state this check will not interpret as healthy."""


def fetch_json(url: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "uptime-kuma-schedule-check",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as answer:
            payload = answer.read(MAX_JSON_BYTES + 1)
    except (urllib.error.URLError, OSError) as error:
        raise Refused(f"{url}: {error}") from error
    if len(payload) > MAX_JSON_BYTES:
        raise Refused(f"{url}: response exceeds {MAX_JSON_BYTES} bytes")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refused(f"{url}: malformed JSON: {error}") from error
    if not isinstance(document, dict):
        raise Refused(f"{url}: response is not an object")
    return document


def timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise Refused(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Refused(f"{label} is malformed: {value!r}") from error
    if parsed.tzinfo is None:
        raise Refused(f"{label} has no timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def check_workflow(
    repository: str,
    workflow: str,
    limit_hours: int,
    now: datetime,
    api_base: str,
) -> None:
    encoded = urllib.parse.quote(workflow, safe="")
    base = f"{api_base}/repos/{repository}/actions/workflows/{encoded}"
    metadata = fetch_json(base)
    state = metadata.get("state")
    if state != "active":
        raise Refused(f"{workflow} is {state or 'in an unknown state'}, not active")

    runs = fetch_json(f"{base}/runs?event=schedule&status=success&per_page=1")
    records = runs.get("workflow_runs")
    if not isinstance(records, list):
        raise Refused(f"{workflow}: scheduled runs response has no workflow_runs array")
    if records:
        if not isinstance(records[0], dict):
            raise Refused(f"{workflow}: scheduled run is not an object")
        last = timestamp(records[0].get("created_at"), f"{workflow} last success")
    else:
        # A newly added or newly scheduled workflow has no run yet. Its own age
        # is the grace period; this cannot excuse a workflow that has been
        # silent for weeks without a file change.
        changed_at = metadata.get("updated_at") or metadata.get("created_at")
        last = timestamp(changed_at, f"{workflow} update time")

    delta = (now - last).total_seconds()
    if delta < -FUTURE_SKEW_SECONDS:
        raise Refused(f"{workflow}: last success is in the future ({last.isoformat()})")
    age_hours = max(0, int(delta // 3600))
    print(f"{workflow}: last scheduled success {age_hours}h ago (limit {limit_hours}h)")
    if age_hours > limit_hours:
        raise Refused(f"{workflow} has not succeeded on schedule in {age_hours}h")


def parse_spec(value: str) -> tuple[str, int]:
    workflow, separator, limit = value.partition("=")
    if not separator or not WORKFLOW.match(workflow) or not limit.isdigit():
        raise argparse.ArgumentTypeError("expected WORKFLOW=HOURS")
    hours = int(limit)
    if hours < 1:
        raise argparse.ArgumentTypeError("the limit must be at least one hour")
    return workflow, hours


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check_schedules.py")
    parser.add_argument("--workflow", action="append", type=parse_spec, required=True)
    args = parser.parse_args(argv)

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not REPOSITORY.match(repository):
        print("refused: GITHUB_REPOSITORY is missing or malformed", file=sys.stderr)
        return 4
    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    now = datetime.now(timezone.utc)
    failed = False
    for workflow, limit_hours in args.workflow:
        try:
            check_workflow(repository, workflow, limit_hours, now, api_base)
        except Refused as error:
            print(f"::error::{error}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
