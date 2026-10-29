#!/usr/bin/env python3
"""Boot the image and assert invariants that only exist at container runtime.

Every assertion is an isolated container case; nothing touches the host except
throwaway fixture directories. Each recurring class keeps a positive, a
boundary and a negative case plus the named mutation control that proves the
guard is live.

Usage: tests/runtime_smoke.py <image>
"""

from __future__ import annotations

import itertools
import os
import shutil
import sys
import tempfile
import time
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

# Byte-exact Caddyfile fragments, so the C1 mutation controls stay anchored to
# the shipped text instead of drifting into untested rewrites.
HEADER_BLOCK = """\theader {
\t\tStrict-Transport-Security "max-age=31536000;"
\t\tX-Content-Type-Options "nosniff"
\t\tX-Frame-Options "DENY"
\t\tX-XSS-Protection "1; mode=block"
\t\tReferrer-Policy "strict-origin-when-cross-origin"
\t\tPermissions-Policy "camera=(), microphone=(), geolocation=(), payment=()"
\t\tX-Robots-Tag "noindex, nofollow"
\t\t-Server
\t\t-X-Powered-By
\t\t-Last-Modified
\t}"""
ROUTE_OPEN = """\troute {"""
HEALTH_BLOCK = """\t\t# Fly's health check must not depend on the application or the allowlist.
\t\t@health path /health
\t\thandle @health {
\t\t\trespond "OK" 200
\t\t}"""
DENIED_BLOCK = """\t\t# One matcher owns the allowlist: unset means allow everything, otherwise
\t\t# only the listed names pass. `:localhost` also keeps the host matcher
\t\t# from being null when the value is empty.
\t\t@denied {
\t\t\texpression {env.CADDY_DOMAINS} != ""
\t\t\tnot host {$CADDY_DOMAINS:localhost}
\t\t}
\t\thandle @denied {
\t\t\trespond "Access Denied: Domain not allowed" 403
\t\t}"""
GATE_BLOCK = HEALTH_BLOCK + "\n\n" + DENIED_BLOCK
BACKEND_OPEN = """\t\thandle {
\t\t\treverse_proxy localhost:3001 {"""

# One external client, one Cloudflare edge, one Fly edge, one forged value.
CF_PEER = "173.245.48.10"
FLY_PEER = "10.0.0.7"
CF_CLIENT = "203.0.113.99"
FLY_CLIENT = "203.0.113.9"
FORGED = "198.51.100.13"

suite = Suite("runtime smoke")
handles: list[tempfile.TemporaryDirectory[str]] = []
containers: list[str] = []
names = itertools.count()


def workspace() -> str:
    handle = tempfile.TemporaryDirectory(prefix="runtime-smoke-")
    handles.append(handle)
    return handle.name


def cleanup(handle: tempfile.TemporaryDirectory[str], image: str) -> None:
    """Remove a fixture from the host, tolerating container-owned inodes.

    The containers run as root, so files they create inside a bind mount are
    root-owned on the host. CI's unprivileged runner can then delete them but
    not chmod their parent, which makes `TemporaryDirectory.cleanup` raise
    after a fully green run. The host tries first; if it loses the race with
    permissions, the container deletes the contents and the host retries.
    """
    path = handle.name
    try:
        handle.cleanup()
        return
    except OSError:
        pass
    docker("run", "--rm", "-v", f"{path}:/w", "--entrypoint", "bash", image,
           "-c", "rm -rf /w/* /w/.[!.]* 2>/dev/null || :", check=False)
    shutil.rmtree(path, ignore_errors=True)


def stack(image: str, delay: str = "0.2", source: Path | None = None) -> str:
    """Start a detached container running the real process table."""
    name = f"runtime-supervise-{os.getpid()}-{next(names)}"
    args = ["run", "--detach", "--name", name, "--env", f"SUPERVISE_RESTART_DELAY={delay}"]
    if source is None:
        docker(*args, image)
    else:
        body = (
            "cp -a /src/config/crontab /src/config/Caddyfile /src/scripts/restic.sh "
            "/src/scripts/services.sh / && chmod +x /services.sh && exec /entrypoint.sh"
        )
        docker(*args, "-v", f"{source}:/src:ro", "--entrypoint", "bash", image, "-c", body)
    containers.append(name)
    return name


def pids(container: str, process: str) -> list[int]:
    result = docker("exec", container, "pidof", process, check=False)
    return [int(value) for value in result.stdout.split() if value.isdigit()]


def replacement(container: str, process: str, previous: int) -> int | None:
    """Wait for a different pid; a restart must never reuse the dead one."""
    for _ in range(600):
        found = next((pid for pid in pids(container, process) if pid != previous), None)
        if found is not None:
            return found
        time.sleep(0.05)
    return None


def wait_for(container: str, process: str) -> int | None:
    for _ in range(600):
        found = pids(container, process)
        if found:
            return found[0]
        time.sleep(0.05)
    return None


def stop(container: str) -> None:
    docker("rm", "--force", container, check=False)
    if container in containers:
        containers.remove(container)


def run(
    image: str,
    script: str,
    root: str,
    source: Path | None = None,
    env: dict[str, str] | None = None,
) -> Case:
    return case(
        image,
        script,
        env={**RESTIC_ENV, **(env or {})},
        binds=(f"{root}:/w",),
        prepare=PREPARE,
        source=source,
    )


def db_value(image: str, root: str, sql: str) -> str:
    result = docker(
        "run", "--rm", "--entrypoint", "sqlite3", "-v", f"{root}:/w", image,
        "/w/data/backup.bak", sql,
    )
    return result.stdout.strip()


ECHO = "printf '%s' \"$REQ\" >/dev/null; echo ok"


def proxy(
    image: str,
    body: str,
    root: str,
    source: Path | None = None,
    env: dict[str, str] | None = None,
    normalize: bool = False,
) -> Case:
    """Run the real Caddyfile in front of an echo backend on :3001.

    ``body`` receives a proxy that is already accepting connections; it must
    kill ``$caddy`` itself. ``normalize`` runs the real entrypoint address
    audit first, which is what turns an empty ``CADDY_DOMAINS`` into "unset".
    """
    script = (
        "if [ -n \"$NORMALIZE\" ]; then . /entrypoint/config.sh; audit_addresses || exit 9; fi\n"
        "node /echo_srv.js & sleep 1\n"
        "{ caddy run --config /Caddyfile >/tmp/caddy.log 2>&1 & } ; caddy=$!\n"
        "for _ in $(seq 1 100); do curl -s -o /dev/null http://127.0.0.1:8080/ && break; sleep 0.2; done\n"
        f"{body}\n"
        "rc=$?; kill $caddy 2>/dev/null; [ \"$rc\" -eq 0 ] || tail -5 /tmp/caddy.log >&2; exit $rc"
    )
    return case(
        image,
        script,
        env={**(env if env is not None else {"CADDY_DOMAINS": "allowed.example"}), "NORMALIZE": "1" if normalize else ""},
        binds=(f"{root}:/w", f"{ROOT}/echo_srv.js:/echo_srv.js:ro"),
        source=source,
    )


def get(extra: str = "", path: str = "/") -> str:
    """One request; prints only the JSON header echo."""
    return f"curl -sS --max-time 5 {extra} -H 'Host: allowed.example' http://127.0.0.1:8080{path}; echo"


