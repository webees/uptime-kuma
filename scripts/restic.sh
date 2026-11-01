#!/bin/bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║ Restic Backup Script                                                      ║
# ║ Usage: /restic.sh {backup|restore <id>|restore-test|snapshots|            ║
# ║                    check-freshness|test}                                  ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

set -uo pipefail

umask 077

# ── Configuration ─────────────────────────────────────────────────────────────
APP_NAME="${FLY_APP_NAME:-Uptime Kuma}"
DATA="${RESTIC_DATA_DIR:-/app/data}"
DB="$DATA/kuma.db"
DB_BAK="$DATA/backup.bak"
LOG_DIR="${RESTIC_LOG_DIR:-/var/log/restic}"
LOG="$LOG_DIR/$(date +%Y%m%d_%H%M%S).log"
STATUS_FILE="$DATA/backup-status.txt"
MAX_STATUS_BYTES=4096
MAX_LOG_BYTES="${RESTIC_LOG_MAX_BYTES:-262144}"
MAX_LOG_DIR_BYTES="${RESTIC_LOG_DIR_MAX_BYTES:-10485760}"
QUICK_TIMEOUT="${RESTIC_QUICK_TIMEOUT:-60}"
LONG_TIMEOUT="${RESTIC_LONG_TIMEOUT:-1800}"
LIMIT_LOG_BYTES=8388608
LIMIT_LOG_DIR_BYTES=67108864
LIMIT_QUICK_TIMEOUT=3600
LIMIT_LONG_TIMEOUT=86400
STALE_HOURS="${RESTIC_STALE_HOURS:-26}"
LIMIT_STALE_HOURS=8760
LAST_SUCCESS=0
CURRENT_STEP=""

# ANSI colors
R="\x1b[31;01m" G="\x1b[32;01m" Y="\x1b[33;01m" B="\x1b[34;01m" X="\x1b[0m"

# ── Utilities ─────────────────────────────────────────────────────────────────

# ── Bounded diagnostics ───────────────────────────────────────────────────────

# A positive whole number inside the documented range, else fail closed.
require_bounded_int() {
    local name="$1" value="${2:-}" maximum="$3"
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]] || [ "${#value}" -gt "${#maximum}" ] \
        || [ "$value" -gt "$maximum" ]; then
        echo "Error: ${name} must be a positive integer up to ${maximum}, got '${value}'" >&2
        return 2
    fi
}

# Replace the log atomically and keep it inside its byte cap. Nothing may grow
# unbounded in memory first: the old log is read through `tail -c` as well.
write_log_entry() {
    local body="$1" tmp
    tmp="$(mktemp "${LOG}.tmp.XXXXXX")" || return 1
    { [ ! -f "$LOG" ] || tail -c "$MAX_LOG_BYTES" "$LOG"; printf '%s\n' "$body"; } \
        | tail -c "$MAX_LOG_BYTES" > "$tmp" && mv -f "$tmp" "$LOG" && printf '%s\n' "$body" && return 0
    rm -f "$tmp"
    echo "Error: cannot write bounded output to ${LOG}" >&2
    return 1
}

# Capture bounded, persist bounded, and never downgrade a failure to a warning.
log() {
    local body rc
    body="$( { "$@" 2>&1 | tail -c "$MAX_LOG_BYTES"; } )"; rc=$?
    [ "$rc" -eq 0 ] || { echo "Error: cannot capture bounded log output" >&2; exit 2; }
    [ -n "$body" ] || return 0
    write_log_entry "$body" || exit 2
}

log_safe() { ( log "$@" ) || :; }

