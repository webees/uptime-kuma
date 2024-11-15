FROM louislam/uptime-kuma:2.0.0-beta.0

WORKDIR /app

ARG SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.29/supercronic-linux-amd64 \
    OVERMIND_URL=https://github.com/DarthSim/overmind/releases/download/v2.4.0/overmind-v2.4.0-linux-amd64.gz

ENV TZ="Asia/Shanghai" \

    OVERMIND_CAN_DIE=crontab \
    OVERMIND_PROCFILE=/Procfile \

    SMTP_HOST=smtp.gmail.com \
    SMTP_PORT=587 \
    SMTP_USERNAME=88888888@gmail.com \
    SMTP_PASSWORD=88888888 \
    SMTP_FROM=88888888@gmail.com \
    SMTP_TO= \

    RESTIC_REPOSITORY=s3://88888888.r2.cloudflarestorage.com/uptime-kuma \
    RESTIC_PASSWORD= \
    AWS_ACCESS_KEY_ID= \
    AWS_SECRET_ACCESS_KEY=

COPY config/crontab \
     config/Procfile \
     config/Caddyfile \
     scripts/restic.sh \
     /

RUN apt update && apt install -y --no-install-recommends \
        debian-keyring \
        debian-archive-keyring \
        apt-transport-https \
        gnupg \
        curl \

        && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
        && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list \

    && apt update && apt install -y --no-install-recommends \
        caddy \
        restic \
        ca-certificates \
        openssl \
        tzdata \
        iptables \
        iputils-ping \
        tmux \
        sqlite3 \
        msmtp \
        bsd-mailx \

        && rm -rf /var/lib/apt/lists/* && apt -y autoremove  \
        && curl -fsSL "$SUPERCRONIC_URL" -o /usr/local/bin/supercronic \
        && curl -fsSL "$OVERMIND_URL" | gunzip -c - > /usr/local/bin/overmind \

        && ln -sf /usr/bin/msmtp /usr/bin/sendmail \
        && ln -sf /usr/bin/msmtp /usr/sbin/sendmail \

        && chmod +x /usr/local/bin/supercronic \
        && chmod +x /usr/local/bin/overmind \
        && chmod +x /restic.sh

CMD ["overmind", "start"]