def code(host: str, path: str = "/") -> str:
    """Status code for one host/path pair, appended to the batch record."""
    return (
        f"printf 'code=%s\\n' \"$(curl -sS --max-time 5 -o /dev/null -w '%{{http_code}}' "
        f"-H 'Host: {host}' http://127.0.0.1:8080{path} || :)\" >> /tmp/codes"
    )


def batch(*parts: str) -> str:
    """Run ``code()`` requests in order, then fail on a dead proxy.

    Every request is attempted, so one negative host cannot mask the rest; a
    ``code=000`` then fails the case instead of reading as a policy decision.
    """
    return "\n".join(
        (
            "rm -f /tmp/codes; : > /tmp/codes",
            *parts,
            "cat /tmp/codes",
            "grep -q 'code=000' /tmp/codes && exit 1 || :",
        )
    )


def codes(result: Case) -> list[str]:
    """The status codes of a ``code()`` batch, in request order."""
    return [line.split("code=", 1)[1] for line in result.body() if line.startswith("code=")]


def probes(result: Case) -> list[str]:
    """Assertion lines of a case, ignoring the noisy progress output."""
    return [line for line in result.body() if line.startswith("@ ")]


UNSET = "<unset>"


def headers(host: str, path: str = "/") -> str:
    """Response headers for one host/path pair."""
    return (
        f"curl -sS --max-time 5 -o /dev/null -D - -H 'Host: {host}' "
        f"http://127.0.0.1:8080{path} | tr -d '\\r'"
    )


SECURITY_HEADERS = (
    "Strict-Transport-Security:",
    "X-Content-Type-Options:",
    "X-Frame-Options:",
    "Referrer-Policy:",
    "Permissions-Policy:",
    "X-Robots-Tag:",
)


def check_host_policy(image: str) -> None:
    """C1: one matcher, empty means allow-all, and the probe escapes the gate."""
    root = workspace()
    for label, value in (
        ("C1 unset allowlist", UNSET),
        ("C1 empty allowlist", ""),
        ("C1 blank allowlist", "  \r\n "),
    ):
        env = {k: v for k, v in (("CADDY_DOMAINS", value),) if v is not UNSET}
        result = proxy(image, batch(code("anything.example"), code("other.example", "/health")), root, env=env, normalize=True)
        expect_pass(suite, result, f"{label} starts the proxy", f"{label} failed to start")
        suite.same(f"{label} allows every host", ["200", "200"], codes(result))

    # Positive, boundary and negative in one table: exactly the listed name
    # passes, everything else answers 403.
    for label, value, hosts, expected in (
        ("C1 single listed domain", "allowed.example", ["allowed.example", "nope.example"], ["200", "403"]),
        (
            "C1 space-separated list",
            "allowed.example other.example",
            ["allowed.example", "other.example", "nope.example"],
            ["200", "200", "403"],
        ),
        ("C1 host case is case-insensitive", "allowed.example", ["ALLOWED.example", "NOPE.example"], ["200", "403"]),
        ("C1 config accepts upper case", "ALLOWED.example", ["allowed.example"], ["200"]),
        # The validator only accepts a bare name, so a trailing dot in the
        # configuration must be rejected instead of silently matching nothing.
        ("C1 config rejects a trailing dot", "allowed.example.", ["allowed.example"], ["200"]),
    ):
        result = proxy(image, batch(*(code(h) for h in hosts)), root, env={"CADDY_DOMAINS": value}, normalize=True)
        if label.endswith("trailing dot"):
            expect_red(suite, result, f"{label} refuses to start", f"{label} unexpectedly started")
        else:
            expect_pass(suite, result, f"{label} starts the proxy", f"{label} failed to start")
            suite.same(label, expected, codes(result))

    result = proxy(image, headers("nope.example"), root, env={"CADDY_DOMAINS": "allowed.example"}, normalize=True)
    expect_pass(suite, result, "C1 denied response is served", "C1 denied response failed")
    suite.same("C1 denied response is 403", True, " 403 " in result.output)
    for header in SECURITY_HEADERS:
        suite.same(f"C1 denied response keeps {header}", True, header in result.output)

    result = proxy(image, headers("nope.example", "/health"), root, env={"CADDY_DOMAINS": "allowed.example"}, normalize=True)
    expect_pass(suite, result, "C1 probe answers for a denied host", "C1 probe failed for a denied host")
    suite.same("C1 probe is not denied", True, " 200 " in result.output)
    suite.same("C1 probe keeps security headers", True, "Strict-Transport-Security:" in result.output)

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        moved_health = mutate(
            "config/Caddyfile",
            ((GATE_BLOCK, DENIED_BLOCK + "\n\n" + HEALTH_BLOCK),),
            Path(tmp),
        )
        control = proxy(
            image,
            batch(code("nope.example", "/health")),
            root,
            source=moved_health,
            env={"CADDY_DOMAINS": "allowed.example"},
            normalize=True,
        )
        expect_pass(suite, control, "C1 mutation control still serves", "C1 mutation control failed")
        suite.same("C1 mutation: probe after the gate becomes 403", ["403"], codes(control))

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        moved_header = mutate(
            "config/Caddyfile",
            (
                (HEADER_BLOCK + "\n\n", ""),
                (BACKEND_OPEN, "\t\t" + HEADER_BLOCK.replace("\n\t", "\n\t\t") + "\n\n" + BACKEND_OPEN),
            ),
            Path(tmp),
        )
        control = proxy(image, headers("nope.example"), root, source=moved_header, env={"CADDY_DOMAINS": "allowed.example"}, normalize=True)
        expect_pass(suite, control, "C1 header mutation still serves", "C1 header mutation failed")
        suite.same(
            "C1 mutation: header after the gate drops the 403 headers",
            False,
            "Strict-Transport-Security:" in control.output,
        )

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        no_unset = mutate(
            "scripts/entrypoint/config.sh",
            ((" || { unset CADDY_DOMAINS; return 0; }", " || return 0"),),
            Path(tmp),
        )
        control = proxy(
            image,
            batch(code("anything.example")),
            root,
            source=no_unset,
            env={"CADDY_DOMAINS": ""},
            normalize=True,
        )
        expect_red(suite, control, "C1 mutation control refuses to start", "C1 mutation: empty value crashes Caddy")


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


