#!/bin/bash
# Process table: Caddy and the scheduler restart, the app does not. When
# uptime-kuma exits the container stops, so Fly owns machine recovery and the
# proxy can never outlive a dead application.
set -euo pipefail

if [ "$(id -u)" = "0" ]; then
    echo "services.sh must run as the unprivileged service user" >&2
    exit 2
fi

delay="${SUPERVISE_RESTART_DELAY:-5}"
supervisors=()

if [[ ! "$delay" =~ ^(0|[1-9][0-9]*|0\.[0-9]*[1-9][0-9]*|[1-9][0-9]*\.[0-9]+)$ ]] \
    || [ "$delay" = 0 ]; then
    echo "SUPERVISE_RESTART_DELAY must be a positive number, got '${delay}'" >&2
    exit 2
fi

# One supervisor per process. The child runs in its own session so a timeout or
# a crash takes its whole process group with it instead of leaking helpers.
supervise() {
    local name="$1" restart="$2" child status
    shift 2
    child=""
    trap 'if [ -n "$child" ]; then
        kill -TERM -"$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null || :
    fi
    exit 0' INT TERM

    while :; do
        setsid "$@" &
        child=$!
        if wait "$child"; then status=0; else status=$?; fi
        child=""
        if [ "$restart" != "true" ]; then
            return "$status"
        fi
        echo "[${name}] exited with status ${status}, restarting in ${delay}s" >&2
        sleep "$delay" &
        child=$!
        wait "$child" || return $?
        child=""
    done
}

stop_services() {
    local pid
    trap - INT TERM
    for pid in "${supervisors[@]}"; do
        kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || :
    done
    for pid in "${supervisors[@]}"; do
        wait "$pid" 2>/dev/null || :
    done
}

supervise caddy true caddy run --config /Caddyfile &
supervisors+=("$!")
supervise crontab true supercronic /crontab &
supervisors+=("$!")
supervise uptime-kuma false sh -c 'cd /app && exec node server/server.js --port=3001' &
supervisors+=("$!")

trap 'stop_services; exit 0' INT TERM
if wait -n "${supervisors[@]}"; then
    status=0
else
    status=$?
fi
stop_services
exit "$status"
