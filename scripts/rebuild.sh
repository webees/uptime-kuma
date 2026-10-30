#!/bin/bash
# Scheduled-rebuild probe: rebuild.sh count <image>
#
# A weekly cron that rebuilds unconditionally spends a full build and a deploy
# on an image that is already current. The published image is probed instead;
# only a positive, well-formed upgrade count lets the scheduled run continue.
set -euo pipefail

readonly DOCKER_TIMEOUT="${DEPS_DOCKER_TIMEOUT:-300}"

fail() { echo "$*" >&2; exit 1; }
usage_error() { echo "$*" >&2; exit 2; }

validate_timeout() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]] || fail "$2 must be a positive integer number of seconds"
}

docker_bounded() {
    validate_timeout "$DOCKER_TIMEOUT" DEPS_DOCKER_TIMEOUT
    if command -v timeout >/dev/null 2>&1; then
        timeout -k 5 "$DOCKER_TIMEOUT" docker "$@"
    elif command -v gtimeout >/dev/null 2>&1; then
        gtimeout -k 5 "$DOCKER_TIMEOUT" docker "$@"
    else
        fail "docker requires timeout or gtimeout to enforce DEPS_DOCKER_TIMEOUT"
    fi
}

count() {
    [ "$#" -eq 1 ] || usage_error "usage: rebuild.sh count <image>"
    local image="$1" rows count
    [ -n "$image" ] || usage_error "usage: rebuild.sh count <image>"
    # `apt list --upgradable` writes its banner to stderr and prints
    # `<name>/<suite> <version> <arch> [upgradable from: <version>]` per hit, so
    # only lines with that shape are counted; a failed update aborts the probe
    # rather than reporting a clean zero.
    # `apt-get update` needs root, but the image entrypoint drops to `node`
    # before it runs a command, so the entrypoint is bypassed here. Boot proof
    # stays the job of the runtime smoke gate.
    rows="$(docker_bounded run --rm --entrypoint bash "$image" -c '
        set -e
        apt-get update -qq >/dev/null 2>&1 || { echo "apt-get update failed" >&2; exit 1; }
        apt list --upgradable 2>/dev/null | sed "1d"
    ')" || fail "package probe failed for ${image}"
    rows="$(printf '%s\n' "$rows" | awk 'NF >= 2 && $1 ~ /\// {print $1, $2}')"
    count="$(printf '%s\n' "$rows" | grep -c . || true)"
    [[ "$count" =~ ^[0-9]+$ ]] || fail "package count is missing or malformed"
    printf 'upgradable packages: %s\n' "$count"
    [ "$count" -eq 0 ] || printf '%s\n' "$rows"
}

case "${1:-}" in
    count) shift; count "$@" ;;
    *) usage_error "usage: rebuild.sh count <image>" ;;
esac
