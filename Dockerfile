# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║ Uptime Kuma Deployment                                                    ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
ARG UPTIME_KUMA_VERSION=2.5.9

# Pinned index digest: a retagged beta can never silently change the base.
FROM louislam/uptime-kuma:${UPTIME_KUMA_VERSION}@sha256:e88415a90dd2f7efecdbc18de0490aff292764a3ac471531bf1d0481725591bd

# ── Build Args ────────────────────────────────────────────────────────────────
ARG TARGETARCH
ARG SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.51/supercronic-linux-${TARGETARCH}
# sha256 of the release asset per architecture, verified before use.
ARG SUPERCRONIC_SHA256_amd64=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123
ARG SUPERCRONIC_SHA256_arm64=4ae22576a78223d8ccdf5f3b3660a5cbb5288416bf0ca96afc4a36d195932b94

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
