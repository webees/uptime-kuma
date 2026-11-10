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

# cloudflared is compiled from a pinned source commit so its embedded Go
# dependency graph can be patched before Trivy evaluates the runtime image.
ARG CLOUDFLARED_VERSION=2026.10.7
ARG CLOUDFLARED_COMMIT=872b241f94871665c1c157adefcf90c11f0ead96
ARG CLOUDFLARED_SOURCE_SHA256=cbf3820d9a88918941f14633ad50e72c81e73889cfceab5fb78b79b5b542d38f
ARG CLOUDFLARED_BUILD_TIME=2026-10-30T16:43:29Z
ARG CLOUDFLARED_GO_VERSION=1.27.3
ARG CLOUDFLARED_GO_SHA256_amd64=65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40
ARG CLOUDFLARED_GO_SHA256_arm64=a06e99309dbcbdca1a8ccd2282c3ee146e55930886e26b4f1193269587165f49
ARG CLOUDFLARED_X_NET_VERSION=0.62.0

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
    # Add Caddy repository
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list \
    # Install additional packages
    && apt-get update && apt-get install -y --no-install-recommends \
    caddy \
    restic \
    # The distribution package embeds a stale golang.org/x/net release. The
    # pinned source build below replaces it with a patched static binary.
    && apt-get purge -y cloudflared \
    && rm -f /usr/bin/cloudflared \
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

# Build cloudflared from the pinned release commit with an explicit minimum for
# the vulnerable Go module, then prove the binary selected on PATH contains the
# repair. Both archives and the compiler are checksum-pinned before execution.
RUN set -eu \
    && case "$TARGETARCH" in \
    amd64) go_sha="$CLOUDFLARED_GO_SHA256_amd64" ;; \
    arm64) go_sha="$CLOUDFLARED_GO_SHA256_arm64" ;; \
    *) echo "Unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "https://go.dev/dl/go${CLOUDFLARED_GO_VERSION}.linux-${TARGETARCH}.tar.gz" \
        -o /tmp/go.tar.gz \
    && printf '%s  %s\n' "$go_sha" /tmp/go.tar.gz | sha256sum -c - \
    && mkdir -p /tmp/go \
    && tar -C /tmp/go --strip-components=1 -xzf /tmp/go.tar.gz \
    && curl -fsSL "https://github.com/cloudflare/cloudflared/archive/${CLOUDFLARED_COMMIT}.tar.gz" \
        -o /tmp/cloudflared.tar.gz \
    && printf '%s  %s\n' "$CLOUDFLARED_SOURCE_SHA256" /tmp/cloudflared.tar.gz | sha256sum -c - \
    && mkdir -p /tmp/cloudflared-src \
    && tar -C /tmp/cloudflared-src --strip-components=1 -xzf /tmp/cloudflared.tar.gz \
    && cd /tmp/cloudflared-src \
    && /tmp/go/bin/go mod edit --require="golang.org/x/net@v${CLOUDFLARED_X_NET_VERSION}" \
    && CGO_ENABLED=0 GOOS=linux GOARCH="$TARGETARCH" GOTOOLCHAIN=local \
        GOPROXY=https://proxy.golang.org,direct \
        /tmp/go/bin/go build -trimpath -buildvcs=false -mod=mod \
        -ldflags="-s -w -X main.Version=${CLOUDFLARED_VERSION} -X main.BuildTime=${CLOUDFLARED_BUILD_TIME}" \
        -o /usr/local/bin/cloudflared ./cmd/cloudflared \
    && test "$(command -v cloudflared)" = /usr/local/bin/cloudflared \
    && /usr/local/bin/cloudflared --version \
    && /tmp/go/bin/go version -m /usr/local/bin/cloudflared > /tmp/cloudflared.buildinfo \
    && grep -Eq "^[^:]+: go$(printf '%s' "$CLOUDFLARED_GO_VERSION" | sed 's/\./\\./g')$" \
        /tmp/cloudflared.buildinfo \
    && test "$(awk '$1 == "dep" && $2 == "golang.org/x/net" {print $3}' \
        /tmp/cloudflared.buildinfo)" = "v${CLOUDFLARED_X_NET_VERSION}" \
    && rm -rf /tmp/go /tmp/go.tar.gz /tmp/cloudflared-src /tmp/cloudflared.tar.gz \
        /tmp/cloudflared.buildinfo /root/go /root/.cache/go-build

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
        @azure/msal-node@7.2.1 \
        @grpc/grpc-js@1.14.7 \
        axios@1.21.1 \
        body-parser@2.4.0 \
        follow-redirects@1.16.3 \
        liquidjs@10.30.1 \
        mysql2@3.24.6 \
        nodemailer@10.5.0 \
        protobufjs@8.8.1 \
        redbean-node@0.4.0 \
        tar@7.5.22 \
        ws@8.22.2 \
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
