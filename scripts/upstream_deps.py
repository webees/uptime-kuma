#!/usr/bin/env python3
"""Resolve, compare and apply the pinned upstream dependencies.

`Dockerfile` pins five moving values: the Uptime Kuma release, the digest of
that release's image, the supercronic release, and the two per-architecture
checksums. A human cannot keep reading those sources every day, and a stale pin
means security fixes never reach the image.

    upstream_deps.py resolve [--github-output PATH]
    upstream_deps.py compare --uptime-version V --uptime-digest D ...
    upstream_deps.py apply   --uptime-version V --uptime-digest D ...

`resolve` reads the public release metadata. `compare` prints the pins that
differ and classifies the largest bump as `patch`, `minor`, `major` or
`prerelease`, which is what the workflow's auto-merge policy keys on. `apply`
rewrites only the five lines the pins live on and refuses a Dockerfile whose
shape no longer matches, so a renamed ARG cannot be silently left behind.

Exit codes: `0` success, `1` a refusal, `2` usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = Path(os.environ.get("UPSTREAM_DEPS_DOCKERFILE") or ROOT / "Dockerfile")

UPSTREAM_REPO = "louislam/uptime-kuma"
SUPERCRONIC_REPO = "aptible/supercronic"
IMAGE_REPOSITORY = "louislam/uptime-kuma"
HTTP_TIMEOUT = int(os.environ.get("UPSTREAM_DEPS_TIMEOUT") or 60)
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ASSET_BYTES = 64 * 1024 * 1024

VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)+(?:-[0-9A-Za-z.]+)?$")
STABLE = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)+$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
CHECKSUM = re.compile(r"^[0-9a-f]{64}$")


class Refused(Exception):
    """A shape this script will not rewrite."""


def github_api(path: str) -> object:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return fetch_json(f"https://api.github.com{path}", headers)


def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as answer:
            length = answer.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > MAX_JSON_BYTES:
                raise Refused(f"{url}: response exceeds {MAX_JSON_BYTES} bytes")
            payload = answer.read(MAX_JSON_BYTES + 1)
    except (urllib.error.URLError, OSError) as error:
        raise Refused(f"{url}: {error}") from error
    if len(payload) > MAX_JSON_BYTES:
        raise Refused(f"{url}: response exceeds {MAX_JSON_BYTES} bytes")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refused(f"{url}: malformed JSON: {error}") from error


def fetch_bytes(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as answer:
            length = answer.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > MAX_ASSET_BYTES:
                raise Refused(f"{url}: asset exceeds {MAX_ASSET_BYTES} bytes")
            payload = answer.read(MAX_ASSET_BYTES + 1)
    except (urllib.error.URLError, OSError) as error:
        raise Refused(f"{url}: {error}") from error
    if len(payload) > MAX_ASSET_BYTES:
        raise Refused(f"{url}: asset exceeds {MAX_ASSET_BYTES} bytes")
    return payload


def release_digest(tag: str) -> str:
    """The manifest-list digest Docker Hub serves for one tag."""
    auth = fetch_json(
        "https://auth.docker.io/token"
        f"?service=registry.docker.io&scope=repository:{IMAGE_REPOSITORY}:pull"
    )
    if not isinstance(auth, dict) or not isinstance(auth.get("token"), str):
        raise Refused("Docker Hub returned no registry token")
    token = auth["token"]
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
        raise Refused("Docker Hub returned a malformed registry token")
    request = urllib.request.Request(
        f"https://registry-1.docker.io/v2/{IMAGE_REPOSITORY}/manifests/{tag}",
        method="HEAD",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": (
                "application/vnd.oci.image.index.v1+json,"
                "application/vnd.docker.distribution.manifest.list.v2+json"
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as answer:
            digest = answer.headers.get("Docker-Content-Digest", "")
    except (urllib.error.URLError, OSError) as error:
        raise Refused(f"{IMAGE_REPOSITORY}:{tag}: {error}") from error
    if not DIGEST.match(digest):
        raise Refused(f"{IMAGE_REPOSITORY}:{tag} has no usable digest: {digest!r}")
    return digest


def upstream_release() -> str:
    """The newest official, non-draft, non-prerelease Uptime Kuma tag."""
    release = github_api(f"/repos/{UPSTREAM_REPO}/releases/latest")
    if not isinstance(release, dict):
        raise Refused("the upstream release response is not an object")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not STABLE.match(tag):
        raise Refused(f"upstream latest release tag is not a stable version: {tag!r}")
    if release.get("draft") or release.get("prerelease"):
        raise Refused(f"upstream latest release {tag} is not a published stable release")
    return tag


def supercronic_release() -> tuple[str, dict[str, str]]:
    """The newest stable supercronic tag and the sha256 of each binary."""
    releases = github_api(f"/repos/{SUPERCRONIC_REPO}/releases?per_page=100")
    if not isinstance(releases, list):
        raise Refused("the supercronic release list is not an array")
    usable = []
    for release in releases:
        if not isinstance(release, dict):
            raise Refused("a supercronic release is not an object")
        tag = release.get("tag_name")
        if (
            isinstance(tag, str)
            and re.fullmatch(r"v[0-9]+(?:\.[0-9]+){1,2}", tag)
            and not release.get("draft")
            and not release.get("prerelease")
        ):
            usable.append(release)
    if not usable:
        raise Refused("no stable supercronic release was published")
    latest = max(usable, key=lambda item: numeric(item["tag_name"]))
    tag = latest["tag_name"]
    assets = latest.get("assets")
    if not isinstance(assets, list):
        raise Refused(f"supercronic {tag} lists no assets")
    sums = {}
    for arch in ("amd64", "arm64"):
        name = f"supercronic-linux-{arch}"
        urls = [
            asset.get("browser_download_url")
            for asset in assets
            if isinstance(asset, dict) and asset.get("name") == name
        ]
        if len(urls) != 1 or not isinstance(urls[0], str):
            raise Refused(f"supercronic {tag} has no single {name} asset")
        url = urls[0]
        expected = f"https://github.com/{SUPERCRONIC_REPO}/releases/download/{tag}/{name}"
        if url != expected:
            raise Refused(f"untrusted supercronic download URL: {url}")
        sums[arch] = hashlib.sha256(fetch_bytes(url)).hexdigest()
        if not CHECKSUM.match(sums[arch]):
            raise Refused(f"supercronic {arch} digest is malformed")
    return tag, sums


def numeric(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag.lstrip("v").split("."))


def emit(name: str, value: str, out: Path | None) -> None:
    line = f"{name}={value}"
    if out is None:
        print(line)
        return
    with out.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


PINS = {
    "uptime_version": re.compile(r"(?m)^ARG UPTIME_KUMA_VERSION=(?P<v>\S+)$"),
    "uptime_digest": re.compile(
        r"(?m)^FROM louislam/uptime-kuma:\$\{UPTIME_KUMA_VERSION\}@(?P<v>sha256:[0-9a-f]{64})$"
    ),
    "supercronic_version": re.compile(
        r"(?m)^ARG SUPERCRONIC_URL=https://github\.com/aptible/supercronic/releases/download/v(?P<v>\S+)/supercronic-linux-\$\{TARGETARCH\}$"
    ),
    "supercronic_sha_amd64": re.compile(r"(?m)^ARG SUPERCRONIC_SHA256_amd64=(?P<v>[0-9a-f]{64})$"),
    "supercronic_sha_arm64": re.compile(r"(?m)^ARG SUPERCRONIC_SHA256_arm64=(?P<v>[0-9a-f]{64})$"),
}

REWRITES = {
    "uptime_version": "ARG UPTIME_KUMA_VERSION={value}",
    "uptime_digest": "FROM louislam/uptime-kuma:${{UPTIME_KUMA_VERSION}}@{value}",
    "supercronic_version": (
        "ARG SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/"
        "download/v{value}/supercronic-linux-${{TARGETARCH}}"
    ),
    "supercronic_sha_amd64": "ARG SUPERCRONIC_SHA256_amd64={value}",
    "supercronic_sha_arm64": "ARG SUPERCRONIC_SHA256_arm64={value}",
}


def current() -> dict[str, str]:
    """The five pins as the Dockerfile states them today."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    found = {}
    for name, pattern in PINS.items():
        matches = pattern.findall(text)
        if len(matches) != 1:
            raise Refused(f"{DOCKERFILE} names {name} {len(matches)} times, expected once")
        found[name] = matches[0]
    return found


