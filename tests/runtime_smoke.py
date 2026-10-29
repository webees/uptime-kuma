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
RESTIC = Path("restic.sh")
RESTIC_ENV = {
    "RESTIC_PASSWORD": "fixture-password",
    "RESTIC_REPOSITORY": "/w/repo",
    "RESTIC_DATA_DIR": "/w/data",
    "RESTIC_LOG_DIR": "/w/logs",
}
PREPARE = "mkdir -p /w/data /w/repo /w/logs"

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
    checks = [check_sqlite_consistency]
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
