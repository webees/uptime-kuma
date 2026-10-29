# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║ Uptime Kuma Deployment                                                    ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
ARG UPTIME_KUMA_VERSION=2.1.0-beta.2

# Pinned index digest: a retagged beta can never silently change the base.
FROM louislam/uptime-kuma:${UPTIME_KUMA_VERSION}@sha256:3bc2b7a28a5debba4d33cef02b1cf97699404af30d5a5227a48d0fe226d6321b

# ── Build Args ────────────────────────────────────────────────────────────────
ARG TARGETARCH
ARG SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.37/supercronic-linux-${TARGETARCH}
# sha256 of the release asset per architecture, verified before use.
ARG SUPERCRONIC_SHA256_amd64=14a91b905dc68eaf533557734198b5d4a46898109c29193becddda40bf7b09d2
ARG SUPERCRONIC_SHA256_arm64=b8fe14272949722290a6e0d271ad6198fbaa62cedb9c8bc3608b42996af0915c

# ── Environment ───────────────────────────────────────────────────────────────
ENV WORKDIR=/app \
    TZ="Asia/Shanghai" \
    SUPERVISE_RESTART_DELAY=5

WORKDIR $WORKDIR

# ── Config Files ──────────────────────────────────────────────────────────────
COPY config/crontab \
    config/Caddyfile \
    scripts/services.sh \
    scripts/restic.sh \
    /
COPY scripts/entrypoint/ /entrypoint/
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /services.sh && rm -f /app/data/.gitkeep \
    # The process table runs unprivileged: /restic.sh writes these.
    && install -d -o node -g node /var/log/restic /app/data \
    && install -o node -g node -m 600 /dev/null /etc/msmtprc

# ── Dependencies ──────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    openssl \
    tzdata \
    ntpdate \
    iptables \
    iputils-ping \
    sqlite3 \
    msmtp \
    bsd-mailx \
    # Add Caddy repository
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list \
    # Install additional packages
    && apt-get update && apt-get install -y --no-install-recommends \
    caddy \
    restic \
    # Download the scheduler and verify the pinned digest before it is used
    && case "$TARGETARCH" in \
    amd64) supercronic_sha="$SUPERCRONIC_SHA256_amd64" ;; \
    arm64) supercronic_sha="$SUPERCRONIC_SHA256_arm64" ;; \
    *) echo "Unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "$SUPERCRONIC_URL" -o /usr/local/bin/supercronic \
    && printf '%s  %s\n' "$supercronic_sha" /usr/local/bin/supercronic | sha256sum -c - \
    && chmod +x /usr/local/bin/supercronic /restic.sh \
    # Symlink msmtp for mail commands
    && ln -sf /usr/bin/msmtp /usr/bin/sendmail \
    && ln -sf /usr/bin/msmtp /usr/sbin/sendmail \
    # Cleanup
    && apt -y autoremove \
    && rm -rf /var/lib/apt/lists/*

# ── Startup ───────────────────────────────────────────────────────────────────
ENTRYPOINT ["/entrypoint.sh"]
