FROM louislam/uptime-kuma:nightly2

# 🏗️ Args & Env
ARG TARGETARCH
ARG SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.37/supercronic-linux-${TARGETARCH}
ARG OVERMIND_URL=https://github.com/DarthSim/overmind/releases/download/v2.5.1/overmind-v2.5.1-linux-${TARGETARCH}.gz

ENV WORKDIR=/app \
    TZ="Asia/Shanghai" \
    OVERMIND_PROCFILE=/Procfile \
    OVERMIND_CAN_DIE=crontab

WORKDIR $WORKDIR

# 📂 Files
COPY config/crontab config/Procfile config/Caddyfile scripts/restic.sh /

# 🛠️ Install & Setup
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates openssl tzdata iptables iputils-ping tmux sqlite3 msmtp bsd-mailx \
    # 🔐 Caddy Repo
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list \
    # 📦 Install Caddy & Restic
    && apt-get update && apt-get install -y --no-install-recommends caddy restic \
    # ⬇️ Binaries
    && curl -fsSL "$SUPERCRONIC_URL" -o /usr/local/bin/supercronic \
    && curl -fsSL "$OVERMIND_URL" | gunzip -c - > /usr/local/bin/overmind \
    # 🔗 Symlinks & Perms
    && ln -sf /usr/bin/msmtp /usr/bin/sendmail \
    && ln -sf /usr/bin/msmtp /usr/sbin/sendmail \
    && chmod +x /usr/local/bin/supercronic /usr/local/bin/overmind /restic.sh \
    # 🧹 Cleanup
    && rm -rf /var/lib/apt/lists/*

CMD ["overmind", "start"]