def valid(name: str, value: str) -> bool:
    if name == "uptime_version":
        return bool(VERSION.match(value))
    if name == "uptime_digest":
        return bool(DIGEST.match(value))
    if name == "supercronic_version":
        return bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", value))
    return bool(CHECKSUM.match(value))


def bump_kind(name: str, have: str, want: str) -> str:
    """How much a single changed pin moved, from the pinned value's view."""
    if name not in ("uptime_version", "supercronic_version"):
        return "patch"
    old = re.split(r"[-+]", have, maxsplit=1)[0].split(".")
    new = re.split(r"[-+]", want, maxsplit=1)[0].split(".")
    old_num = tuple(int(part) for part in old)
    new_num = tuple(int(part) for part in new)
    if new_num < old_num:
        return "downgrade"
    if "-" in want:
        return "prerelease"
    if new_num[:1] != old_num[:1]:
        return "major"
    if new_num[:2] != old_num[:2]:
        return "minor"
    return "patch"


ORDER = {"patch": 0, "minor": 1, "major": 2, "prerelease": 3, "downgrade": 4}


def compare(want: dict[str, str], out: Path | None) -> int:
    have = current()
    changed = False
    biggest = "patch"
    for name in PINS:
        if not valid(name, want[name]):
            raise Refused(f"{name} has an invalid value: {want[name]!r}")
        if have[name] == want[name]:
            print(f"ok   {name}")
            continue
        changed = True
        print(f"diff {name}: {have[name]} -> {want[name]}")
        kind = bump_kind(name, have[name], want[name])
        biggest = max(biggest, kind, key=lambda item: ORDER[item])
    emit("changed", "true" if changed else "false", out)
    emit("bump", biggest, out)
    return 0


