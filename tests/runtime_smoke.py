#!/usr/bin/env python3
"""Boot the image and assert invariants that only exist at container runtime.

Every assertion is an isolated container case; nothing touches the host except
throwaway fixture directories. Each recurring class keeps a positive, a
boundary and a negative case plus the named mutation control that proves the
guard is live.

Usage: tests/runtime_smoke.py <image>
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from suite import Case, Suite, case, docker, expect_pass, expect_red, mutate

ROOT = Path(__file__).resolve().parent
RESTIC = "scripts/restic.sh"
RESTIC_ENV = {
    "RESTIC_PASSWORD": "fixture-password",
    "RESTIC_REPOSITORY": "/w/repo",
    "RESTIC_DATA_DIR": "/w/data",
    "RESTIC_LOG_DIR": "/w/logs",
}
PREPARE = "mkdir -p /w/data /w/repo /w/logs"

# One external client, one Cloudflare edge, one Fly edge, one forged value.
CF_PEER = "173.245.48.10"
FLY_PEER = "10.0.0.7"
CF_CLIENT = "203.0.113.99"
FLY_CLIENT = "203.0.113.9"
FORGED = "198.51.100.13"

suite = Suite("runtime smoke")
handles: list[tempfile.TemporaryDirectory[str]] = []


def workspace() -> str:
    handle = tempfile.TemporaryDirectory(prefix="runtime-smoke-")
    handles.append(handle)
    return handle.name


def run(image: str, script: str, root: str, source: Path | None = None) -> Case:
    return case(
        image, script, env=RESTIC_ENV, binds=(f"{root}:/w",), prepare=PREPARE, source=source
    )


def db_value(image: str, root: str, sql: str) -> str:
    result = docker(
        "run", "--rm", "--entrypoint", "sqlite3", "-v", f"{root}:/w", image,
        "/w/data/backup.bak", sql,
    )
    return result.stdout.strip()


ECHO = "printf '%s' \"$REQ\" >/dev/null; echo ok"


def proxy(image: str, body: str, root: str, source: Path | None = None) -> Case:
    """Run the real Caddyfile in front of an echo backend on :3001.

    ``body`` receives a proxy that is already accepting connections; it must
    kill ``$caddy`` itself.
    """
    script = (
        "node /echo_srv.js & sleep 1\n"
        "{ caddy run --config /Caddyfile 2>/dev/null & } ; caddy=$!\n"
        "for _ in $(seq 1 100); do curl -s -o /dev/null http://127.0.0.1/ && break; sleep 0.2; done\n"
        f"{body}\n"
        "rc=$?; kill $caddy 2>/dev/null; exit $rc"
    )
    return case(
        image,
        script,
        env={"CADDY_DOMAINS": "allowed.example"},
        binds=(f"{root}:/w", "/tmp/echo_srv.js:/echo_srv.js:ro"),
        source=source,
    )


def get(extra: str = "") -> str:
    """One request for `/`; prints only the JSON header echo."""
    return f"curl -sS --max-time 5 {extra} -H 'Host: allowed.example' http://127.0.0.1/"


def forwarded(result: Case) -> tuple[str, str]:
    """The X-Real-IP and X-Forwarded-For values the backend actually saw."""
    body = result.output
    real = body.rsplit('"x-real-ip":"', 1)[-1].split('"', 1)[0] if '"x-real-ip":"' in body else ""
    xff = body.rsplit('"x-forwarded-for":"', 1)[-1].split('"', 1)[0] if '"x-forwarded-for":"' in body else ""
    return real, xff


def check_client_ip(image: str) -> None:
    """C2: Cloudflare beats Fly beats the peer, and forged input is ignored."""
    root = workspace()
    result = proxy(
        image,
        get(f"-H 'CF-Connecting-IP: {CF_CLIENT}' -H 'Fly-Client-IP: {FLY_CLIENT}'"
            f" -H 'X-Forwarded-For: {FORGED}' -H 'X-Real-IP: {FORGED}'"),
        root,
    )
    expect_pass(suite, result, "C2 request through the proxy", "C2 request failed")
    suite.same("C2 Cloudflare header wins", (CF_CLIENT, CF_CLIENT), forwarded(result))

    for label, extra, expected in (
        ("C2 Fly header used without Cloudflare", f"-H 'Fly-Client-IP: {FLY_CLIENT}'", FLY_CLIENT),
        ("C2 forged X-Forwarded-For ignored", f"-H 'X-Forwarded-For: {FORGED}'", "127.0.0.1"),
        ("C2 peer address used when nothing is present", "", "127.0.0.1"),
    ):
        result = proxy(image, get(extra), root)
        expect_pass(suite, result, label, f"{label}: request failed")
        suite.same(label, (expected, expected), forwarded(result))

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        mutated = mutate(
            "config/Caddyfile",
            (("\t\tclient_ip_headers CF-Connecting-IP Fly-Client-IP\n", ""),),
            Path(tmp),
        )
        control = proxy(
            image,
            get(f"-H 'CF-Connecting-IP: {CF_CLIENT}' -H 'Fly-Client-IP: {FLY_CLIENT}'"
                f" -H 'X-Forwarded-For: {FORGED}'"),
            root,
            source=mutated,
        )
        expect_pass(suite, control, "C2 mutation control still serves", "C2 mutation control failed")
        suite.same(
            "C2 mutation: header order falls back to X-Forwarded-For",
            (FORGED, FORGED),
            forwarded(control),
        )


def check_sqlite_consistency(image: str) -> None:
    """C3: snapshot the checked copy, never the live database files."""
    root = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v); insert into t values(1);'\n"
        "echo stale > /w/data/kuma.db-wal\n"
        "echo stale > /w/data/kuma.db-shm\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "restic ls latest 2>&1\n",
        root,
    )
    expect_pass(suite, result, "C3 backup runs", "C3 backup did not run")
    suite.same("C3 snapshot has the copy", True, "/w/data/backup.bak" in result.output)
    for name in ("kuma.db", "kuma.db-wal", "kuma.db-shm"):
        suite.same(f"C3 live {name} excluded", False, f"/w/data/{name}\n" in result.output)
    suite.same("C3 copy is a usable database", "1", db_value(image, root, "select count(*) from t"))

    boundary = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v);'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "sqlite3 /w/data/backup.bak 'create table stale(v)'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "sqlite3 /w/data/backup.bak \"select count(*) from sqlite_master where name='stale'\"\n",
        boundary,
    )
    expect_pass(suite, result, "C3 second run succeeds", "C3 second backup failed")
    suite.same("C3 each run rewrites the copy", "0", result.body()[-1])

    negative = workspace()
    result = run(
        image,
        "printf 'not a database' > /w/data/kuma.db\n"
        "rc=0; /restic.sh backup >/dev/null 2>&1 || rc=$?\n"
        "echo \"backup_rc=${rc}\"\n"
        "echo \"snapshots=$(restic snapshots --json 2>/dev/null)\"\n"
        "test \"$rc\" -eq 2 && test \"$(restic snapshots --json 2>/dev/null)\" = '[]'\n",
        negative,
    )
    expect_pass(suite, result, "C3 corrupt database aborts", "C3 case did not complete")
    suite.same(
        "C3 nothing is snapshotted after a failed check", ["backup_rc=2", "snapshots=[]"], result.body()
    )
    suite.same("C3 failed copy is removed", False, Path(negative, "data", "backup.bak").exists())

    tmp = workspace()
    mutated = mutate(
        RESTIC,
        (('--exclude="$DB" --exclude="$DB-journal" --exclude="$DB-wal" --exclude="$DB-shm"', "--exclude=/nonexistent"),),
        Path(tmp),
    )
    control_root = workspace()
    control = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v);'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "if restic ls latest 2>&1 | grep -qx /w/data/kuma.db; then echo LIVE_IN_SNAPSHOT;"
        " else echo LIVE_ABSENT; fi\n",
        control_root,
        source=mutated,
    )
    expect_pass(suite, control, "C3 mutation control still backs up", "C3 mutation control backup failed")
    suite.same(
        "C3 mutation: live files return to the snapshot",
        "LIVE_IN_SNAPSHOT",
        control.body()[-1].strip(),
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: tests/runtime_smoke.py <image>")
    image = sys.argv[1]
    checks = [check_sqlite_consistency, check_client_ip]
    only = os.environ.get("SMOKE_ONLY", "").split()
    try:
        for check in checks:
            if not only or check.__name__ in only:
                check(image)
    finally:
        for handle in handles:
            handle.cleanup()
    suite.report()


if __name__ == "__main__":
    main()
