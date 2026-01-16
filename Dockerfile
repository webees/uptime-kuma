# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║ Uptime Kuma Deployment                                                    ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
FROM louislam/uptime-kuma:2.1.0-beta.2

# ── Build Args ────────────────────────────────────────────────────────────────
ARG TARGETARCH
ARG SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.37/supercronic-linux-${TARGETARCH}
ARG OVERMIND_URL=https://github.com/DarthSim/overmind/releases/download/v2.5.1/overmind-v2.5.1-linux-${TARGETARCH}.gz

# ── Environment ───────────────────────────────────────────────────────────────
ENV WORKDIR=/app \
    TZ="Asia/Shanghai" \
    OVERMIND_PROCFILE=/Procfile \
    OVERMIND_CAN_DIE=caddy,crontab \
    OVERMIND_SHOW_TIMESTAMPS=0

WORKDIR $WORKDIR

# ── Config Files ──────────────────────────────────────────────────────────────
COPY config/crontab \
    config/Procfile \
    config/Caddyfile \
    scripts/restic.sh \
    /

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
    tmux \
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
    # Download binary tools
    && curl -fsSL "$SUPERCRONIC_URL" -o /usr/local/bin/supercronic \
    && curl -fsSL "$OVERMIND_URL" | gunzip -c - > /usr/local/bin/overmind \
    && chmod +x /usr/local/bin/supercronic /usr/local/bin/overmind /restic.sh \
    # Symlink msmtp for mail commands
    && ln -sf /usr/bin/msmtp /usr/bin/sendmail \
    && ln -sf /usr/bin/msmtp /usr/sbin/sendmail \
    # Cleanup
    && apt -y autoremove \
    && rm -rf /var/lib/apt/lists/*

# ── Startup ───────────────────────────────────────────────────────────────────
ENTRYPOINT []
CMD ["overmind", "start"]
