# Recurring Engineering Decisions

This file records defects fixed more than once in this repository, especially
where a later simplification removed the fix. Every entry is a permanent
invariant: a change that weakens one needs a failing regression test and an
explicit replacement design first.

Five classes are regressions: `C1` (host gate), `C2` (client IP), `C3` (SQLite
hot copy), `C5` (bounded output), and `C9` (simplification). Three were never
established: `C4` (restore isolation), `C6` (lock/state/supervision), `C7`
(input audit). `C8` was partial: a mooted pin, no validation, no ignore file.
The recurring pattern is not only missing code; it is removing an existing
invariant while optimizing line count. Commit identifiers below are evidence,
not exceptions to the rules.

Audit baseline: all 97 commits reachable through `a72413e` on 2026-10-30,
extending the previous 96-commit baseline at `0a3b4a7`. Every later round must
extend that audit to every commit reachable at its start.

## Target-Mode Prompt

Every new, resumed, review, optimization, scoring, release, and post-merge round
starts with this gate. The procedure itself lives in
`docs/target_mode.md`, and `python3.13 scripts/round_open.py` prints the skeleton
so no round opens from memory. Build one evidence row per class below containing
the history classification, authoritative guard, positive, boundary and
negative evidence, a named mutation control, and the observed result. Run the
named mutation control and prove that removing the guard changes the outcome.
If a row is failing, unverified, or shows a guard weakened by simplification,
repair the invariant in the same round before other work. Do not optimize,
score, merge, deploy, or claim completion while any row lacks evidence or has an
unclassified residual. Record the matrix in the PR or final report without
creating per-round files. The matrix is a review artifact, not a score: a
passing row requires observed command or test output.

## Every-Round Goal Gate

Before any other audit, optimization, scoring, or completion work in a round,
scan every recorded failure class below against the current implementation and
run its regression gate. For each class, trace reachable history with
`git log -S` and `git log -G`, then classify it as never fixed, regressed after
simplification, or partially fixed, and identify the one authoritative
implementation.

- If history shows a regression or a weakened invariant, restore that invariant
  in the round's first patch before unrelated optimization. Do not combine that
  patch with line-count reduction.
- Check every execution path that reaches the invariant, not only the path named
  in a previous fix. A guard on preflight, one command, or one caller is partial
  until all equivalent paths enforce it.
- Every class on every round must have positive, boundary, negative, and
  mutation-control tests. Run the named mutation control and verify that it
  fails when the authoritative guard is removed.
- Update the affected History, Final decision, and Regression gate when
  evidence changes.
- No score or completion claim is valid while any recorded gate is missing,
  failing, or unverified. An accepted residual stays explicitly open and never
  counts as passing.

## Host Policy And Health Escape

History: `5471baa` introduced `@outside_domain` plus a Caddy-answered `/health`,
`7856ff0` repaired the empty-value crash, `2552bc6` switched to
`@allowed`/`host(...localhost)`, `0ea83e3` switched to `@denied` plus `respond`,
and `a839ef2` rewrote it again while deleting `/health`. `3ec25bd` then pointed
the Fly probe at a path that needs the application.

Final decision: exactly one host matcher exists, in one ordered `route` chain
that also carries the health escape. An empty or blank `CADDY_DOMAINS` is
unset so the same matcher evaluates to allow-all; a non-empty value matches
each listed bare DNS name exactly; everything else answers 403 with the full
security header set. `/health` is answered by Caddy and never reaches the
application, so the Fly probe survives a dead app and a denied host.

Authority: `config/Caddyfile:@health`, `config/Caddyfile:@denied`, and
`scripts/entrypoint/config.sh:normalize_caddy_domains`.

Mutation control: `tests/runtime_smoke.py:check_host_policy` (health moved after
the gate, headers moved into the backend, `unset` removed).

Regression gate: unset, empty, and blank allowlists allow every host; a single
name, a space-separated list, upper case, and a denied host behave exactly;
denied responses keep every security header; the probe answers 200 with headers
for a denied host; a trailing dot in configuration is rejected; an empty value
without the `unset` stops Caddy adapting.

