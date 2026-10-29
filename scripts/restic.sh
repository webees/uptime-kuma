#!/bin/bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║ Restic Backup Script                                                      ║
# ║ Usage: /restic.sh {backup|restore <id>|snapshots|test}                    ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

set -o pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
APP_NAME="${FLY_APP_NAME:-Uptime Kuma}"
DATA="${RESTIC_DATA_DIR:-/app/data}"
DB="$DATA/kuma.db"
DB_BAK="$DATA/backup.bak"
LOG_DIR="${RESTIC_LOG_DIR:-/var/log/restic}"
LOG="$LOG_DIR/$(date +%Y%m%d_%H%M%S).log"

# ANSI colors
R="\x1b[31;01m" G="\x1b[32;01m" Y="\x1b[33;01m" B="\x1b[34;01m" X="\x1b[0m"

# ── Utilities ─────────────────────────────────────────────────────────────────

# Log output to file and stdout
log() { "$@" 2>&1 | tee -a "$LOG"; }

# Send email notification
mail() {
    [ -z "${SMTP_TO:-}" ] && return
    # Strip ANSI colors and set UTF-8
    sed 's/\x1b\[[0-9;]*m//g' "$LOG" | command mail \
        -a "Content-Type: text/plain; charset=UTF-8" \
        -s "[$APP_NAME] $1" "$SMTP_TO"
}

# Run "<label>" <command...>; log and alert on failure, never exit.
run() {
    local label="$1"
    log echo -en "${B}$label${X} "
    shift
    local out rc=0
    out=$("$@" 2>&1) || rc=$?
    if [ "$rc" -eq 0 ]; then
        log echo -e "${G}✓${X}"
        return 0
    fi
    log echo -e "${R}✗${X}"
    log echo "$out"
    mail "Task Failed: $label"
    return "$rc"
}

# run() plus abort, for steps with nothing to clean up.
must() {
    run "$@" || exit 2
}

# Initialize SMTP configuration
initCfg() {
    [ -z "${SMTP_TO:-}" ] && return
    [ -z "${SMTP_HOST:-}" ] && return

    cat > /etc/msmtprc << EOF
defaults
auth on
tls on
tls_trust_file /etc/ssl/certs/ca-certificates.crt
logfile /var/log/msmtp.log
account default
host $SMTP_HOST
port ${SMTP_PORT:-587}
from $SMTP_FROM
user $SMTP_USERNAME
password $SMTP_PASSWORD
EOF
}

# Consistent SQLite copy: .backup never reads a half-written page, and the copy
# is integrity-checked before the live files are excluded from the snapshot.
sqlite_check() {
    local label="$1" file="$2" out rc=0
    log echo -en "${B}${label}${X} "
    out="$(sqlite3 "$file" "PRAGMA integrity_check" 2>&1)" || rc=$?
    if [ "$rc" -eq 0 ] && [ "$out" = ok ]; then
        log echo -e "${G}✓${X}"
        return 0
    fi
    log echo -e "${R}✗${X}"
    log echo "${out:-PRAGMA integrity_check failed}"
    mail "Task Failed: ${label}"
    return 1
}

sqlite_snapshot() {
    rm -f -- "$DB_BAK"
    if ! run "SQLite backup" sqlite3 "$DB" ".backup $DB_BAK"; then
        rm -f -- "$DB_BAK"
        return 1
    fi
    if ! sqlite_check "SQLite check" "$DB_BAK"; then
        rm -f -- "$DB_BAK"
        return 1
    fi
}

# ── Business Logic ────────────────────────────────────────────────────────────

# Backup, check and prune
backup() {
    # Sync time for S3 requests
    ntpdate -u pool.ntp.org 2>/dev/null || log echo -e "${Y}NTP sync skipped${X}"

    # Unlock repository
    restic unlock 2>/dev/null || true

    # Initialize repository if needed
    if ! restic cat config >/dev/null 2>&1; then
        log echo -e "${Y}Repository not initialized, initializing...${X}"
        must "Repo init" restic init
    fi

    # Core operations
    sqlite_snapshot || exit 2
    must "Data backup" restic backup --verbose \
        --exclude="$DB" --exclude="$DB-journal" --exclude="$DB-wal" --exclude="$DB-shm" \
        "$DATA"
    must "Health check" restic check
    must "Snapshot prune" restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 3 --keep-yearly 3 --prune

    # Cleanup logs older than 10 hours
    find "$LOG_DIR" -name "*.log" -type f -mmin +600 -delete

    log echo -e "${G}Backup process complete ✨${X}"
}

# Restore data from snapshot
restore() {
    [ -z "${1:-}" ] && { echo "Usage: $0 restore <snapshot-id>"; exit 1; }
    must "Restore snapshot $1" restic restore "$1" --target /
}

# ── Entry Point ───────────────────────────────────────────────────────────────

main() {
    mkdir -p "$LOG_DIR"

    [ -z "${RESTIC_PASSWORD:-}" ] && { echo "Error: RESTIC_PASSWORD not set"; exit 1; }

    initCfg

    case "${1:-}" in
        backup)    backup ;;
        restore)   restore "${2:-}" ;;
        snapshots) restic snapshots ;;
        test)      echo "Test email from $APP_NAME backup system." | command mail -s "[$APP_NAME] Test Mail" "$SMTP_TO" && echo "Test mail sent to $SMTP_TO" ;;
        *)         echo "Usage: $0 {backup|restore <id>|snapshots|test}"; exit 1 ;;
    esac
}

[ "${RESTIC_LIB_ONLY:-0}" = 1 ] || main "$@"