def check_restore_isolation(image: str) -> None:
    """C4: the drill stays out of live data, and in-place restore is guarded."""
    root = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v); insert into t values(42);'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "sqlite3 /w/data/kuma.db 'create table live_only(v)'\n"
        "/restic.sh restore-test >/tmp/drill 2>&1 || { cat /tmp/drill >&2; exit 1; }\n"
        "printf '@ live=%s\\n' \"$(sqlite3 /w/data/kuma.db \"select count(*) from sqlite_master where name='live_only'\")\"\n"
        "printf '@ drill=%s\\n' \"$(sqlite3 /tmp/restic-restore-test/w/data/backup.bak 'select count(*) from t')\"\n",
        root,
    )
    expect_pass(suite, result, "C4 restore drill runs", "C4 restore drill failed")
    suite.same(
        "C4 drill leaves the live database untouched and restores the snapshot",
        ["@ live=1", "@ drill=1"],
        probes(result),
    )

    root = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v); insert into t values(7);'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "id=$(restic snapshots --json 2>/dev/null | sed -E 's/.*\"short_id\":\"([^\"]+)\".*/\\1/' | head -n 1)\n"
        "rc=0; /restic.sh restore \"$id\" >/dev/null 2>&1 || rc=$?; printf '@ refusal_rc=%s\\n' \"$rc\"\n"
        "printf '@ live_after_refusal=%s\\n' \"$(sqlite3 /w/data/kuma.db 'select count(*) from t')\"\n"
        "sqlite3 /w/data/kuma.db 'insert into t values(8)'\n"
        "printf '@ live_before_restore=%s\\n' \"$(sqlite3 /w/data/kuma.db 'select count(*) from t')\"\n"
        "RESTORE_CONFIRM=yes /restic.sh restore \"$id\" >/tmp/restore 2>&1 || { cat /tmp/restore >&2; exit 1; }\n"
        "printf '@ live_after_restore=%s\\n' \"$(sqlite3 /w/data/kuma.db 'select count(*) from t')\"\n"
        "printf '@ rollback=%s\\n' \"$(sqlite3 /w/data/kuma.db.previous 'select count(*) from t')\"\n"
        "printf '@ staging_left=%s\\n' \"$(ls -d /w/data/.restore 2>/dev/null | wc -l)\"\n",
        root,
    )
    expect_pass(suite, result, "C4 in-place restore runs", "C4 in-place restore failed")
    suite.same(
        "C4 confirmation gate, live data and staging cleanup",
        [
            "@ refusal_rc=1",
            "@ live_after_refusal=1",
            "@ live_before_restore=2",
            "@ live_after_restore=1",
            # The rollback copy holds the pre-restore state, not the snapshot.
            "@ rollback=2",
            "@ staging_left=0",
        ],
        probes(result),
    )

    # A failed stage must not have touched the live file.
    root = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v); insert into t values(7);'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "id=$(restic snapshots --json 2>/dev/null | sed -E 's/.*\"short_id\":\"([^\"]+)\".*/\\1/' | head -n 1)\n"
        "sqlite3 /w/data/kuma.db 'insert into t values(8)'\n"
        "mkdir /w/data/kuma.db.previous\n"
        "rc=0; RESTORE_CONFIRM=yes /restic.sh restore \"$id\" >/dev/null 2>&1 || rc=$?\n"
        "printf '@ rc=%s\\n' \"$rc\"\n"
        "printf '@ live=%s\\n' \"$(sqlite3 /w/data/kuma.db 'select count(*) from t')\"\n",
        root,
    )
    expect_pass(suite, result, "C4 staging failure case runs", "C4 staging failure case failed")
    suite.same("C4 a failed stage leaves the live database intact", ["@ rc=2", "@ live=2"], probes(result))

    root = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "id=$(restic snapshots --json 2>/dev/null | sed -E 's/.*\"short_id\":\"([^\"]+)\".*/\\1/' | head -n 1)\n"
        "rm -f /w/data/kuma.db; ln -s /w/elsewhere.db /w/data/kuma.db\n"
        "RESTORE_CONFIRM=yes /restic.sh restore \"$id\" 2>&1; echo \"rc=$?\"\n",
        root,
    )
    expect_pass(suite, result, "C4 symlink case completes", "C4 symlink case failed")
    suite.same("C4 symlinked live database is refused", True, "rc=2" in result.output)
    suite.same("C4 refusal names the reason", True, "not a regular file" in result.output)

    # Not just symlinks: a directory standing in for the database must be
    # refused as well, before anything is written over it.
    root = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "id=$(restic snapshots --json 2>/dev/null | sed -E 's/.*\"short_id\":\"([^\"]+)\".*/\\1/' | head -n 1)\n"
        "rm -f /w/data/kuma.db; mkdir /w/data/kuma.db; : > /w/data/kuma.db/keep\n"
        "RESTORE_CONFIRM=yes /restic.sh restore \"$id\" 2>&1; echo \"rc=$?\"\n"
        "printf '@ still_dir=%s\\n' \"$(test -d /w/data/kuma.db && echo yes || echo no)\"\n"
        "printf '@ keep=%s\\n' \"$(test -f /w/data/kuma.db/keep && echo yes || echo no)\"\n",
        root,
    )
    expect_pass(suite, result, "C4 directory case completes", "C4 directory case failed")
    suite.same(
        "C4 a non-regular live database is refused untouched",
        ["@ still_dir=yes", "@ keep=yes"],
        probes(result),
    )

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        retargeted = mutate(
            RESTIC,
            ((r'local target="${RESTORE_SCRATCH:-/tmp/restic-restore-test}"', 'local target="$DATA"'),),
            Path(tmp),
        )
        control_root = workspace()
        control = run(
            image,
            "sqlite3 /w/data/kuma.db 'create table t(v); insert into t values(1);'\n"
            "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
            "sqlite3 /w/data/kuma.db 'create table live_only(v)'\n"
            "/restic.sh restore-test >/dev/null 2>&1 || exit 1\n"
            "printf '@ live_only=%s\\n' \"$(sqlite3 /w/data/kuma.db \"select count(*) from sqlite_master where name='live_only'\")\"\n",
            control_root,
            source=retargeted,
        )
        expect_pass(suite, control, "C4 mutation control completes", "C4 mutation control failed")
        suite.same(
            "C4 mutation: drill retargeted at live data overwrites it",
            ["@ live_only=0"],
            probes(control),
        )