## Client IP Attribution

History: `5471baa` used `CF-Connecting-IP`, then `Fly-Client-IP`, then
`remote_host`. `0ea83e3` and `a839ef2` reduced it to `Fly-Client-IP` only, so a
Cloudflare request degraded to the edge address; `fd86130` restored the chain.

Final decision: one ordered fallback, `CF-Connecting-IP`, then `Fly-Client-IP`,
then the remote peer. An empty value falls through instead of being forwarded.
Headers are only read from trusted peers, so a client-supplied copy is not
believed. `X-Real-IP` and `X-Forwarded-For` carry the same single-hop value.

Authority: `config/Caddyfile:client_ip_headers` and `config/Caddyfile:header_up`.

Mutation control: `tests/runtime_smoke.py:check_client_ip` (Cloudflare hop
removed).

Regression gate: all three sources present, only the Fly header, only the remote
peer, all three empty, and a forged client header are covered, plus the
single-hop assertion on both forwarded headers.

## Consistent SQLite Backup

History: `0066949` and `9972497` used `sqlite3 .backup`, `PRAGMA
integrity_check`, and excluded the live files. `5471baa` deleted the copy and
shipped the live `kuma.db` with its sidecars; `e4fc3e5` restored the checked copy.

Final decision: the backup takes a `.backup` snapshot, proves the copy with a
single `ok` from `integrity_check`, and only then runs the backend. The live
database and its `-journal`/`-wal`/`-shm` sidecars are excluded. Any failure
aborts before the backend and records no success time; the temporary copy is
removed on success and on failure.

Authority: `scripts/restic.sh:sqlite_snapshot` and
`scripts/restic.sh:sqlite_check`.

Mutation control: `tests/runtime_smoke.py:check_sqlite_consistency` (live files
backed up directly).

Regression gate: a healthy database yields an `ok` check, a corrupted one aborts
without calling the backend, the snapshot contains no live file, and the
temporary copy never survives either outcome.

## Restore Isolation And Confirmation

History: never established before this round; `restore` always overwrote
`/app/data` in place with no drill and no confirmation.

Final decision: the drill extracts into an isolated scratch directory and
validates SQLite there, touching no production file. In-place restore requires
`RESTORE_CONFIRM=yes`, refuses a symlinked or non-regular live database, stages
through `.restore`, and copies the current database to `kuma.db.previous` with
`.backup` before the swap, so a restorable previous state survives it. Stale
sidecars are dropped only at the swap, and a failed stage never touches the
live file.

Authority: `scripts/restic.sh:restore_test` and `scripts/restic.sh:restore`.

Mutation control: `tests/runtime_smoke.py:check_restore_isolation` (drill
retargeted at the live data directory).

Regression gate: isolated drill, missing confirmation refused, symlink refused,
non-regular file refused, copy failure leaves the live database intact, and the
rollback copy holds the pre-restore state after a successful swap.

## Bounded Diagnostics

History: `1a9d3bb` used `tee -a` with no cap. `5471baa` and `a839ef2` rewrote
`log()`/`run()` between the crontab and the script while capture stayed
unbounded in shell memory; `e4fc3e5` restored trimming only before a backup, so
a failure after backend work was downgraded to a warning and still recorded as
success.

Final decision: output is bounded at capture time with `tail -c`, bounded again
before it is persisted, and the log file is replaced atomically inside its byte
cap. The per-file and per-directory caps are independent safeguards checked
before work and again after backend work. Capture, trim, and write failures
abort and record a failure instead of producing a false success.

Authority: `scripts/restic.sh:trim_logs`, `scripts/restic.sh:write_log_entry`,
and `scripts/restic.sh:log`.

Mutation control: `tests/runtime_smoke.py:check_bounded_diagnostics` (persist-time
truncation removed, capture-failure guard removed, write-failure guard removed,
then the post-run guard disabled). A broken `tail` only fails the first stdin
capture, so removing that guard is what turns the same run from exit 2 into a
false `ok`; the write control fails only `mktemp` and proves the same downgrade
for persistence.

