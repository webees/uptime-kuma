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

`Validate` is a required status check on `main`; a commit that fails it cannot
merge (see [Residual risk](#-residual-risk)).

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
/restic.sh restore-test        # Drill the latest snapshot into /tmp
RESTORE_CONFIRM=yes /restic.sh restore <id>   # Replace the live database
/restic.sh test                # Test email notifications
```

Restore drills never touch `/app/data`. In-place restore stages the snapshot,
verifies it, copies the current database to `kuma.db.previous`, swaps, then
requires an application restart. Keep that rollback copy until the instance is
confirmed healthy.

Every run also compares the recorded `last_success` against the wall clock. When
the last good backup is older than `RESTIC_STALE_HOURS` (default `26`, at most
`8760`), the run still proceeds but warns on stderr and mails
`Backup is stale` to `SMTP_TO`. A first run with no history never warns, and a
single success clears the alert.

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
  observed gating real PRs, so a rename of the check needs the branch rule
  renamed in the same commit.
- The image is only published and deployed from GitHub Actions; a manual
  `fly deploy` builds this Dockerfile and bypasses the digest gate.
- `deploy` has no retry of its own. A Fly control-plane error fails the job and
  needs a `gh run rerun --failed` (observed once: HTTP 503 fetching the
  organization).
- Nothing triggers a restore drill or a real backup on a schedule; both were
  proven inside the image only.

## 📝 License

Distributed under the [MIT License](LICENSE).

---
🚀 Optimized for Fly.io by **[WeBees](https://github.com/webees)**
