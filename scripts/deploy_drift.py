#!/usr/bin/env python3
"""Compare what production runs against what `latest` was promoted from.

CI is the only supported release path, but `fly deploy` typed by hand builds
this Dockerfile and pushes straight to the app: no digest gate, no smoke test,
no verification step. Nothing then records that production and `latest` have
parted ways.

Two JSON documents are compared, both read from the Fly API:

    deploy_drift.py <releases-json> <status-json> <latest-index-json>

Exit codes: `0` in sync, `1` drift, `2` unreadable input (fail closed, because
"cannot tell" must never read as "fine").
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AMD64 = "linux/amd64"


class Unreadable(Exception):
    """The API answered something the comparison cannot use."""


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Unreadable(f"{path}: {error}") from error


def deployed_image(releases: object, status: object) -> str:
    """The digest every machine is running, checked against the newest release."""
    if not isinstance(status, dict):
        raise Unreadable("status is not an object")
    machines = status.get("Machines")
    if not isinstance(machines, list) or not machines:
        raise Unreadable("status carries no machine")
    digests = set()
    for machine in machines:
        if not isinstance(machine, dict):
            raise Unreadable("a machine is not an object")
        ref = machine.get("image_ref")
        if not isinstance(ref, dict):
            raise Unreadable("a machine carries no image_ref")
        digest = ref.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise Unreadable(f"a machine carries no usable digest: {digest!r}")
        digests.add(
            f"{ref.get('registry', '')}/{ref.get('repository', '')}@{digest}"
        )
    if len(digests) != 1:
        raise Unreadable(f"machines disagree on the image: {sorted(digests)}")
    running = digests.pop()

    if not isinstance(releases, list) or not releases:
        raise Unreadable("no release recorded")
    complete = [
        release
        for release in releases
        if isinstance(release, dict)
        and str(release.get("Status", "")).lower() == "complete"
    ]
    if not complete:
        raise Unreadable("no completed release recorded")
    newest = max(complete, key=lambda release: int(release.get("Version") or 0))
    if newest.get("ImageRef") != running:
        raise Unreadable(
            f"the newest release {newest.get('ImageRef')} is not what the machine"
            f" runs ({running})"
        )
    return running


def published_digest(index: object) -> str:
    """The linux/amd64 child of the `latest` index, never an attestation.

    A raw registry document carries no repository name, so only the digest is
    returned; the repository side comes from the machine that is running.
    """
    if not isinstance(index, dict):
        raise Unreadable("latest is not an object")
    manifests = index.get("Manifests") or index.get("manifests")
    if not isinstance(manifests, list) or not manifests:
        raise Unreadable("latest has no manifest list")
    # A raw registry document lower-cases the key; `imagetools inspect` prints
    # the same data capitalised. Accept both shapes rather than one caller's.
    found = []
    for manifest in manifests:
        platform = manifest.get("platform") or manifest.get("Platform") or {}
        if not isinstance(platform, dict):
            continue
        if (
            str(platform.get("os", "")).lower() == "linux"
            and str(platform.get("architecture", "")).lower() == "amd64"
        ):
            found.append(manifest)
    if len(found) != 1:
        raise Unreadable(f"latest has {len(found)} linux/amd64 children")
    digest = found[0].get("Digest") or found[0].get("digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise Unreadable(f"the amd64 child carries no digest: {digest!r}")
    return digest


def check(releases: object, status: object, index: object) -> str:
    running = deployed_image(releases, status)
    published = published_digest(index)
    if not running.endswith(f"@{published}"):
        raise Drift(running, published)
    return running


class Drift(Exception):
    def __init__(self, running: str, published: str) -> None:
        super().__init__(f"production runs {running} but latest is {published}")
        self.running = running
        self.published = published


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(f"usage: {argv[0]} <releases> <status> <latest-index>", file=sys.stderr)
        return 2
    try:
        running = check(*(_load(Path(path)) for path in argv[1:]))
    except Unreadable as error:
        print(f"cannot compare: {error}", file=sys.stderr)
        return 2
    except Drift as drift:
        print(f"DRIFT: {drift}", file=sys.stderr)
        return 1
    print(f"in sync: {running}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