def check_bounded_diagnostics(image: str) -> None:
    """C5: capture is bounded, and a cap that cannot be kept fails closed."""
    root = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "big=/w/data/big; for _ in $(seq 1 400); do head -c 2000 /dev/zero | tr '\\0' 'x'; done > \"$big\"\n"
        "RESTIC_LOG_MAX_BYTES=4096 /restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "printf 'log_bytes=%s\\n' \"$(wc -c < /w/logs/*.log)\"\n"
        "printf 'files=%s\\n' \"$(ls /w/logs | wc -l)\"\n"
        "printf 'temp=%s\\n' \"$(ls /w/logs | grep -c 'tmp' || true)\"\n",
        root,
    )
    expect_pass(suite, result, "C5 backup with a small cap runs", "C5 backup with a small cap failed")
    body = result.body()
    suite.same("C5 the log stays inside the cap", True, int(body[0].split("=")[1]) <= 4096)
    suite.same("C5 no temporary file is left behind", "temp=0", body[2])

    boundary = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "printf 'x%.0s' $(seq 1 9000) > /w/logs/old.log\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "printf 'trimmed=%s\\n' \"$(wc -c < /w/logs/old.log)\"\n",
        boundary,
    )
    expect_pass(suite, result, "C5 an oversized existing log is trimmed", "C5 oversized log case failed")
    suite.same("C5 existing log is capped", True, int(result.body()[-1].split("=")[1]) <= 262144)

    for label, variable, value in (
        ("C5 zero log cap", "RESTIC_LOG_MAX_BYTES", "0"),
        ("C5 fractional timeout", "RESTIC_QUICK_TIMEOUT", "1.5"),
        ("C5 negative directory cap", "RESTIC_LOG_DIR_MAX_BYTES", "-1"),
    ):
        invalid = workspace()
        result = case(
            image,
            "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
            "bash /restic.sh backup 2>&1; echo \"rc=$?\"\n",
            env={**RESTIC_ENV, variable: value},
            binds=(f"{invalid}:/w",),
            prepare=PREPARE,
        )
        expect_pass(suite, result, f"{label} is rejected", f"{label} was accepted")
        suite.same(f"{label} exits 2", "rc=2", result.body()[-1])
        suite.same(f"{label} names the variable", True, f"{variable} must be" in result.output)

    # The same stub against the real script and the mutated one isolates the
    # capture/persist bound as the only difference.
    hostile = (
        "mkdir -p /w/bin\n"
        "printf '%s\\n' '#!/bin/bash' '[ \"$1\" = cat ] && head -c 12 /w/data/kuma.db; case \"$1\" in'"
        " '  cat) exit 0 ;;'"
        " '  backup|check|forget) head -c 400000000 /dev/zero | tr \"\\\\0\" y; exit 1 ;;' '  *) exit 0 ;;' 'esac'"
        " > /w/bin/restic; chmod +x /w/bin/restic\n"
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "rc=0; PATH=\"/w/bin:$PATH\" RESTIC_LOG_MAX_BYTES=4096 /restic.sh backup >/dev/null 2>&1 || rc=$?\n"
        "printf '@ rc=%s\\n' \"$rc\"\n"
        "printf '@ log_bytes=%s\\n' \"$(wc -c < /w/logs/*.log)\"\n"
    )
    guarded = run(image, hostile, workspace())
    expect_pass(suite, guarded, "C5 hostile output still fails cleanly", "C5 hostile output case failed")
    suite.same("C5 guarded run keeps the failed log inside the cap", "4096", probes(guarded)[1].split("=")[1])

    # A failing `tail` means the bound itself is broken: the run must abort
    # rather than persist an unbounded capture. Only the first stdin capture
    # (`tail -c N`, no file operand) fails, so the observation stays on the
    # capture guard instead of also breaking trim or the write bookkeeping.
    tail_failure = (
        "mkdir -p /w/bin\n"
        "printf '%s\\n' '#!/bin/sh'"
        " 'if [ \"$1\" = -c ] && [ \"$#\" -eq 2 ]; then'"
        " '  n=\"$(cat /w/tail.calls 2>/dev/null || echo 0)\"'"
        " '  echo $((n + 1)) > /w/tail.calls'"
        " '  [ \"$n\" -gt 0 ] || exit 1'"
        " 'fi'"
        " 'exec /usr/bin/tail \"$@\"' > /w/bin/tail; chmod +x /w/bin/tail\n"
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "rc=0; PATH=\"/w/bin:$PATH\" /restic.sh backup >/dev/null 2>&1 || rc=$?\n"
        "printf '@ rc=%s\\n' \"$rc\"\n"
        "printf '@ state=%s\\n' \"$(sed -n 's/^state=//p' /w/data/backup-status.txt)\"\n"
        "printf '@ backend=%s\\n' \"$(test -f /w/repo/config && echo called || echo never)\"\n"
    )
    failed_tail = run(image, tail_failure, workspace())
    expect_pass(suite, failed_tail, "C5 failing tail case runs", "C5 failing tail case failed")
    suite.same(
        "C5 a failing tail fails closed before the backend",
        ["@ rc=2", "@ state=failed", "@ backend=never"],
        probes(failed_tail),
    )

    # `write_log_entry` cannot even create its temporary file: the run must
    # stop there rather than continue without a bound and report success.
    write_failure = (
        "mkdir -p /w/bin\n"
        "printf '%s\\n' '#!/bin/sh' 'exit 1' > /w/bin/mktemp; chmod +x /w/bin/mktemp\n"
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "rc=0; PATH=\"/w/bin:$PATH\" /restic.sh backup >/dev/null 2>&1 || rc=$?\n"
        "printf '@ rc=%s\\n' \"$rc\"\n"
        "printf '@ state=%s\\n' \"$(sed -n 's/^state=//p' /w/data/backup-status.txt)\"\n"
        "printf '@ backend=%s\\n' \"$(test -f /w/repo/config && echo called || echo never)\"\n"
    )
    failed_write = run(image, write_failure, workspace())
    expect_pass(suite, failed_write, "C5 failing log write case runs", "C5 failing log write case failed")
    suite.same(
        "C5 a failing log write fails closed before the backend",
        ["@ rc=2", "@ state=failed", "@ backend=never"],
        probes(failed_write),
    )

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        unbounded = mutate(
            RESTIC,
            (
                (r'body="$( { "$@" 2>&1 | tail -c "$MAX_LOG_BYTES"; } )"', 'body="$("$@" 2>&1)"'),
                (r'| tail -c "$MAX_LOG_BYTES" > "$tmp" && mv -f "$tmp" "$LOG"', '> "$tmp" && mv -f "$tmp" "$LOG"'),
            ),
            Path(tmp),
        )
        control_root = workspace()
        control = run(image, hostile, control_root, source=unbounded)
        expect_pass(suite, control, "C5 mutation control still backs up", "C5 mutation control failed")
        suite.same(
            "C5 mutation: the failed run still exits 2",
            ["@ rc=2"],
            probes(control)[:1],
        )
        suite.same(
            "C5 mutation: the log grows past the cap",
            "4299",
            probes(control)[1].split("=")[1],
        )

        # Dropping the capture-failure guard makes the same broken `tail` look
        # like a good run.
        (Path(tmp) / "swallowed").mkdir()
        swallowed = mutate(
            RESTIC,
            (("    [ \"$rc\" -eq 0 ] || { echo \"Error: cannot capture bounded log output\" >&2; exit 2; }\n",
              ""),),
            Path(tmp) / "swallowed",
        )
        ignored = run(image, tail_failure, workspace(), source=swallowed)
        expect_pass(suite, ignored, "C5 swallowed-failure mutation runs", "C5 swallowed-failure mutation failed")
        suite.same(
            "C5 mutation: without the capture guard a failed tail still succeeds",
            ["@ rc=0", "@ state=ok", "@ backend=called"],
            probes(ignored),
        )

        # The same downgrade for persistence: a broken write becomes a warning
        # and the run finishes `ok` with nothing on disk.
        (Path(tmp) / "unwritten").mkdir()
        unwritten = mutate(
            RESTIC,
            (("    write_log_entry \"$body\" || exit 2\n", "    write_log_entry \"$body\" || :\n"),),
            Path(tmp) / "unwritten",
        )
        lost = run(image, write_failure, workspace(), source=unwritten)
        expect_pass(suite, lost, "C5 swallowed-write mutation runs", "C5 swallowed-write mutation failed")
        suite.same(
            "C5 mutation: without the write guard a failed write still succeeds",
            ["@ rc=0", "@ state=ok", "@ backend=called"],
            probes(lost),
        )

    # A cap that cannot be re-enforced after the run must not end in `ok`.
    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        warning_only = mutate(
            RESTIC,
            (("    if ! trim_logs; then\n", "    if ! trim_logs || false; then\n"),),
            Path(tmp),
        )
        control_root = workspace()
        control = run(
            image,
            "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
            "printf 'x%.0s' $(seq 1 5000) > /w/logs/old.log\n"
            "rc=0; RESTIC_LOG_DIR_MAX_BYTES=4096 /restic.sh backup >/dev/null 2>&1 || rc=$?\n"
            "printf '@ rc=%s\\n' \"$rc\"\n"
            "printf '@ state=%s\\n' \"$(sed -n 's/^state=//p' /w/data/backup-status.txt)\"\n",
            control_root,
            source=warning_only,
        )
        expect_pass(suite, control, "C5 post-run guard mutation still runs", "C5 post-run mutation failed")
        suite.same(
            "C5 mutation: an unmaintainable directory after the run becomes success",
            ["@ rc=0", "@ state=ok"],
            probes(control),
        )


