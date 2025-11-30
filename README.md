# Uptime Kuma on Fly.io

This project provides a customized Docker image for deploying [Uptime Kuma](https://github.com/louislam/uptime-kuma) (v2) on [Fly.io](https://fly.io/). It is enhanced with a process manager, cron scheduler, backup utilities, and a reverse proxy to provide a robust monitoring solution.

## ✨ Features

- **Uptime Kuma v2**: The latest version of the fancy self-hosted monitoring tool.
- **Overmind**: A process manager to handle multiple processes (Uptime Kuma, Caddy, Supercronic) within the single container.
- **Caddy**: A powerful, enterprise-ready, open source web server with automatic HTTPS, acting as a reverse proxy.
- **Supercronic**: A cron-compatible job runner for containers, used for scheduling backups.
- **Restic**: A fast, secure, and efficient backup program.
- **Fly.io Ready**: Optimized configuration for deployment on Fly.io.

## 🚀 Deployment Guide

Follow these steps to deploy your own instance on Fly.io.

### Prerequisites

- A [Fly.io](https://fly.io/) account.
- [flyctl](https://fly.io/docs/hands-on/install-flyctl/) installed on your machine.

### Steps

1.  **Login to Fly.io**
    ```bash
    fly auth login
    ```

2.  **Create the Application**
    Replace `uptime-kuma` with your desired unique app name.
    ```bash
    fly apps create uptime-kuma
    ```

3.  **Configure Secrets**
    Create a `.env` file with your sensitive configuration (e.g., Restic passwords, S3 credentials) and import them.
    ```bash
    # Example .env content
    # RESTIC_PASSWORD=your_secure_password
    # AWS_ACCESS_KEY_ID=...
    # AWS_SECRET_ACCESS_KEY=...
    
    cat .env | fly secrets import
    ```

4.  **Create Persistent Storage**
    Create a volume to store Uptime Kuma's database and data.
    ```bash
    fly volumes create app_data --size 1
    ```

5.  **Deploy**
    Deploy the application using the configuration in `fly.toml`.
    ```bash
    fly deploy
    ```

## ⚙️ Configuration

### Environment Variables

The following environment variables can be set in your `.env` file or via `fly secrets set`:

- `TZ`: Timezone (default: `Asia/Shanghai`).
- `RESTIC_REPOSITORY`: Restic repository location (e.g., `s3:https://s3.amazonaws.com/bucket_name`).
- `RESTIC_PASSWORD`: Password for the Restic repository.
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`: Credentials for S3 storage if used for backups.

### Config Files

- **`config/Caddyfile`**: Configuration for the Caddy web server.
- **`config/crontab`**: Cron schedule for scheduled tasks (e.g., backups).
- **`config/Procfile`**: Process definitions for Overmind.

## 🛡️ Backup & Restore

This image includes `restic` for backups. A helper script is available at `/restic.sh`.

### Automatic Backups
Backups are scheduled via `config/crontab`. By default, check the `config/crontab` file to see the schedule.

### Manual Operations

You can run commands inside the container using `fly ssh console`.

**Trigger a Backup:**
```bash
/restic.sh backup
```

**List Snapshots:**
```bash
/restic.sh snapshots
```

**Restore:**
```bash
/restic.sh restore <snapshot-id>
```

## 🛠️ Local Development

To build and run the image locally:

```bash
# Build the image
docker build -t uptime-kuma-custom .

# Run the container
docker run -d \
  -p 80:80 \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  uptime-kuma-custom
```