Regression gate: oversized output, a failing `tail`, an unmaintainable directory
before work (backend never called), an unmaintainable directory after work, and
a failing log write all fail closed inside their cap.

## State, Lock And Process Supervision

History: never established before this round. `restic unlock ... || true` hid a
stale lock, `@hourly` could overlap itself, and `overmind` restarted a dead
application while leaving a dead proxy dead. Nothing watched the age of
`last_success` either: a schedule that stopped firing, a rotated marker, or a
clock jump left a green status file next to a backup that was days old.

Final decision: one `flock` decides concurrency and only the holder publishes
`running`; a skipped run exits 3 without touching the marker. Missing,
unreadable, oversized, non-regular, stale-running, and in-flight states stay
distinct and none of them blocks the next run. Status writes rename a sibling
temp file, so a failure keeps the previous marker. One supervisor owns the
process table: Caddy and the scheduler restart after a validated positive
delay, and an application exit stops the container so Fly recovers the machine.
A missing `flock`, a rotated marker, or a clock jump must not silently age the
last success: every run derives the age from `last_success` and warns plus mails
once the age reaches `RESTIC_STALE_HOURS` (default 26, bounded to 8760). The age
is derived state rather than another marker, so one success clears it, a first
run with no history stays quiet, and a backwards clock never warns.

Authority: `scripts/restic.sh:write_status` and `scripts/services.sh:supervise`.
Staleness lives in `scripts/restic.sh:stale_backup`, called once from `main`
after `load_status` and `initCfg` (so the mailer is configured); its window is
bounded by `require_bounded_int`.

The advisory is only reached when something else runs, so a scheduler that
died with the application stayed silent by construction. The schedule itself is
therefore part of the guard: `scripts/restic.sh:check_freshness` re-runs the
same comparison as its own job at `:55` every hour and fails closed instead of
warning, while `config/crontab` keeps `backup` (`:25`), `check-freshness` and
the monthly `restore-test` as three separate duties, each off minute zero.
`check_freshness` never stamps `last_success`, so a passing check cannot launder
a stale backup.

Mutation control: `tests/runtime_smoke.py:check_lock_and_state` (in-place status
write, stale call removed, freshness comparison disabled) and
`tests/runtime_smoke.py:check_process_supervision` (restartable application,
non-restartable proxy); `tests/test_recurring_gate.py:test_restic_schedule_covers_backup_freshness_and_drill`
collapses the schedule, moves a job to minute zero, and downgrades the drill.

Regression gate: a held lock skips, an unusable status path fails closed, a stale
running marker, an unknown state, an oversized marker, and a symlinked marker all
recover, a failed rename keeps the old marker, a forced Caddy or scheduler exit
returns with a new pid and a logged backoff, the proxy serves again, an
application exit stops the container, and a zero or malformed delay fails closed
before any service starts. The stale window has its own cases: 30h warns and
mails, exactly 26h warns, one second under stays quiet, no history stays quiet,
and `0`, a fractional value, and an over-limit value fail with exit 2 naming the
variable. Removing the call leaves a stale backup silent while the run still
succeeds. The gate adds its own cases: 30h and exactly 26h both exit 2 with
`state=failed` and one mail, one second under passes, a first run passes, and a
future stamp stays quiet; disabling the comparison turns the fresh case red.
The crontab must keep exactly the three duties, both hourly jobs off minute
zero, and the drill pinned to day 1.

## Address And Secret Input Audit

History: never established before this round. `CADDY_DOMAINS`,
`RESTIC_REPOSITORY`, `HEALTHCHECK_URL`, and the `SMTP_*` values were spliced
into Caddy, restic, and `msmtprc` unvalidated, so a newline injected a config
line and an IP-shaped host silently never matched.