def apply(want: dict[str, str], out: Path | None) -> int:
    text = DOCKERFILE.read_text(encoding="utf-8")
    for name, pattern in PINS.items():
        value = want[name]
        if not valid(name, value):
            raise Refused(f"{name} has an invalid value: {value!r}")
        replacement = REWRITES[name].format(value=value)
        text, count = pattern.subn(replacement, text, count=1)
        if count != 1:
            raise Refused(f"{DOCKERFILE} no longer names {name} exactly once")
    DOCKERFILE.write_text(text, encoding="utf-8")
    print(f"updated {DOCKERFILE}")
    emit("applied", "true", out)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="upstream_deps.py")
    parser.add_argument("--github-output", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("resolve")
    for name in ("compare", "apply"):
        sub = commands.add_parser(name)
        for pin in PINS:
            sub.add_argument(f"--{pin.replace('_', '-')}", required=True)
    args = parser.parse_args(argv)
    if args.command == "resolve":
        version = upstream_release()
        tag, sums = supercronic_release()
        values = {
            "uptime_version": version,
            "uptime_digest": release_digest(version),
            "supercronic_version": tag.lstrip("v"),
            "supercronic_sha_amd64": sums["amd64"],
            "supercronic_sha_arm64": sums["arm64"],
        }
        for name, value in values.items():
            emit(name, value, args.github_output)
        return 0
    want = {name: getattr(args, name) for name in PINS}
    if args.command == "compare":
        return compare(want, args.github_output)
    return apply(want, args.github_output)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Refused as error:
        print(f"refused: {error}", file=sys.stderr)
        sys.exit(1)
