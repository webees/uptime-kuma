# 🐻 Uptime Kuma (Fly.io Edition)

[![Fly.io](https://img.shields.io/badge/Fly.io-Deploy-purple?style=for-the-badge&logo=flydotio)](https://fly.io)
[![Docker](https://img.shields.io/badge/Docker-ghcr.io-blue?style=for-the-badge&logo=docker)](https://ghcr.io/webees/uptime-kuma)
[![Uptime Kuma](https://img.shields.io/badge/Uptime%20Kuma-Latest-green?style=for-the-badge)](https://github.com/louislam/uptime-kuma)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

> Uptime Kuma on Fly.io with a Caddy host gate, a small process supervisor, and checked Restic backups to Cloudflare R2.

## ✨ Features

| Component | Description |
| :--- | :--- |
| **Uptime Kuma** | Self-hosted monitoring tool |
| **Caddy** | Reverse proxy with security headers and IP forwarding |
| **Supervisor** | Restarts the proxy and scheduler, hands app exits back to Fly |
| **Supercronic** | Cron daemon for automated tasks |
| **Restic** | Encrypted incremental backups to S3/R2 |
| **msmtp** | Email notifications for system alerts |

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Fly.io Edge                        │
└───────────────────────────┬─────────────────────────────┘
                            │ :443
┌───────────────────────────▼─────────────────────────────┐
│                        Caddy                            │
│         (/health, host gate, security headers)          │
└───────────────────────────┬─────────────────────────────┘
                            │ :3001
┌───────────────────────────▼─────────────────────────────┐
│                     Uptime Kuma                         │
│                  (SQLite + WebSocket)                   │
└─────────────────────────────────────────────────────────┘
         │
         │ Hourly backup
         ▼
┌─────────────────────────────────────────────────────────┐
│                  Restic → Cloudflare R2                 │
│          (7 daily, 4 weekly, 3 monthly, 3 yearly)       │
└─────────────────────────────────────────────────────────┘
```

## 🚀 Quick Start

### 1. Initialize Application

```bash
# Login to Fly.io
fly auth login

# Create application
fly apps create bxx-uptime-kuma

# Import secrets from .env
cat .env | fly secrets import

# Create storage volume
fly volumes create app_data --region sin --size 1
```

### 2. Required Secrets Configuration

```bash
# Domain configuration (Multiple domains: "a.com b.com")
fly secrets set CADDY_DOMAINS="status.example.com"

# Restic / S3 backup settings
fly secrets set RESTIC_PASSWORD="your-secure-password"
fly secrets set RESTIC_REPOSITORY="s3://your-account-id.r2.cloudflarestorage.com/uptime-kuma"
fly secrets set AWS_ACCESS_KEY_ID="your-r2-id"
fly secrets set AWS_SECRET_ACCESS_KEY="your-r2-key"

# SMTP notification settings
fly secrets set SMTP_HOST="smtp.gmail.com"
fly secrets set SMTP_PORT="587"
fly secrets set SMTP_FROM="sender@example.com"
fly secrets set SMTP_TO="admin@example.com"
fly secrets set SMTP_USERNAME="sender@example.com"
fly secrets set SMTP_PASSWORD="app-specific-password"
```

Backup and freshness alerts call `msmtp` directly. The image intentionally
contains no `bsd-mailx` client or local mail-transfer agent, so there is only
one SMTP delivery path to the configured server.

### 3. Deploy

```bash
# fly.toml has no image: build locally, or pass the published digest.
fly deploy

# What CI does: deploy an exact amd64 manifest built from a validated commit.
fly deploy --image ghcr.io/webees/uptime-kuma@sha256:<digest>
```

`ghcr.yaml` runs `validate` first, pushes a `build-<run>-<attempt>` tag, records
the amd64 manifest digest, smoke-tests that digest, and scans the same digest
with Trivy. A complete HIGH/CRITICAL inventory is retained as SARIF in code
scanning; a second pass blocks deployment on findings that have a published
fixed version. The scan also produces a CycloneDX SBOM artifact named from that
digest. Only after both gates pass is the digest sent to Fly. Deployment
verification fails unless the latest release is complete, every machine runs
that digest, and its health check is passing.

The runtime installs its pinned application dependencies with npm during the
build, then removes the generated root lockfile, package-shipped historical
lockfiles, and `npm`/`npx` themselves. The hidden
`node_modules/.package-lock.json` remains because it describes the installed
tree; it is not a build recipe. This keeps scanner results and runtime tools
limited to what the application actually ships.

Only after that verification passes is that exact index promoted to `latest`
and re-read to prove both the index and its amd64 child still match. A failed,
cancelled, or unverified build therefore leaves `latest` on the last good
image; `latest` is a convenience reference, never the deploy input.

The weekly schedule is conditional: `rebuild_check` probes the packages pending
in the published `latest` image and the build only runs when that count is
positive. A current image therefore costs one short probe instead of a build and
a deploy, and a probe failure fails the run rather than silently skipping it.
Production and scheduled builds bypass Docker layer cache, so a newly published
Debian security update cannot be hidden by a cached `apt-get` layer.
`workflow_dispatch` defaults to `force`, so a manual run still publishes
immediately; choose `probe` to exercise the scheduled decision by hand.

`Validate` is a required status check on `main`; a commit that fails it cannot
merge (see [Residual risk](#-residual-risk)).

`deploy-drift` runs after every publish and daily at 05:41: it reads the machine
image and the `latest` amd64 child and fails on a difference. It is the only
detector for the case above — a hand deploy, or a promotion that never happened
— because CI itself is not involved in either. A scheduled check that overlaps
a rolling deploy retries only the transient `replacing` state for a bounded
interval; stopped, unhealthy, mismatched, and other unreadable states remain
immediate failures. It is triggered by the publish workflow's *title*, so
renaming that workflow means renaming the listener too. It checks only the
trusted default branch's workflow, configuration and script; the completed
publish SHA is never checked out or executed.

### CI Deploy Credential

The publish workflow deploys the exact digest with a token scoped to one app.
Create a 60-day token, store it as a secret, and record its UTC expiry in a
repository variable so `automation-health.yml` can fail before a deploy discovers
an expired credential halfway through a release:

```bash
# The command prints the token once. Do not paste it into a log or commit it.
fly tokens create deploy -a bxx-uptime-kuma --expiry 60d \
  --name "github-actions-$(date +%Y%m%d)"
gh secret set FLY_API_TOKEN --repo webees/uptime-kuma
gh variable set FLY_API_TOKEN_EXPIRES_AT --repo webees/uptime-kuma \
  --body "YYYY-MM-DDTHH:MM:SSZ"
```

Replace the placeholder with the token's actual UTC expiry. The daily and manual
Automation Health runs require a `FlyV1` token, require a timezone-qualified
expiry, and keep the remaining lifetime between 30 and 90 days. They also
require `ALERT_WEBHOOK_URL`, so an absent notification target is a red preflight
rather than a silent skip. The checker never prints either credential.

Keep the scope on `bxx-uptime-kuma`: a token minted for another app passes the
`deploy` guard and then fails against the real one. To rotate safely, create the
new token, update both repository settings, dispatch `automation-health.yml`,
and revoke the old token only after the next verified deploy succeeds.

## 🛠️ Management & Operations

### Deployment CLI

```bash
fly status                     # Check application status
fly logs                       # View real-time logs
fly ssh console                # Access container shell
fly apps restart               # Restart all instances
```

### Backup Operations (via SSH)

```bash
flyctl ssh console -u node -C '/restic.sh backup'              # Run manual backup
flyctl ssh console -u node -C '/restic.sh snapshots'           # List all snapshots
flyctl ssh console -u node -C '/restic.sh check-freshness'     # Fail if the last success is too old
flyctl ssh console -u node -C '/restic.sh restore-test'        # Drill the latest snapshot into /tmp
flyctl ssh console -u node -C 'RESTORE_CONFIRM=yes /restic.sh restore <id>'   # Replace the live database
flyctl ssh console -u node -C '/restic.sh test'                # Test email notifications
```

Fly SSH defaults to `root`, while the scheduler runs as `node`. Always pass
`-u node`; `restic.sh` rejects root invocations so a manual read cannot create
root-owned files that make every later scheduled backup fail.

`supercronic` runs three jobs, each off minute zero so a deploy restart cannot
cut one in half: `backup` at `:25` every hour, `check-freshness` at `:55` every
hour, and `restore-test` at 04:15 on the first of each month. The freshness job
is separate on purpose: the in-run warning only fires when something else
executes, so a scheduler that died with the application would otherwise look
fresh forever.

Restore drills never touch `/app/data`. In-place restore stages the snapshot,
verifies it, copies the current database to `kuma.db.previous`, swaps, then
requires an application restart. Keep that rollback copy until the instance is
confirmed healthy.

Snapshots exclude the live SQLite files and the active top-level
`/app/data/error.log`. Uptime Kuma 2.5.9 writes errors to that file without
rotation; on 2026-11-10 it had reached 441 MiB on a 974 MiB data volume. Every
non-library `restic.sh` invocation now retains only the newest
`RESTIC_ERROR_LOG_MAX_BYTES` of diagnostics (default 8 MiB, maximum 64 MiB)
before backend work. A symlink, non-regular path, or failed trim stops the
invocation instead of silently leaving an unbounded file. Other data files
under `/app/data` remain backed up; current container output is available
through `fly logs`.

`restore-test` also starts an isolated Uptime Kuma against a copy of the
restored database and requires `/` to return `302`. Monitors are deactivated in
that copy before launch, and the process is stopped before the drill returns,
so the check does not run production monitors or emit notifications.

Every run also compares the recorded `last_success` against the wall clock. When
the last good backup is older than `RESTIC_STALE_HOURS` (default `26`, at most
`8760`), the run still proceeds but warns on stderr and mails
`Backup is stale` to `SMTP_TO`. A first run with no history never warns, a
backwards clock stays quiet, and a single success clears the alert.

`check-freshness` is the same comparison with teeth: it exits `2`, lets the
shared failure trap record `state=failed`, and mails once. It never rewrites
`last_success`, so a passing check cannot make a stale backup look fresh.

### Log Inspection

```bash
cat /var/log/restic/*.log      # Check backup logs
tail -f /var/log/msmtp.log     # Monitor email logs
```

## 🔐 Security Headers

The Caddy configuration automatically applies the following security posture:
- **HSTS**: `Strict-Transport-Security` (1 year)
- **Clickjacking**: `X-Frame-Options DENY`
- **MIME Sniffing**: `X-Content-Type-Options nosniff`
- **XSS Protection**: `X-XSS-Protection 1; mode=block`
- **Privacy**: `Referrer-Policy strict-origin-when-cross-origin`
- **Indexing**: `X-Robots-Tag noindex, nofollow`

`/health` is answered by Caddy itself and is never subject to
`CADDY_DOMAINS`, so the Fly probe survives a dead application. Every other
request is matched against the allowlist and answers 403 (with the full header
set) when it is not listed.

Client IPs come from `CF-Connecting-IP`, then `Fly-Client-IP`, then the peer
address, and are forwarded as a single-hop `X-Real-IP`/`X-Forwarded-For`.

Base image and the downloaded scheduler are pinned by digest/sha256. `.env`
holds placeholders only; real values live in `fly secrets`.

## 🧪 Validation

```bash
shellcheck scripts/*.sh scripts/entrypoint/*.sh
python3 -m unittest discover -s tests -p 'test_*.py'
docker build --tag uptime-kuma:local .
python3 tests/runtime_smoke.py uptime-kuma:local
```

`docs/recurring_issues.md` records the invariants that regressed more than once.
Every round starts from its target-mode matrix; `tests/test_recurring_gate.py`
fails when a class, a field, or a mutation control disappears.

## 🔁 Automation

### Failure Notifications

Create an HTTPS webhook and store it as `ALERT_WEBHOOK_URL`:

```bash
gh secret set ALERT_WEBHOOK_URL --repo webees/uptime-kuma
```

Every failure path in Validate, GHCR publish/deploy, upstream updates, deploy
drift, GHCR cleanup, Automation Health and Dependabot auto-merge sends one JSON
notification. The payload contains only the repository, workflow, run URL,
branch, commit, conclusion and a short reason. It includes both `text` and
`content` fields for common webhook receivers. A watched workflow reports its
own failure; Automation Health does not send a second copy for that
`workflow_run`. Scheduled and manual health runs fail closed when the secret is
absent, and the failure path adds an explicit annotation instead of claiming a
notification was sent.

Six workflows run without a human, and Dependabot updates the action pins:

| Workflow | When | What it does |
| :--- | :--- | :--- |
| `upstream-updates.yml` | daily `02:23` UTC | resolves the newest stable Uptime Kuma release, its image digest, the supercronic binaries and the explicit Node.js runtime packages, rewrites only the known `Dockerfile` pins, opens `deps/upstream`, approves only the exact held same-SHA PR validator, waits for it to pass, then requires a clean branch and dispatches the publish chain |
| `dependabot-automerge.yml` | when Dependabot opens or refreshes a PR | accepts only same-Action SHA/comment replacements, waits for the required `validate` check, then merges the action update directly |
| `ghcr.yaml` | weekly `Mon 03:17` UTC, plus every push | probes the published image for pending OS packages, builds only when there is something to ship, smoke-tests and scans the digest, records its SBOM, deploys it to Fly, verifies the release and only then moves `latest` |
| `deploy-drift.yml` | after every publish, daily `05:41` UTC | fails when production is not running the digest `latest` was promoted from |
| `ghcr-cleanup.yml` | daily `03:00` UTC, plus every push | prunes untagged and stale tags without racing a publish |
| `automation-health.yml` | daily `05:47` UTC, plus watched workflow completions | fails on a failed main-branch workflow, checks that every scheduled workflow is still active and recent, and fail-closes on missing or expiring deployment credentials and the alert webhook |

`upstream-updates.yml` closes the last manual loop: image and tool pins are
read from GitHub releases and Docker Hub, while direct Node.js versions come
from the public npm registry and the four installer-only `tar` overrides stay
equal to the direct `tar` pin. An npm pin that is already ahead of the
registry's `latest` is retained rather than rolled back. A PR opened or updated
with `GITHUB_TOKEN` creates an approval-required `pull_request` check. The
workflow first proves that the PR changes only `Dockerfile`, identifies the
exact held validator for the pushed SHA, and approves only that run through
its scoped `actions: write` permission. It then waits for that validator to
succeed and refuses to merge unless branch protection reports `CLEAN`. Public
release metadata and assets use bounded retries for transient HTTP failures,
and the held-run lookup allows for GitHub's delayed `pull_request` scheduling.
A PAT,
`--admin`, a replacement dispatch or a synthetic success is never accepted. A
stable update therefore merges on its own once the real `validate` check
passes on the branch head, matching the Vaultwarden delivery model. A
**downgrade** or unexpected **prerelease** stays open with the `needs-review`
label; a major npm update still has to pass the same full image/runtime gate
before it can merge. Merging dispatches `ghcr.yaml`, since a push made with
`GITHUB_TOKEN` does not emit a workflow-triggering event; that run then builds,
smoke-tests, deploys and verifies the new digest.

Its second job is one half of a watchdog pair. It checks `automation-health.yml`,
`ghcr.yaml`, `deploy-drift.yml` and `ghcr-cleanup.yml`; `automation-health.yml`
checks `upstream-updates.yml` in return. Each check fails when a workflow is
disabled or has not completed within its cadence (weekly `192h`, daily `48h`).
A recent failed completion still proves the cron fired; that workflow's own
failure and notifier remain the failure signal, so mutually watching workflows
cannot deadlock waiting for each other's next success.
GitHub disables a schedule after 60 idle days, and a cron that silently stops
firing otherwise looks the same as "nothing to update". A workflow cannot see
its own disabled state, so the mutual pair is deliberate; neither check claims
to monitor itself.

`.github/dependabot.yml` opens a grouped daily PR when an immutable GitHub
Actions SHA has a newer release. `dependabot-automerge.yml` checks the API diff
against the trusted default branch and accepts only complete remote Action
`uses: owner/action@40-sha # version` replacements for the same Action. Workflow
logic, local actions, added/deleted files, short refs, missing comments and
cross-hunk moves fail closed. The required `validate` path repeats that API-diff
check, so bypassing the auto-merge workflow cannot turn a broader Dependabot
change green. Only a green required check is merged.

Dependency bots cannot author a human round matrix, but the exemption is earned
inside the required check rather than skipped wholesale. The
`Automated dependency PR scope` step first requires a same-repository head. A
Dependabot PR must contain only complete remote Action pin replacements,
checked by the validator from the trusted base revision; a
`github-actions[bot]` PR must come from `deps/upstream` and change exactly
`Dockerfile`. Only after that proof does `Round matrix` skip the bot author, and
the normal image/runtime gates still run. Removing the author, repository,
branch, file or diff check fails `validate`.

Run the check by hand without merging:

```bash
gh workflow run upstream-updates.yml
python3.13 scripts/upstream_deps.py resolve    # pins the API currently returns
python3.13 scripts/upstream_deps.py compare --uptime-version … --uptime-digest …
```

## ⚠️ Residual risk

- The `validate` context is enforced on `main` for admins too and has been
  observed gating real PRs. That rule lives outside the repository, and
  `GITHUB_TOKEN` cannot read it, so it is recorded in
  `docs/branch_protection.json` and compared by `scripts/branch_rule.py`.
  `gh api repos/webees/uptime-kuma/branches/main/protection > live.json` then
  `python3.13 scripts/branch_rule.py live.json` reports `rule matches` or
  `RULE DRIFT`. A check rename still has to land in both the live rule and the
  recorded copy, in that order, or every merge blocks.
  The rule is `strict`: a branch behind `main` must update and re-run
  `validate` before merging, so a green check never stands in for a test
  against the commit that actually lands.
- The image is only published and deployed from GitHub Actions, and a manual
  `fly deploy` still builds this Dockerfile and bypasses the digest gate. It no
  longer goes unnoticed: `deploy-drift` compares the machine image with the
  `latest` amd64 child after every publish and daily, and fails the run on a
  difference. The gate is therefore after the fact — a hand deploy reaches
  production first and is reported, not blocked. Observed 2026-10-30: a hand
  `fly deploy --image` of the previous digest produced release `v67` and the
  next `deploy-drift` run failed with `DRIFT: production runs …615bbab9… but
  latest is …964758a0…`; redeploying the promoted digest produced `v68` and
  the following run returned `in sync`.
- `deploy` retries only transient Fly 5xx up to three attempts with backoff.
  A longer control-plane outage still fails the job and needs
  `gh run rerun --failed` (observed twice: `status 503`). The Fly CLI is
  installed from a sha256-pinned GitHub release, because the setup action
  resolved `latest` through Fly itself and failed there first.
- `check-freshness` fails the scheduled run and mails, but it can only report
  through SMTP and `backup-status.txt`; if SMTP is misconfigured the failure
  stays in the workflow-free cron log. Runtime smoke now proves the restored
  database can boot the application, but no production snapshot has yet been
  exercised through the new drill.
- `ALERT_WEBHOOK_URL` is not configured in this repository. If a workflow
  fails, the notifier writes an explicit skipped notice and no external alert
  is delivered; the failed run remains the only signal. Trigger: the repository
  secret is absent. Observed by: `gh secret list` returning no
  `ALERT_WEBHOOK_URL` entry.
- The hardened runtime image release 84 (`4c56ccd`) has 403 open Trivy
  code-scanning alerts: 9 critical and 394 high. Every alert has an empty
  `Fixed Version`, so the actionable gate cannot block on an available upgrade.
  The nine critical alerts cover `zlib1g`, `sqlite3`/`libsqlite3-0`, `python3-jwt`,
  `libxml2`, `libglib2.0-0` and `gawk`. A reachability review found no demonstrated path
  from the public web surface to their vulnerable parsers, but that does not
  close the findings: the bundled SQLite 3.41.1 build is affected by
  `CVE-2025-7458`, and application SQL must remain parameterized. Trigger:
  Debian 12.15 package metadata supplies no fixed versions. Observed by: a
  paginated GitHub code-scanning query on 2026-11-10 for release 84 returned
  403 open alerts, 9 critical, 394 high, and zero non-empty fix versions. The
  detailed disposition is in `docs/recurring_issues.md`.

## 📝 License

Distributed under the [MIT License](LICENSE).

---
🚀 Optimized for Fly.io by **[WeBees](https://github.com/webees)**