def check_lock_and_state(image: str) -> None:
    """C6: one run at a time, and a status write never truncates the old marker."""
    root = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "printf '@ state_before=%s\\n' \"$(sed -n 's/^state=//p' /w/data/backup-status.txt)\"\n"
        "exec 9>/w/logs/backup.lock; flock 9\n"
        "rc=0; /restic.sh backup >/dev/null 2>&1 || rc=$?; printf '@ skipped_rc=%s\\n' \"$rc\"\n"
        "printf '@ state_after=%s\\n' \"$(sed -n 's/^state=//p' /w/data/backup-status.txt)\"\n"
        "printf '@ runs=%s\\n' \"$(ls /w/logs/*.log | wc -l)\"\n",
        root,
    )
    expect_pass(suite, result, "C6 lock case runs", "C6 lock case failed")
    suite.same(
        "C6 a held lock skips the run without publishing running",
        ["@ state_before=ok", "@ skipped_rc=3", "@ state_after=ok", "@ runs=2"],
        probes(result),
    )

    root = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "printf 'state=ok\\nupdated_at=1\\nlast_success=7\\nmessage=previous\\n' > /w/previous\n"
        "rm -f /w/data/backup-status.txt; mkdir /w/data/backup-status.txt\n"
        "rc=0; /restic.sh backup >/dev/null 2>&1 || rc=$?\n"
        "printf '@ dir_rc=%s\\n' \"$rc\"\n"
        "printf '@ still_dir=%s\\n' \"$(test -d /w/data/backup-status.txt && echo yes || echo no)\"\n"
        "printf '@ leftover=%s\\n' \"$(ls /w/data | grep -c 'backup-status.txt.tmp' || true)\"\n",
        root,
    )
    expect_pass(suite, result, "C6 unusable status path case runs", "C6 unusable status path case failed")
    suite.same(
        "C6 an unusable status path fails closed and cleans its temp file",
        ["@ dir_rc=2", "@ still_dir=yes", "@ leftover=0"],
        probes(result),
    )

    # A stale `running` marker and an unknown state may not block the next run:
    # the lock decides concurrency, the marker only reports.
    for label, previous in (
        ("a stale running marker", "state=running\nupdated_at=1\nlast_success=7\nmessage=stale\n"),
        ("an unknown state", "garbage\nstate=???\n"),
        ("an oversized marker", "state=ok\n" + "x" * 5000 + "\n"),
    ):
        root = workspace()
        result = run(
            image,
            "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
            f"printf '%s' '{previous.replace(chr(10), chr(92) + 'n')}' > /w/data/backup-status.txt\n"
            "rc=0; /restic.sh backup >/dev/null 2>&1 || rc=$?\n"
            "printf '@ rc=%s\\n' \"$rc\"\n"
            "printf '@ state=%s\\n' \"$(sed -n 's/^state=//p' /w/data/backup-status.txt)\"\n",
            root,
        )
        expect_pass(suite, result, f"C6 {label} case runs", f"C6 {label} case failed")
        suite.same(f"C6 {label} does not block the run", ["@ rc=0", "@ state=ok"], probes(result))

    # A symlinked marker must be replaced by a real file (never written through
    # the link), so the next atomic rename cannot land outside the volume.
    root = workspace()
    result = run(
        image,
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "printf 'state=ok\\nlast_success=7\\n' > /w/real-status\n"
        "ln -s /w/real-status /w/data/backup-status.txt\n"
        "rc=0; /restic.sh backup >/dev/null 2>&1 || rc=$?\n"
        "printf '@ rc=%s\\n' \"$rc\"\n"
        "printf '@ regular=%s\\n' \"$(test -f /w/data/backup-status.txt && ! test -L /w/data/backup-status.txt && echo yes || echo no)\"\n"
        "printf '@ state=%s\\n' \"$(sed -n 's/^state=//p' /w/data/backup-status.txt)\"\n",
        root,
    )
    expect_pass(suite, result, "C6 symlinked status case runs", "C6 symlinked status case failed")
    suite.same(
        "C6 a symlinked marker is replaced by a real file",
        ["@ rc=0", "@ regular=yes", "@ state=ok"],
        probes(result),
    )

    # Staleness is derived from the recorded success time, so the clock is
    # pinned instead of racing a real 26h boundary. The mailer is a stub: the
    # alert path is what is being asserted, not msmtp.
    def stale_case(last_success: str, hours: str | None = None, source: Path | None = None) -> Case:
        root = workspace()
        env = {"FIXED_NOW": "1790000000", "SMTP_TO": "admin@example"}
        if hours is not None:
            env["RESTIC_STALE_HOURS"] = hours
        return run(
            image,
            "mkdir -p /w/bin\n"
            "printf '%s\\n' '#!/bin/bash'"
            " 'if [ \"$1\" = -u ] && [ \"$2\" = +%s ]; then echo \"$FIXED_NOW\"; exit 0; fi'"
            " 'exec /usr/bin/date \"$@\"' > /w/bin/date\n"
            "printf '%s\\n' '#!/bin/bash' 'echo \"$*\" >> /w/mail.calls' > /w/bin/mail\n"
            "printf '%s\\n' '#!/bin/bash' 'exit 0' > /w/bin/restic\n"
            "chmod +x /w/bin/date /w/bin/mail /w/bin/restic\n"
            f"printf 'state=ok\\nupdated_at=1\\nlast_success={last_success}\\nmessage=old\\n'"
            " > /w/data/backup-status.txt\n"
            "rc=0; PATH=\"/w/bin:$PATH\" /restic.sh snapshots 2>/w/stderr || rc=$?\n"
            "cat /w/stderr >&2\n"
            "printf '@ rc=%s\\n' \"$rc\"\n"
            "printf '@ warn=%s\\n' \"$(grep -c 'older than' /w/stderr || true)\"\n"
            "mails=0; [ ! -f /w/mail.calls ] || mails=\"$(grep -c 'Backup is stale' /w/mail.calls)\"\n"
            "printf '@ stale_mails=%s\\n' \"$mails\"\n",
            root,
            env=env,
            source=source,
        )

    now = 1790000000
    # 30h old with the default 26h window: the run warns and mails.
    result = stale_case(str(now - 30 * 3600))
    expect_pass(suite, result, "C6 a stale backup case runs", "C6 stale backup case failed")
    suite.same(
        "C6 a stale backup warns and mails",
        ["@ rc=0", "@ warn=1", "@ stale_mails=1"],
        probes(result),
    )

    # Exactly at the threshold is stale; one second below is not.
    result = stale_case(str(now - 26 * 3600))
    expect_pass(suite, result, "C6 exact threshold case runs", "C6 exact threshold case failed")
    suite.same(
        "C6 exactly the window still warns",
        ["@ rc=0", "@ warn=1", "@ stale_mails=1"],
        probes(result),
    )

    result = stale_case(str(now - (26 * 3600 - 1)))
    expect_pass(suite, result, "C6 just inside the window runs", "C6 just inside the window failed")
    suite.same(
        "C6 one second under the window stays quiet",
        ["@ rc=0", "@ warn=0", "@ stale_mails=0"],
        probes(result),
    )

    # A first ever run has no history to age, so it is never stale.
    result = stale_case("0")
    expect_pass(suite, result, "C6 no-history case runs", "C6 no-history case failed")
    suite.same(
        "C6 an absent history does not warn",
        ["@ rc=0", "@ warn=0", "@ stale_mails=0"],
        probes(result),
    )

    for value in ("0", "1.5", "8761"):
        result = stale_case(str(now - 30 * 3600), value)
        expect_pass(suite, result, f"C6 RESTIC_STALE_HOURS={value} rejected",
                    f"C6 RESTIC_STALE_HOURS={value} accepted")
        suite.same(f"C6 RESTIC_STALE_HOURS={value} fails with 2", "@ rc=2", probes(result)[0])
        suite.same(f"C6 RESTIC_STALE_HOURS={value} names itself", True,
                   "RESTIC_STALE_HOURS must be" in result.output)

    # Removing the call in `main` is the whole guard: the state is still read,
    # the run still succeeds, only the alert disappears.
    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        unwatched = mutate(
            RESTIC,
            (('    [ "$command" = check-freshness ] || stale_backup\n', "    :\n"),),
            Path(tmp),
        )
        control = stale_case(str(now - 30 * 3600), source=unwatched)
        expect_pass(suite, control, "C6 stale mutation control runs", "C6 stale mutation control failed")
        suite.same(
            "C6 mutation: without the call a stale backup is silent",
            ["@ rc=0", "@ warn=0", "@ stale_mails=0"],
            probes(control),
        )

    # The advisory only warns, so a scheduler that died with the app never
    # surfaces. `check-freshness` is the scheduled gate that fails instead.
    def freshness_case(
        last_success: str, hours: str | None = None, source: Path | None = None
    ) -> Case:
        root = workspace()
        env = {"FIXED_NOW": "1790000000", "SMTP_TO": "admin@example"}
        if hours is not None:
            env["RESTIC_STALE_HOURS"] = hours
        return run(
            image,
            "mkdir -p /w/bin\n"
            "printf '%s\\n' '#!/bin/bash'"
            " 'if [ \"$1\" = -u ] && [ \"$2\" = +%s ]; then echo \"$FIXED_NOW\"; exit 0; fi'"
            " 'exec /usr/bin/date \"$@\"' > /w/bin/date\n"
            "printf '%s\\n' '#!/bin/bash' 'echo \"$*\" >> /w/mail.calls' > /w/bin/mail\n"
            "chmod +x /w/bin/date /w/bin/mail\n"
            f"printf 'state=ok\\nupdated_at=1\\nlast_success={last_success}\\nmessage=old\\n'"
            " > /w/data/backup-status.txt\n"
            "rc=0; PATH=\"/w/bin:$PATH\" /restic.sh check-freshness 2>/w/stderr || rc=$?\n"
            "cat /w/stderr >&2\n"
            "printf '@ rc=%s\\n' \"$rc\"\n"
            "printf '@ state=%s\\n' \"$(sed -n 's/^state=//p' /w/data/backup-status.txt)\"\n"
            "mails=0; [ ! -f /w/mail.calls ] || mails=\"$(grep -c 'Backup is stale' /w/mail.calls)\"\n"
            "printf '@ stale_mails=%s\\n' \"$mails\"\n",
            root,
            env=env,
            source=source,
        )

    # Past the window the gate fails, mails once and records the failure.
    result = freshness_case(str(now - 30 * 3600))
    expect_pass(suite, result, "C6 freshness gate case runs", "C6 freshness gate case failed")
    suite.same(
        "C6 a stale backup fails the scheduled gate",
        ["@ rc=2", "@ state=failed", "@ stale_mails=1"],
        probes(result),
    )

    # Exactly at the window is stale; one second inside is not, and neither
    # walks the marker forward.
    result = freshness_case(str(now - 26 * 3600))
    expect_pass(suite, result, "C6 freshness threshold case runs", "C6 freshness threshold case failed")
    suite.same(
        "C6 exactly the window fails the gate without touching the state",
        ["@ rc=2", "@ state=failed", "@ stale_mails=1"],
        probes(result),
    )

    result = freshness_case(str(now - (26 * 3600 - 1)))
    expect_pass(suite, result, "C6 freshness inside-window case runs", "C6 freshness inside-window case failed")
    suite.same(
        "C6 one second under the window passes quietly",
        ["@ rc=0", "@ state=ok", "@ stale_mails=0"],
        probes(result),
    )

    result = freshness_case("0")
    expect_pass(suite, result, "C6 freshness no-history case runs", "C6 freshness no-history case failed")
    suite.same(
        "C6 a first run is not a failure",
        ["@ rc=0", "@ state=ok", "@ stale_mails=0"],
        probes(result),
    )

    # A clock behind the marker must not report an enormous negative age.
    result = freshness_case(str(now + 3600))
    expect_pass(suite, result, "C6 freshness future-stamp case runs", "C6 freshness future-stamp case failed")
    suite.same(
        "C6 a backwards clock stays quiet",
        ["@ rc=0", "@ state=ok", "@ stale_mails=0"],
        probes(result),
    )

    # Mutation control: the gate must depend on the comparison it just made.
    # Making the inside-window branch unreachable turns a fresh backup red.
    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        loose = mutate(
            RESTIC,
            ((
                "    if [ \"$age\" -lt \"$limit\" ]; then\n"
                "        echo \"Last successful backup was ${age}s ago (limit ${limit}s)\"\n"
                "        return 0\n"
                "    fi\n",
                "    if false; then\n"
                "        echo \"Last successful backup was ${age}s ago (limit ${limit}s)\"\n"
                "        return 0\n"
                "    fi\n",
            ),),
            Path(tmp),
        )
        control = freshness_case(str(now - (26 * 3600 - 1)), source=loose)
        expect_pass(suite, control, "C6 freshness mutation control runs", "C6 freshness mutation control failed")
        suite.same(
            "C6 mutation: without the age comparison a fresh backup fails the gate",
            ["@ rc=2", "@ state=failed", "@ stale_mails=1"],
            probes(control),
        )

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        unlocked = mutate(RESTIC, (("    if ! flock -n 9; then\n", "    if false; then\n"),), Path(tmp))
        control_root = workspace()
        control = run(
            image,
            "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
            "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
            "exec 9>/w/logs/backup.lock; flock 9\n"
            "rc=0; /restic.sh backup >/dev/null 2>&1 || rc=$?\n"
            "printf '@ rc=%s\\n' \"$rc\"\n"
            "printf '@ runs=%s\\n' \"$(ls /w/logs/*.log | wc -l)\"\n",
            control_root,
            source=unlocked,
        )
        expect_pass(suite, control, "C6 mutation control still runs", "C6 mutation control failed")
        suite.same(
            "C6 mutation: without the lock the held run no longer skips",
            ["@ rc=0", "@ runs=2"],
            probes(control),
        )

    # Atomicity: only a rename can publish a marker, so a failing `mv` must
    # leave the previous status intact and fail the run.
    atomic_case = (
        "mkdir -p /w/bin\n"
        "printf '%s\\n' '#!/bin/bash' 'case \"$*\" in'"
        " '  *backup-status.txt) exit 1 ;;' 'esac' 'exec /usr/bin/mv \"$@\"' > /w/bin/mv\n"
        "chmod +x /w/bin/mv\n"
        "sqlite3 /w/data/kuma.db 'create table t(v)'\n"
        "/restic.sh backup >/dev/null 2>&1 || exit 1\n"
        "printf 'state=ok\\nupdated_at=1\\nlast_success=7\\nmessage=previous\\n' > /w/data/backup-status.txt\n"
        "rc=0; PATH=\"/w/bin:$PATH\" /restic.sh backup >/dev/null 2>&1 || rc=$?\n"
        "printf '@ rc=%s\\n' \"$rc\"\n"
        "printf '@ message=%s\\n' \"$(sed -n 's/^message=//p' /w/data/backup-status.txt)\"\n"
    )
    guarded = run(image, atomic_case, workspace())
    expect_pass(suite, guarded, "C6 failed rename case runs", "C6 failed rename case failed")
    suite.same(
        "C6 a failing status rename keeps the previous marker",
        ["@ rc=2", "@ message=previous"],
        probes(guarded),
    )

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        in_place = mutate(
            RESTIC,
            (
                (
                    'if ! [ -d "$STATUS_FILE" ] && printf \'%s\' "$body" > "$tmp" && mv "$tmp" "$STATUS_FILE"; then',
                    'if printf \'%s\' "$body" > "$STATUS_FILE"; then',
                ),
            ),
            Path(tmp),
        )
        control = run(image, atomic_case, workspace(), source=in_place)
        expect_pass(suite, control, "C6 atomic mutation control runs", "C6 atomic mutation control failed")
        suite.same(
            "C6 mutation: an in-place status write survives a missing rename",
            ["@ rc=0", "@ message=Backup complete"],
            probes(control),
        )