Final decision: one DNS host rule and one HTTPS URL rule are shared by every
call site, with an absolute path or unambiguous `s3://` rule for the restic
target. SMTP values may not span lines. A username requires a password and a
password a username. `SMTP_PORT` is a whole number in 1-65535 with both ends of
the range as boundary cases; the guard is an explicit `if` because
`test && test || fail` is not if-then-else. All of it runs at start-up and fails
closed.

Authority: `scripts/entrypoint/config.sh:valid_dns_name`,
`scripts/entrypoint/config.sh:validate_https_url`, and
`scripts/entrypoint/config.sh:validate_smtp`.

Mutation control: `tests/runtime_smoke.py:check_address_audit` (numeric host
guard removed, `//` segment guard removed, SMTP pairing guard disabled, port
upper bound removed).

Regression gate: legal names, digit-bearing labels, IPv4 in dotted, short, and
hex form, bracketed IPv6, userinfo, an empty S3 segment, a relative path, and a
newline are all covered as accepted or rejected, as are a lone username, a lone
password, and a complete credential pair. Ports 1, 587, and 65535 are accepted;
0 and 65536 are rejected; dropping the upper bound makes 65536 pass.

## Supply Chain And Public Surface

History: `f11e477`, `35ad898`, `74565b0`, `9972497`, and `133ee5d` each swapped
a mutable base tag (`nightly2-rootless`, `2`, a beta) with no digest, and the
scheduler had no checksum. There was no validation workflow, no `.dockerignore`,
and `.env` was committed. Each gap is closed in this round.

Final decision: the base image is pinned by index digest and the downloaded
scheduler by per-architecture sha256 verified before use. Deployment names the
exact amd64 manifest digest produced by a build that passed `validate`;
`fly.toml` carries no image, and its `app` is the single recorded application
name that CI resolves and passes as `-a` to both the deploy and the verifier.
Every workflow action is pinned to a 40-character commit instead of a movable
tag, and keeps its release as a trailing comment so the pin stays reviewable.
Validation is a required check. Tracked files and reachable history carry no
real domain, bucket, token, or machine identifier.

Builds publish provenance attestations. They land in the same index as
`unknown/unknown` children, so the digest step filters on both architecture and
OS and requires exactly one match; the deploy image therefore stays a real
linux/amd64 image manifest, never an attestation.

The publish workflow also carries one weekly `schedule`, so a base-image or
OS-package fix can ship without a code change. Scheduled runs use `main` HEAD
and traverse the same validate -> build -> deploy chain.

Scheduled automation history: the weekly cron rebuilt unconditionally, so an
image that was already current still cost a full build and a deploy, and every
such deploy moved `latest`. Final decision: `rebuild_check` probes the published
`latest` image through `scripts/rebuild.sh:count` and the build job continues
only on a positive, well-formed upgrade count. A zero count skips the chain, so
the deploy stays on the last verified digest. Probe failure, a malformed count,
and an unresolvable `latest` fail closed instead of reading as "no changes".
Push, tag, and manual events are real changes and bypass the probe.
The probe is otherwise only reachable on Mondays, so `workflow_dispatch`
carries a `mode` choice: `force` (default) publishes unconditionally and `probe`
runs the scheduled decision, which keeps the skip path exercisable on demand.

Publication history: the metadata step used to write `latest` before the image
existed, so the mutable tag moved even when the digest smoke test, the deploy,
or the readiness verification failed; a cancelled run left it on an unverified
image. Final decision: the metadata step publishes only
`build-<run>-<attempt>`. Once the deployment verification passes, that exact
index is retagged as `latest` and re-read, and both the index digest and its
linux/amd64 child must still equal the recorded values. A failed, cancelled, or
unverified build therefore leaves `latest` on the last verified image.

Pruning cannot be left to a bare cron either: GitHub disables a scheduled
workflow after a long quiet period (observed state: `disabled_inactivity`), so
the package grows without bound while the job looks configured. Cleanup
therefore also runs on every `main` push, shares the publish concurrency group
so it queues instead of deleting a `build-<run>-<attempt>` tag mid-verify, and
keeps a window long enough for a stalled review plus `keep-n-tagged` as a floor.