# Enforce both caps and the age policy; a directory that cannot be maintained
# refuses new work instead of silently growing.
trim_logs() {
    local file size tmp total failed=0
    find "$LOG_DIR" -maxdepth 1 -type f \( -name '*.log' -o -name '*.log.tmp.*' \) -mmin +600 ! -path "$LOG" -delete \
        || { echo "Warning: could not remove expired logs from ${LOG_DIR}" >&2; failed=1; }
    for file in "$LOG_DIR"/*.log; do
        [ -f "$file" ] || continue
        if ! size="$(wc -c < "$file")"; then
            echo "Warning: cannot measure ${file}" >&2
            failed=1
            continue
        fi
        [ "$size" -le "$MAX_LOG_BYTES" ] && continue
        tmp="${file}.tmp.$$"
        if tail -c "$MAX_LOG_BYTES" "$file" > "$tmp" && mv -f "$tmp" "$file"; then
            echo "Trimmed ${file} to ${MAX_LOG_BYTES} bytes" >&2
        else
            rm -f "$tmp"
            echo "Warning: cannot trim ${file}" >&2
            failed=1
        fi
    done
    total="$(find "$LOG_DIR" -maxdepth 1 -type f -name '*.log' -exec wc -c {} + 2>/dev/null | tail -n 1 | awk '{ print $1 }')" \
        || { echo "Warning: cannot measure ${LOG_DIR}" >&2; failed=1; }
    if [ "${total:-0}" -gt "$MAX_LOG_DIR_BYTES" ]; then
        while [ "${total:-0}" -gt "$MAX_LOG_DIR_BYTES" ]; do
            file="$(find "$LOG_DIR" -maxdepth 1 -type f -name '*.log' ! -path "$LOG" -print0 2>/dev/null \
                | xargs -0 ls -tr 2>/dev/null | head -n 1)"
            [ -n "$file" ] || { echo "Warning: no removable log left in ${LOG_DIR}" >&2; failed=1; break; }
            size="$(wc -c < "$file")" || { failed=1; break; }
            rm -f "$file" || { failed=1; break; }
            total=$((total - size))
        done
    fi
    return "$failed"
}

# ── Bounded execution ────────────────────────────────────────────────────────

# timeout(1) with a SIGKILL grace period: a child that ignores SIGTERM cannot
# hang the run. Absence of timeout(1) fails closed rather than running unbounded.
timed() {
    local limit="$1"
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout -k 10 "$limit" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then
        gtimeout -k 10 "$limit" "$@"
    else
        echo "Error: no timeout(1) available for a bounded command" >&2
        return 127
    fi
}

# ── State ────────────────────────────────────────────────────────────────────

# Atomic: write a sibling temp file and rename, so a failure keeps the previous
# marker instead of truncating it.
write_status() {
    local state="$1" message="${2:-}" body tmp="${STATUS_FILE}.tmp.$$"
    printf -v body 'state=%s\nupdated_at=%s\nlast_success=%s\nmessage=%s\n' \
        "$state" "$(date -u +%s)" "$LAST_SUCCESS" "$message"
    if ! [ -d "$STATUS_FILE" ] && printf '%s' "$body" > "$tmp" && mv "$tmp" "$STATUS_FILE"; then
        return 0
    fi
    rm -f "$tmp" || echo "Warning: cannot remove ${tmp}" >&2
    echo "Error: cannot write ${STATUS_FILE}; backup state was not recorded" >&2
    return 1
}

# Only the current state file is authoritative, and only the lock holder may
# publish `running`.
is_regular_file() { [ ! -L "$1" ] && [ -f "$1" ]; }

read_status_body() {
    local body
    is_regular_file "$STATUS_FILE" || return 4
    body="$( { head -c "$((MAX_STATUS_BYTES + 1))" "$STATUS_FILE" 2>/dev/null && printf '\001'; } )" || return 1
    body="${body%$'\001'}"
    [ "${#body}" -le "$MAX_STATUS_BYTES" ] || return 3
    printf '%s' "$body"
}

load_status() {
    local body
    [ -e "$STATUS_FILE" ] || return 0
    if ! body="$(read_status_body)"; then
        case "$?" in
            3) echo "Warning: ${STATUS_FILE} exceeds ${MAX_STATUS_BYTES} bytes" >&2 ;;
            4) echo "Warning: ${STATUS_FILE} is not a regular file" >&2 ;;
            *) echo "Warning: ${STATUS_FILE} is unreadable" >&2 ;;
        esac
        return 0
    fi
    LAST_SUCCESS="$(printf '%s\n' "$body" | sed -n 's/^last_success=//p' | head -n 1)"
    if [[ ! "$LAST_SUCCESS" =~ ^[0-9]+$ ]] || [ "${#LAST_SUCCESS}" -gt 18 ]; then
        LAST_SUCCESS=0
    else
        LAST_SUCCESS=$((10#$LAST_SUCCESS))
    fi
}

# A run can succeed forever while the backup age silently grows (a skipped
# schedule, a rotated status file, a clock jump). The age is derived from the
# state, so no extra marker is needed: every run while stale warns and mails,
# and one fresh success clears it.
stale_backup() {
    local now age
    # No history yet (first ever run) is not stale.
    [ "${LAST_SUCCESS:-0}" -eq 0 ] && return 0
    now="$(date -u +%s)"
    [ "${now:-0}" -ge "$LAST_SUCCESS" ] || return 0
    age=$((now - LAST_SUCCESS))
    [ "$age" -ge $((STALE_HOURS * 3600)) ] || return 0
    echo "Warning: the last successful backup is older than ${STALE_HOURS}h" >&2
    mail "Backup is stale" || :
}

# The advisory above only fires when something else runs, so a dead scheduler
# stays silent by definition. `check-freshness` is its own scheduled gate: it
# exits non-zero once the last success is too old, which the EXIT trap records
# and which makes the missing hour visible. It never stamps `last_success`, so
# a passing check cannot launder a stale backup into a fresh one.
check_freshness() {
    local now age limit=$((STALE_HOURS * 3600))
    if [ "${LAST_SUCCESS:-0}" -eq 0 ]; then
        echo "No successful backup recorded yet"
        return 0
    fi
    now="$(date -u +%s)"
    if [ "${now:-0}" -lt "$LAST_SUCCESS" ]; then
        echo "Warning: last_success is in the future; the clock is behind" >&2
        return 0
    fi
    age=$((now - LAST_SUCCESS))
    if [ "$age" -lt "$limit" ]; then
        echo "Last successful backup was ${age}s ago (limit ${limit}s)"
        return 0
    fi
    echo "Error: no successful backup for ${age}s (limit ${limit}s)" >&2
    mail "Backup is stale (${age}s)" || :
    return 1
}

# Send email notification
mail() {
    [ -z "${SMTP_TO:-}" ] && return
    # Strip ANSI colors and set UTF-8
    sed 's/\x1b\[[0-9;]*m//g' "$LOG" | command mail \
        -a "Content-Type: text/plain; charset=UTF-8" \
        -s "[$APP_NAME] $1" "$SMTP_TO"
}

# "<label>" <limit> <command...>; bounded, logged, alerted, never exits.
run() {
    local label="$1" limit="$2" out rc=0
    shift 2
    log echo -en "${B}${label}${X} "
    out="$( { timed "$limit" "$@" 2>&1 | tail -c "$MAX_LOG_BYTES"; } )" || rc=$?
    if [ "$rc" -eq 0 ]; then
        log echo -e "${G}✓${X}"
        return 0
    fi
    log echo -e "${R}✗${X}"
    [ -z "$out" ] || log echo "$out"
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

    printf '%s\n' \
        defaults \
        'auth on' \
        'tls on' \
        'tls_trust_file /etc/ssl/certs/ca-certificates.crt' \
        'logfile /var/log/msmtp.log' \
        'account default' \
        "host ${SMTP_HOST}" \
        "port ${SMTP_PORT:-587}" \
        "from ${SMTP_FROM}" \
        "user ${SMTP_USERNAME}" \
        "password ${SMTP_PASSWORD}" > /etc/msmtprc
}

# Consistent SQLite copy: .backup never reads a half-written page, and the copy
# is integrity-checked before the live files are excluded from the snapshot.
sqlite_check() {
    local label="$1" file="$2" out rc=0
    log echo -en "${B}${label}${X} "
    out="$( { timed "$QUICK_TIMEOUT" sqlite3 "$file" 'PRAGMA integrity_check' 2>&1 | tail -c "$MAX_LOG_BYTES"; } )" || rc=$?
    # Exactly one `ok` line: a partial or chatty result is not a clean bill.
    if [ "$rc" -eq 0 ] && [ "$(printf '%s\n' "$out" | sed '/^$/d')" = ok ]; then
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
    if ! run "SQLite backup" "$QUICK_TIMEOUT" sqlite3 "$DB" ".backup $DB_BAK"; then
        rm -f -- "$DB_BAK"
        return 1
    fi
    if ! sqlite_check "SQLite check" "$DB_BAK"; then
        rm -f -- "$DB_BAK"
        return 1
    fi
}

# ── Business Logic ────────────────────────────────────────────────────────────

# Backup, check and prune. Only the lock holder may publish `running`, and a
# failed run never records a new success time.
backup() {
    if ! exec 9>"$LOG_DIR/backup.lock"; then
        echo "Error: cannot create ${LOG_DIR}/backup.lock" >&2
        CURRENT_STEP="Cannot create the backup lock"
        exit 2
    fi
    if ! flock -n 9; then
        log echo "Another backup is still running, skipping this run"
        exit 3
    fi
    CURRENT_STEP="Cannot record the backup start state"
    if ! write_status running "Backup started"; then exit 2; fi

    # Sync time for S3 requests
    timed "$QUICK_TIMEOUT" ntpdate -u pool.ntp.org 2>/dev/null \
        || log echo -e "${Y}NTP sync skipped${X}"

    # An unlock failure is reported instead of swallowed: a stale lock is the
    # difference between one skipped run and a permanent outage.
    CURRENT_STEP="Repository unlock"
    run "Repository unlock" "$QUICK_TIMEOUT" restic unlock || true

    CURRENT_STEP="Repository access"
    if ! timed "$QUICK_TIMEOUT" restic cat config >/dev/null 2>&1; then
        log echo -e "${Y}Repository not initialized, initializing...${X}"
        must "Repo init" "$QUICK_TIMEOUT" restic init
    fi

    # Core operations
    sqlite_snapshot || exit 2
    must "Data backup" "$LONG_TIMEOUT" restic backup --verbose \
        --exclude="$DB" --exclude="$DB-journal" --exclude="$DB-wal" --exclude="$DB-shm" \
        "$DATA"
    must "Health check" "$LONG_TIMEOUT" restic check
    must "Snapshot prune" "$LONG_TIMEOUT" restic forget \
        --keep-daily 7 --keep-weekly 4 --keep-monthly 3 --keep-yearly 3 --prune

    # Limits are re-enforced after the run: this is the only point where the
    # log of this run is known to be complete, so a failure here must not be
    # downgraded to a warning that later looks like success.
    CURRENT_STEP="Log limits cannot be enforced after the backup"
    if ! trim_logs; then
        echo "Error: cannot enforce log limits after the backup" >&2
        exit 2
    fi

    CURRENT_STEP="Cannot record the backup completion state"
    LAST_SUCCESS="$(date -u +%s)"
    write_status ok "Backup complete" || exit 2
    log echo -e "${G}Backup process complete ✨${X}"
}

# Restore drill: an isolated scratch directory, never `/app/data`. Snapshots
# carry the checked `backup.bak` copy, never the live database, so the drill
# validates exactly that file.
restore_test() {
    local target="${RESTORE_SCRATCH:-/tmp/restic-restore-test}" restored available needed
    rm -rf -- "$target" || { echo "Error: cannot clear ${target}" >&2; return 2; }
    mkdir -p -- "$target" || { echo "Error: cannot create ${target}" >&2; return 2; }
    needed="$(restic snapshots --json 2>/dev/null | wc -c)"
    available="$(df -k --output=avail "$target" 2>/dev/null | tail -n 1 | tr -d ' ')"
    if [ -n "$available" ] && [ -n "$needed" ] && [ "$available" -lt $((needed * 2 / 1024 + 1024)) ]; then
        echo "Error: ${target} has too little free space" >&2
        return 2
    fi
    must "Restore drill from latest" "$LONG_TIMEOUT" restic restore latest --target "$target"
    restored="$target$DB_BAK"
    if [ ! -f "$restored" ]; then
        echo "Error: the restored snapshot has no ${restored}" >&2
        return 2
    fi
    sqlite_check "Restored database check" "$restored" || return 2
    verify_restored_app "$restored" "$target" || return 2
    echo "Restore drill complete: ${restored} starts Uptime Kuma and answers /"
}

# Stop the isolated application without leaving a process behind. The app can
# ignore TERM while closing SQLite, so bound the grace period and then kill.
stop_restored_app() {
    local pid="$1"
    kill -TERM "$pid" 2>/dev/null || :
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1
    done
    kill -KILL "$pid" 2>/dev/null || :
    wait "$pid" 2>/dev/null || :
}

# Prove the restored database can boot the application, not merely pass
# integrity_check. The copy is isolated and every monitor is deactivated before
# launch so the drill cannot execute checks or emit notifications from restored
# production state. A 302 from / proves the application loaded an initialized
# database; a setup page or a process exit fails the drill.
verify_restored_app() {
    local restored="$1" target="$2"
    local app_dir="$target/app-check"
    local app_db="$app_dir/kuma.db" log="$target/app-check.log"
    local port=3012 pid="" code="" rc=0
    rm -rf -- "$app_dir" "$log" || {
        echo "Error: cannot clear the isolated app check" >&2
        return 2
    }
    mkdir -p -- "$app_dir" || {
        echo "Error: cannot create the isolated app data directory" >&2
        return 2
    }
    cp -- "$restored" "$app_db" || {
        echo "Error: cannot copy the restored database for the app check" >&2
        return 2
    }
    if ! sqlite3 "$app_db" 'UPDATE monitor SET active = 0;' >/dev/null 2>&1; then
        echo "Error: restored database cannot deactivate monitors for a safe boot" >&2
        rm -rf -- "$app_dir"
        return 2
    fi

    (
        cd /app || exit 2
        exec env DATA_DIR="$app_dir" node server/server.js --port="$port"
    ) >"$log" 2>&1 &
    pid=$!
    for _ in $(seq 1 60); do
        if ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid" 2>/dev/null || rc=$?
            echo "Error: restored application exited before answering / (exit ${rc})" >&2
            rm -rf -- "$app_dir" "$log"
            return 2
        fi
        code="$(curl --silent --show-error --max-time 1 --output /dev/null \
            --write-out '%{http_code}' "http://127.0.0.1:${port}/" 2>/dev/null)" || code=000
        if [ "$code" = 302 ]; then
            stop_restored_app "$pid"
            rm -rf -- "$app_dir" "$log"
            echo "Restore app check: / returned HTTP 302"
            return 0
        fi
        sleep 0.5
    done
    echo "Error: restored application did not answer / with 302 within 30s" >&2
    stop_restored_app "$pid"
    rm -rf -- "$app_dir" "$log"
    return 2
}

# In-place restore: explicit confirmation, a regular live file, and a staging
# directory so a bad snapshot never touches the live database. The live
# database is copied to `kuma.db.previous` first and survives the swap, so the
# previous state stays restorable until the next success is proven.
restore() {
    local id="${1:-}" stage="$DATA/.restore" restored previous="$DB.previous"
    [ -n "$id" ] || { echo "Usage: $0 restore <snapshot-id>" >&2; exit 1; }
    [ "${RESTORE_CONFIRM:-}" = yes ] \
        || { echo "Error: set RESTORE_CONFIRM=yes to replace live data" >&2; exit 1; }
    if [ -L "$DB" ] || { [ -e "$DB" ] && [ ! -f "$DB" ]; }; then
        echo "Error: ${DB} is not a regular file; refusing to restore over it" >&2
        exit 2
    fi

    rm -rf -- "$stage" || { echo "Error: cannot clear ${stage}" >&2; exit 2; }
    mkdir -p -- "$stage" || { echo "Error: cannot create ${stage}" >&2; exit 2; }
    must "Restore snapshot ${id}" "$LONG_TIMEOUT" restic restore "$id" --target "$stage"
    restored="$stage$DB_BAK"
    if [ ! -f "$restored" ]; then
        echo "Error: snapshot ${id} has no checked ${restored}" >&2
        exit 2
    fi
    sqlite_check "Restored database check" "$restored" || exit 2

    rm -f -- "$previous"
    # A consistent copy of the live state, not a raw file copy: the app may
    # still be writing while the snapshot is verified.
    if [ -f "$DB" ]; then
        must "Keep the rollback copy" "$QUICK_TIMEOUT" sqlite3 "$DB" ".backup $previous" || exit 2
    fi
    # Sidecars from the previous database would otherwise replay over the new
    # file on the next start.
    rm -f -- "$DB-journal" "$DB-wal" "$DB-shm"
    mv -f -- "$restored" "$DB" || { echo "Error: cannot replace ${DB}" >&2; exit 2; }
    rm -rf -- "$stage"
    log echo -e "${G}Restore complete: restart the app to load ${DB} (rollback copy: ${previous})${X}"
}

# ── Entry Point ───────────────────────────────────────────────────────────────

main() {
    local command="${1:-}"

    require_bounded_int RESTIC_LOG_MAX_BYTES "$MAX_LOG_BYTES" "$LIMIT_LOG_BYTES" || exit 2
    require_bounded_int RESTIC_LOG_DIR_MAX_BYTES "$MAX_LOG_DIR_BYTES" "$LIMIT_LOG_DIR_BYTES" || exit 2
    require_bounded_int RESTIC_QUICK_TIMEOUT "$QUICK_TIMEOUT" "$LIMIT_QUICK_TIMEOUT" || exit 2
    require_bounded_int RESTIC_LONG_TIMEOUT "$LONG_TIMEOUT" "$LIMIT_LONG_TIMEOUT" || exit 2
    require_bounded_int RESTIC_STALE_HOURS "$STALE_HOURS" "$LIMIT_STALE_HOURS" || exit 2

    mkdir -p "$LOG_DIR" || { echo "Error: cannot create ${LOG_DIR}" >&2; exit 2; }
    # Refuse new work when the log directory cannot be kept bounded.
    trim_logs || { echo "Error: cannot enforce log limits; refusing to create a new log" >&2; exit 2; }
    if [ "$command" != backup ]; then
        : >> "$LOG" || { echo "Error: cannot create ${LOG}" >&2; exit 2; }
    fi

    if [ -z "${RESTIC_PASSWORD:-}" ]; then
        case "$command" in
            backup) write_status failed "RESTIC_PASSWORD not set" || : ;;
        esac
        echo "Error: RESTIC_PASSWORD not set" >&2
        exit 1
    fi

    load_status
    # A failed run records the reason and never a new success time. Exit 3 is a
    # deliberate skip, which must not look like a failure.
    # shellcheck disable=SC2016
    trap 'rc=$?; if [ "$rc" -ne 0 ] && [ "$rc" -ne 3 ]; then
        write_status failed "${CURRENT_STEP:-Task failed} (exit ${rc})" || :
    fi' EXIT
    initCfg
    # After initCfg so the mailer has its configuration when this warns. The
    # freshness gate mails on its own, so the advisory would only duplicate.
    [ "$command" = check-freshness ] || stale_backup

    case "$command" in
        backup)      backup ;;
        restore)     restore "${2:-}" ;;
        restore-test) restore_test ;;
        snapshots)   restic snapshots ;;
        check-freshness) check_freshness || exit 2 ;;
        test)
            echo "Test email from $APP_NAME backup system." \
                | command mail -s "[$APP_NAME] Test Mail" "$SMTP_TO" \
                && echo "Test mail sent to $SMTP_TO"
            ;;
        *)
            echo "Usage: $0 {backup|restore <id>|restore-test|snapshots|check-freshness|test}" >&2
            exit 1
            ;;
    esac
}

[ "${RESTIC_LIB_ONLY:-0}" = 1 ] || main "$@"