def check_process_supervision(image: str) -> None:
    """C6: crashed proxy or scheduler returns, application exit ends the run."""
    container = stack(image)
    try:
        for process in ("caddy", "supercronic", "node"):
            if wait_for(container, process) is None:
                suite.bad(f"C6 {process} never started in the process table")
                print(docker("logs", container, check=False).stdout, file=sys.stderr)
                return
        for process in ("caddy", "supercronic"):
            before = pids(container, process)
            if len(before) != 1:
                suite.bad(f"C6 expected one {process} before the restart probe, got {before}")
                print(docker("logs", container, check=False).stdout, file=sys.stderr)
                return
            docker("exec", container, "kill", "-9", str(before[0]))
            after = replacement(container, process, before[0])
            if after is None:
                suite.bad(f"C6 {process} was not restarted after a forced exit")
                print(docker("logs", container, check=False).stdout, file=sys.stderr)
                return
            suite.ok(f"C6 {process} restarted with a new pid {before[0]}->{after}")

        # The supervisor logs to stderr; Docker keeps the two streams apart.
        result = docker("logs", container, check=False)
        log = result.stdout + result.stderr
        for label in ("caddy", "crontab"):
            if f"[{label}] exited with status 137, restarting in 0.2s" not in log:
                suite.bad(f"C6 the process table did not log the {label} restart")
                print(log, file=sys.stderr)
                return
        suite.ok("C6 both restarts are recorded with the configured delay")

        # The proxy must actually serve again, not merely exist.
        for _ in range(100):
            result = docker("exec", container, "curl", "-s", "-o", "/dev/null",
                            "http://127.0.0.1:8080/health", check=False)
            if result.returncode == 0:
                break
            time.sleep(0.1)
        else:
            suite.bad("C6 the proxy did not recover after its forced restart")
            return
        suite.ok("C6 the proxy answers /health again after the restart")

        # Application exit is authoritative: the container must stop and take
        # the restartable services with it.
        app = pids(container, "node")
        if not app:
            suite.bad("C6 the application process was not found")
            return
        docker("exec", container, "kill", "-TERM", str(app[0]))
        for _ in range(200):
            if docker("inspect", "--format", "{{.State.Running}}", container,
                      check=False).stdout.strip() == "false":
                break
            time.sleep(0.05)
        else:
            suite.bad("C6 the container kept running after the application exited")
            return
        suite.ok("C6 application exit stops the container")
    finally:
        stop(container)

    # An unprivileged table is required: /restic.sh runs as node and writes
    # root-owned paths, so root must never reach this script directly.
    result = docker("run", "--rm", "--entrypoint", "/services.sh", image, check=False)
    if result.returncode != 2 or "must run as the unprivileged" not in result.stderr:
        suite.bad("C6 the process table must refuse to start as root")
        print(result.stdout + result.stderr, file=sys.stderr)
        return
    suite.ok("C6 the process table refuses to start as root")

    # A zero or malformed delay must fail before any service starts.
    for delay in ("0", "0.0", "abc", "-1"):
        result = docker("run", "--rm", "--env", f"SUPERVISE_RESTART_DELAY={delay}",
                        image, check=False)
        if result.returncode != 2 or "must be a positive number" not in result.stderr:
            suite.bad(f"C6 SUPERVISE_RESTART_DELAY={delay} did not fail before starting")
            print(result.stdout + result.stderr, file=sys.stderr)
            return
    suite.ok("C6 a zero or malformed restart delay fails closed")

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        restartless = mutate(
            "scripts/services.sh",
            (("supervise caddy true caddy run", "supervise caddy false caddy run"),),
            Path(tmp),
        )
        container = stack(image, source=restartless)
        try:
            if wait_for(container, "caddy") is None:
                suite.bad("C6 mutation control: caddy never started")
                return
            before = pids(container, "caddy")
            if len(before) != 1:
                suite.bad("C6 mutation control could not see caddy")
                return
            docker("exec", container, "kill", "-9", str(before[0]))
            for _ in range(200):
                running = docker("inspect", "--format", "{{.State.Running}}", container,
                                 check=False).stdout.strip()
                if running == "false":
                    break
                time.sleep(0.05)
            suite.same(
                "C6 mutation: without restart the table exits with caddy",
                "false",
                running,
            )
        finally:
            stop(container)

        # Only the app may be load-bearing: if it is marked restartable the
        # container survives a dead application and Fly never recovers it.
        (Path(tmp) / "appless").mkdir()
        appless = mutate(
            "scripts/services.sh",
            (("supervise uptime-kuma false", "supervise uptime-kuma true"),),
            Path(tmp) / "appless",
        )
        container = stack(image, source=appless)
        try:
            app = wait_for(container, "node")
            if app is None:
                suite.bad("C6 mutation appless: no application process")
                return
            docker("exec", container, "kill", "-TERM", str(app))
            time.sleep(1.5)
            running = docker("inspect", "--format", "{{.State.Running}}", container,
                             check=False).stdout.strip()
            suite.same(
                "C6 mutation: a restartable application keeps the container alive",
                "true",
                running,
            )
        finally:
            stop(container)