The listener port is part of the same surface: the container runs Caddy as the
unprivileged `node` user, and a Fly machine refuses `bind :80` with EACCES while
local Docker Desktop allows it. Every local gate therefore stayed green and the
first real deploy timed out on a crash loop. The listener, `internal_port` and
the health-check port are one unprivileged value (8080).

The deploy trigger is surface too. `push` rebuilt and redeployed on every
documentation or test edit, so three consecutive baseline bumps each moved
`latest` and restarted production for no image change. Final decision: the
`main` push trigger ignores exactly the paths that cannot reach the image
(`.gitignore`, `README.md`, `LICENSE`, `.github/**`, `docs/**`, `tests/**`,
`scripts/rebuild.sh`, `scripts/round_open.py`, `scripts/deploy_drift.py`) and
those paths must also be excluded from the build context, so the two lists
cannot silently disagree. `Dockerfile`, `fly.toml`, `config/**`, the entrypoint
scripts and `scripts/restic.sh`/`scripts/services.sh` stay deployable. GitHub
does not path-filter tag pushes, and `schedule` and `workflow_dispatch` never
see the filter, so releases and the weekly rebuild still publish.

CI is the only supported release path, but nothing proved that production still
matched it: a hand `fly deploy` builds the Dockerfile and reaches the machine
with no digest gate, and a promotion that silently never ran looks identical
from the outside. Final decision: `.github/workflows/deploy-drift.yml` reads the
newest completed release, every machine image and the `latest` amd64 child after
every publish and daily, and fails on any difference. Every read is bounded,
and an unreadable document exits `2` rather than reading as "in sync"; the
detector is after the fact by design, because a hand deploy cannot be blocked.
It runs on `workflow_run` rather than the push, because a release and a machine
move separately and reading between them looks like a broken app; the first
`main` push proved it by cancelling the publish run before any job started.

Authority: `Dockerfile` digest/sha arguments,
`.github/workflows/validate.yml:validate`, and
`.github/workflows/ghcr.yaml:digest`, plus
`.github/workflows/ghcr.yaml:rebuild_check` with `scripts/rebuild.sh:count` and
`.github/workflows/ghcr.yaml:paths-ignore` with `.dockerignore`, and
`.github/workflows/deploy-drift.yml` with `scripts/deploy_drift.py:check`.

Mutation control: `tests/test_recurring_gate.py:test_privacy_mutation_detects_planted_identifier`,
`tests/test_recurring_gate.py:test_digest_mutation_allows_mutable_base` and
`tests/test_recurring_gate.py:test_latest_is_promoted_only_after_verification`;
`tests/test_recurring_gate.py:test_scheduled_rebuild_requires_package_drift`
executes the probe against fake `docker` output;
the deploy verifier carries its own control that deletes the digest comparison. The
port invariant is controlled inside
`tests/test_recurring_gate.py:test_listen_port_is_unprivileged_and_matches_fly`,
which rewrites the listener back to 80 and drifts `internal_port` to 8081.
A bare SHA is immutable but unreadable, so each pin also carries its release as
a trailing comment; the same test deletes one comment while keeping the SHA,
which only the readability rule can catch.
The release path is also the only one Fly answers 5xx on independently of the
commit (observed twice: `failed to create release (status 503)` while reads
stayed healthy), so
`tests/test_recurring_gate.py:test_deploy_retries_only_transient_errors` pins a
bounded, digest-idempotent retry with a growing delay, a transient-only filter
and a fail-closed last attempt.
The trigger filter has its own control set in
`tests/test_recurring_gate.py:test_docs_only_changes_skip_the_production_build`:
removing the filter, ignoring an input the image is built from, and dropping a
still-needed context entry each turn red.
The retry only covers the step it lives in, so the toolchain must not depend on
the same service: the setup action resolved `latest` through a Fly-hosted
manifest and died there with 503 before any retry ran (run `40959357266`).
`tests/test_recurring_gate.py:test_flyctl_is_pinned_and_not_fetched_through_fly`
therefore installs the version-pinned GitHub release asset, verifies its sha256
and asserts the action is gone; a control re-inserts it, and another drifts the
asset name away from the pinned version.

