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

# The upstream release image can lag Debian security updates. Upgrade the
# inherited system packages before adding deployment dependencies, then discard
# the package indexes so they do not become part of the runtime image.
RUN apt-get update \
    && apt-get dist-upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# ── Config Files ──────────────────────────────────────────────────────────────
COPY config/crontab \
    config/Caddyfile \
    scripts/services.sh \
    scripts/restic.sh \
    /
COPY scripts/entrypoint/ /entrypoint/
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /services.sh \
    && rm -f /app/data/.gitkeep /app/extra/healthcheck \
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

# Backport the dependency releases already adopted by upstream master. The
# release image's ranges predate fixes for HIGH/CRITICAL advisories in several
# runtime clients. Keep the direct packages pinned and force the vulnerable
# installer-only tar versions to the patched release without changing Uptime
# Kuma's application source.
RUN cd /app \
    && npm pkg set \
        "overrides.@louislam/sqlite3.tar=7.5.22" \
        "overrides.@mapbox/node-pre-gyp.tar=7.5.22" \
        "overrides.node-gyp.tar=7.5.22" \
        "overrides.cacache.tar=7.5.22" \
    && npm install --save-exact --omit=dev --no-audit --no-fund \
        @azure/identity@4.14.1 \
        @azure/msal-node@7.1.2 \
        @grpc/grpc-js@1.14.7 \
        axios@0.34.0 \
        body-parser@1.20.9 \
        follow-redirects@1.16.2 \
        liquidjs@10.30.1 \
        mysql2@3.24.6 \
        nodemailer@10.3.1 \
        protobufjs@8.8.0 \
        redbean-node@0.4.0 \
        tar@7.5.22 \
        ws@8.22.1 \
    # The runtime never installs packages. Removing the generated root lockfile
    # and package-shipped historical lockfiles stops image scanners from
    # inventorying unreachable dev-only dependencies as runtime packages. The
    # installed node_modules tree and its hidden metadata remain intact.
    && rm -f /app/package-lock.json \
    && find /app/node_modules -type f -name package-lock.json -delete \
    # npm is a build-time tool here. Removing it and its wrappers keeps the
    # runtime image's dependency surface limited to what the application uses.
    && rm -rf /usr/local/lib/node_modules/npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx

# ── Startup ───────────────────────────────────────────────────────────────────
# The upstream healthcheck binary was built with an obsolete Go runtime. Use the
# already-installed curl against the application's internal listener instead.
HEALTHCHECK --interval=60s --timeout=30s --start-period=180s --retries=5 \
    CMD curl --fail --silent --show-error http://127.0.0.1:3001/ || exit 1

ENTRYPOINT ["/entrypoint.sh"]
