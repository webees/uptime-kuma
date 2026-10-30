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

### 3. Deploy

```bash
# fly.toml has no image: build locally, or pass the published digest.
fly deploy

# What CI does: deploy an exact amd64 manifest built from a validated commit.
fly deploy --image ghcr.io/webees/uptime-kuma@sha256:<digest>
```

`ghcr.yaml` runs `validate` first, pushes a `build-<run>-<attempt>` tag, records
the amd64 manifest digest, smoke-tests that digest, and only then deploys it.
Deployment verification fails unless the latest release is complete, every
machine runs that digest, and its health check is passing.

Only after that verification passes is that exact index promoted to `latest`
and re-read to prove both the index and its amd64 child still match. A failed,
cancelled, or unverified build therefore leaves `latest` on the last good
image; `latest` is a convenience reference, never the deploy input.

The weekly schedule is conditional: `rebuild_check` probes the packages pending
in the published `latest` image and the build only runs when that count is
positive. A current image therefore costs one short probe instead of a build and
a deploy, and a probe failure fails the run rather than silently skipping it.
`workflow_dispatch` defaults to `force`, so a manual run still publishes
immediately; choose `probe` to exercise the scheduled decision by hand.

`Validate` is a required status check on `main`; a commit that fails it cannot
merge (see [Residual risk](#-residual-risk)).

`deploy-drift` runs after every publish and daily at 05:41: it reads the machine
image and the `latest` amd64 child and fails on a difference. It is the only
detector for the case above — a hand deploy, or a promotion that never happened
— because CI itself is not involved in either. It is triggered by the publish
workflow's *title*, so renaming that workflow means renaming the listener too.

### CI Deploy Credential

The publish workflow deploys the exact digest with a token scoped to one app.
Create it and store it once; rotating it is the same command:

```bash
fly tokens create deploy -a bxx-uptime-kuma
gh secret set FLY_API_TOKEN --repo webees/uptime-kuma
```

Keep the scope on `bxx-uptime-kuma`: a token minted for another app passes the
`deploy` guard and then fails against the real one.

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
/restic.sh backup              # Run manual backup
/restic.sh snapshots           # List all snapshots
/restic.sh check-freshness     # Fail if the last success is too old
/restic.sh restore-test        # Drill the latest snapshot into /tmp
RESTORE_CONFIRM=yes /restic.sh restore <id>   # Replace the live database
/restic.sh test                # Test email notifications
```

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
  stays in the workflow-free cron log. The restore drill proves a snapshot is
  readable and consistent, not that the application boots against it.

## 📝 License

Distributed under the [MIT License](LICENSE).

---
🚀 Optimized for Fly.io by **[WeBees](https://github.com/webees)**