Regression gate: the digest check rejects a mutable image, a tampered binary
fails the build, the deploy job needs `build` which needs `validate`, the
published digest is the amd64 manifest, and the history scan stays clean. The
recorded app name matches the README and every `-a` in the deploy job, and
renaming it in one place, or documenting the deploy token for another app,
fails. The two verifier filters are executed
against fixture JSON: a stale release, an unfinished release, a stale machine
digest, a stopped machine, a critical, a missing or an unmatchable health check,
a bad host, and an undeployed app all reject, while the matching one passes.
Any non-SHA `uses:` reference fails with its `file:line`, and a single floating
tag is enough to reproduce it.
The digest filter requires both `amd64` and `linux` and an exactly-one result;
removing the filter makes an unfiltered index (image plus attestation children)
slip through the assertion.
Exactly one weekly cron entry is required with `* *` for day-of-month and month
and `1` for day-of-week; turning it into a monthly rebuild fails.
`latest` may not appear in the build metadata, promotion must follow the
verifier, and a re-read mismatch on either the index digest or the amd64 child
must fail; re-adding the metadata tag or dropping the comparison each turns red.
A scheduled run must probe the published image and skip the build on zero
upgrades; dropping the zero test or the numeric guard each turns red. Probe
output is counted only on `<name>/<suite> <version>` rows, so a clean image or a
`Listing...` banner alone never fabricates drift.
Cleanup must keep exactly one cron entry, keep the `main` push trigger, share
the publish concurrency group with `cancel-in-progress: false`, and hold an
`older-than` window of at least 7 days plus a `keep-n-tagged` floor. Dropping
the push trigger, giving cleanup its own group, or tightening the window to a
day each fails with its own mutation control.
The Caddyfile listener must be above 1024 and equal to both
`http_service.internal_port` and `checks.health.port`; moving the listener back
to 80, or drifting `internal_port` away from it, each fails on its own control.
An ignored path must also be absent from the build context and every `COPY`
source must stay triggerable, so the ignore list and `.dockerignore` cannot
diverge; a docs edit is required to stay filtered while `scripts/services.sh`
is required to stay admitted.
Drift is proven on fixtures, not on the live app: a matching triple is in sync,
a hand deploy is drift, a machine and its release disagreeing is unreadable, and
two amd64 children, an absent machine and an absent completed release all fail
closed. `tests/test_deploy_drift.py:DeployDriftMutationTest` removes the digest
comparison, the attestation filter and the release cross-check independently.
The workflow must trigger on the publish completing, not on the push, checkout
the published commit, and queue rather than cancel; dropping the trigger, the
`ref`, or the non-cancelling group each turns its own control red.
`validate.yml` is both a `workflow_call` target and a standalone workflow, so
its concurrency group must include the workflow name: keyed only on the ref,
the nested copy and its standalone twin share a group under
`cancel-in-progress: true` and the publish run is cancelled before `build`
(observed: run `40904431995`, 3s, `build` and `deploy` skipped).

## Simplification Rule

History: `a839ef2` removed 177 lines in one commit and took the SQLite hot copy,
`/health`, and the Cloudflare hop with it. Log maintenance moved between crontab
and script three times without gaining a bound.

Final decision: line-count reduction is not evidence of quality. A
simplification may remove duplication only after authority boundaries,
fail-closed behaviour, bounded resources, atomic state, rollback paths, and the
negative control for each guard are preserved. This rule outranks further
compaction, and every class keeps its History, Final decision, Authority,
Mutation control, and Regression gate sections.

Authority: `tests/test_recurring_gate.py:assert_recurrence_contract`.

Mutation control: `tests/test_recurring_gate.py:test_contract_mutation_removes_authoritative_field`.

Regression gate: the class set and all five fields per class are required, every
named reference resolves, every gate clause is present, and every existing
mutation control still turns red when its guard is removed.