def check_invalid_restart_delay(image: str) -> None:
    """Kept separate so C6 also runs standalone under SMOKE_ONLY."""
    for delay in ("0", "abc"):
        result = docker("run", "--rm", "--env", f"SUPERVISE_RESTART_DELAY={delay}",
                        image, check=False)
        if result.returncode != 2 or "must be a positive number" not in result.stderr:
            suite.bad(f"SUPERVISE_RESTART_DELAY={delay} did not fail before services start")
            print(result.stdout + result.stderr, file=sys.stderr)
            return
    suite.ok("an invalid restart delay fails closed before services start")


ADDRESS_GOOD = (
    ("CADDY_DOMAINS", "status.example"),
    ("CADDY_DOMAINS", "a.example b.example"),
    ("CADDY_DOMAINS", "version.127.example"),
    ("CADDY_DOMAINS", "STATUS.Example"),
    ("RESTIC_REPOSITORY", "/w/repo"),
    ("RESTIC_REPOSITORY", "s3://s3.example/r2-bucket/uptime-kuma"),
    ("RESTIC_REPOSITORY", "s3://s3.example:9000/r2-bucket"),
    ("HEALTHCHECK_URL", "https://status.example/api/push/abc"),
    # The port guard has both ends of its legal range as boundary cases.
    ("SMTP_PORT", "1"),
    ("SMTP_PORT", "587"),
    ("SMTP_PORT", "65535"),
)

