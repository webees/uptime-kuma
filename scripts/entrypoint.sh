#!/bin/bash
# shellcheck disable=SC1091
set -euo pipefail

. /entrypoint/config.sh

audit_addresses
validate_smtp

mkdir -p /app/data
chown -R node:node /app
export HOME=/app

if [ "$#" -gt 0 ]; then
    exec setpriv --reuid=node --regid=node --clear-groups "$@"
fi

# dumb-init forwards TERM to the process table; the table then stops every
# group. Caddy and the scheduler restart, the application exit ends the run.
exec setpriv --reuid=node --regid=node --clear-groups \
    dumb-init -- /services.sh
