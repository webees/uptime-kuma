# 🐻 Uptime Kuma (Fly.io Edition)

![Fly.io](https://img.shields.io/badge/Fly.io-Deploy-purple?style=for-the-badge&logo=flydotio)
![Docker](https://img.shields.io/badge/Docker-Container-blue?style=for-the-badge&logo=docker)
![Uptime Kuma](https://img.shields.io/badge/Uptime%20Kuma-v2-green?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)

> **TL;DR**: Uptime Kuma v2 on Fly.io with Caddy, Overmind, and Restic backups. 🚀

## ✨ Stack
| Component | Role |
| :--- | :--- |
| **Uptime Kuma** | 📊 Monitoring |
| **Overmind** | 🧠 Process Manager |
| **Caddy** | 🔒 Reverse Proxy |
| **Supercronic** | ⏰ Cron |
| **Restic** | 💾 Backups |

## 🚀 Quick Start

### 1️⃣ Deploy
```bash
fly auth login
fly apps create uptime-kuma
fly volumes create app_data --size 1
fly deploy
```

### 2️⃣ Configure Secrets
Create `.env` and import:
```bash
# .env
# RESTIC_PASSWORD=xxx
# AWS_ACCESS_KEY_ID=xxx
# AWS_SECRET_ACCESS_KEY=xxx
cat .env | fly secrets import
```

## 🛠️ CLI
Connect via `fly ssh console` and use the helpers:

| Command | Description |
| :--- | :--- |
| `/restic.sh backup` | 💾 Trigger manual backup |
| `/restic.sh restore <id>` | ♻️ Restore snapshot |
| `/restic.sh snapshots` | 📜 List snapshots |

## ⚙️ Config
- **Caddy**: `config/Caddyfile`
- **Cron**: `config/crontab`
- **Procs**: `config/Procfile`

---
Made with ❤️ for ☁️