ADDRESS_BAD = (
    ("CADDY_DOMAINS", "127.0.0.1"),
    ("CADDY_DOMAINS", "127.1"),
    ("CADDY_DOMAINS", "0x7f.0.0.1"),
    ("CADDY_DOMAINS", "[::1]"),
    ("CADDY_DOMAINS", "status.example status.example"),
    ("CADDY_DOMAINS", "status.example:8443"),
    ("RESTIC_REPOSITORY", "s3://s3.example"),
    ("RESTIC_REPOSITORY", "s3://s3.example//bucket"),
    ("RESTIC_REPOSITORY", "s3://user@pass@s3.example/bucket"),
    ("RESTIC_REPOSITORY", "repo/relative"),
    ("HEALTHCHECK_URL", "http://status.example"),
    ("HEALTHCHECK_URL", "https://status.example/" + "a" * 2048),
    ("SMTP_PORT", "0"),
    ("SMTP_PORT", "65536"),
)


def check_address_audit(image: str) -> None:
    """C7: one DNS rule and one URL rule, enforced before the app starts."""
    audit = (
        ". /entrypoint/config.sh\n"
        "out=$(audit_addresses && validate_smtp 2>&1); rc=$?\n"
        "printf '@ rc=%s\\n' \"$rc\"\n"
        "[ \"$rc\" -eq 0 ] || printf '@ err=%s\\n' \"$out\"\n"
    )
    for label, cases in (("accepted", ADDRESS_GOOD), ("rejected", ADDRESS_BAD)):
        for name, value in cases:
            expected = "@ rc=0" if label == "accepted" else "@ rc=2"
            result = case(image, audit, env={name: value})
            expect_pass(suite, result, f"C7 {name}={value} runs", f"C7 {name}={value} case failed")
            suite.same(f"C7 {label} {name}={value}", expected, probes(result)[0] if probes(result) else "")

    result = case(image, audit, env={"SMTP_HOST": "smtp.example", "SMTP_PASSWORD": "line1\nline2"})
    expect_pass(suite, result, "C7 multiline SMTP case runs", "C7 multiline SMTP case failed")
    suite.same(
        "C7 a newline in an SMTP value is rejected",
        "@ rc=2",
        probes(result)[0] if probes(result) else "",
    )

    # `initCfg` always emits a `user` and a `password` line, so half a pair
    # must be refused instead of silently authenticating with an empty field.
    for label, env in (
        ("C7 a username without a password is rejected", {"SMTP_USERNAME": "user"}),
        ("C7 a password without a username is rejected", {"SMTP_PASSWORD": "secret"}),
    ):
        result = case(image, audit, env=env)
        expect_pass(suite, result, f"{label} case runs", f"{label} case failed")
        suite.same(label, "@ rc=2", probes(result)[0] if probes(result) else "")

    paired = case(
        image, audit, env={"SMTP_HOST": "smtp.example", "SMTP_USERNAME": "user", "SMTP_PASSWORD": "secret"}
    )
    expect_pass(suite, paired, "C7 a complete credential pair case runs", "C7 credential pair case failed")
    suite.same("C7 a complete credential pair is accepted", "@ rc=0", probes(paired)[0] if probes(paired) else "")

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        unpaired = mutate(
            "scripts/entrypoint/config.sh",
            (('    if [ -n "${SMTP_USERNAME:-}" ] && [ -z "${SMTP_PASSWORD:-}" ]; then\n', "    if false; then\n"),),
            Path(tmp),
        )
        control = case(image, audit, env={"SMTP_USERNAME": "user"}, source=unpaired)
        expect_pass(suite, control, "C7 pairing mutation runs", "C7 pairing mutation failed")
        suite.same(
            "C7 mutation: without the pairing guard a lone username is accepted",
            "@ rc=0",
            probes(control)[0],
        )

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        numeric = mutate(
            "scripts/entrypoint/config.sh",
            (("        && ! is_numeric_host \"$name\" \\\n", "        \\\n"),),
            Path(tmp),
        )
        control = case(image, audit, env={"CADDY_DOMAINS": "127.0.0.1"}, source=numeric)
        expect_pass(suite, control, "C7 mutation control runs", "C7 mutation control failed")
        suite.same("C7 mutation: without the numeric guard an IP is accepted", "@ rc=0", probes(control)[0])

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        empty_segment = mutate(
            "scripts/entrypoint/config.sh",
            (("        /* | *//* | *'/../'*", "        *//* | *'/../'*"),),
            Path(tmp),
        )
        control = case(
            image,
            audit,
            env={"RESTIC_REPOSITORY": "s3://s3.example//bucket"},
            source=empty_segment,
        )
        expect_pass(suite, control, "C7 empty-segment mutation runs", "C7 empty-segment mutation failed")
        suite.same(
            "C7 mutation: without the empty-segment guard `//` is accepted",
            "@ rc=0",
            probes(control)[0],
        )

    with tempfile.TemporaryDirectory(prefix="runtime-mutate-") as tmp:
        port = mutate(
            "scripts/entrypoint/config.sh",
            (('if [[ ! "$SMTP_PORT" =~ ^[1-9][0-9]{0,4}$ ]] || [ "$SMTP_PORT" -gt 65535 ]; then',
              'if [[ ! "$SMTP_PORT" =~ ^[1-9][0-9]{0,4}$ ]]; then'),),
            Path(tmp),
        )
        control = case(image, audit, env={"SMTP_PORT": "65536"}, source=port)
        expect_pass(suite, control, "C7 port-bound mutation runs", "C7 port-bound mutation failed")
        suite.same(
            "C7 mutation: without the upper bound port 65536 is accepted",
            "@ rc=0",
            probes(control)[0],
        )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: tests/runtime_smoke.py <image>")
    image = sys.argv[1]
    checks = [
        check_sqlite_consistency,
        check_client_ip,
        check_host_policy,
        check_restore_isolation,
        check_bounded_diagnostics,
        check_lock_and_state,
        check_process_supervision,
        check_invalid_restart_delay,
        check_address_audit,
    ]
    only = os.environ.get("SMOKE_ONLY", "").split()
    try:
        for check in checks:
            if not only or check.__name__ in only:
                check(image)
    finally:
        for name in list(containers):
            stop(name)
        for handle in handles:
            cleanup(handle, image)
    suite.report()


if __name__ == "__main__":
    main()
