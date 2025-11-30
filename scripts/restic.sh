#!/bin/bash
set -euo pipefail

# 🔧 Config
LOG_FILE="/var/log/restic/$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$(dirname "$LOG_FILE")"

# 🎨 Helpers
log() { echo -e "[$1] $2" | tee -a "$LOG_FILE"; }
info() { log "ℹ️" "$1"; }
ok() { log "✅" "$1"; }
err() { log "❌" "$1"; [ -n "${SMTP_TO:-}" ] && echo "$1" | mail -s "[Restic] Error" "$SMTP_TO"; exit 1; }

# 🚀 Init
[ -z "${RESTIC_PASSWORD:-}" ] && err "RESTIC_PASSWORD missing!"
if [ -n "${SMTP_TO:-}" ]; then
    echo -e "defaults\nauth on\ntls on\ntls_trust_file /etc/ssl/certs/ca-certificates.crt\nlogfile /var/log/msmtp.log\naccount default\nhost $SMTP_HOST\nport $SMTP_PORT\nfrom $SMTP_FROM\nuser $SMTP_USERNAME\npassword $SMTP_PASSWORD" > /etc/msmtprc
fi

# 🛠️ Commands
cmd_backup() {
    info "Starting Backup..."
    
    info "📦 SQLite..."
    sqlite3 /app/data/db.sqlite3 ".backup /app/data/backup.bak" || err "SQLite fail"
    
    info "💾 Restic..."
    restic cat config >/dev/null 2>&1 || restic init || err "Init fail"
    restic backup --verbose --exclude='db.*' /app/data || err "Backup fail"
    
    info "🧹 Pruning..."
    restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 3 --keep-yearly 3 --prune || err "Prune fail"
    
    ok "Done!"
}

cmd_restore() {
    [ -z "${1:-}" ] && err "Usage: restore <id>"
    info "♻️ Restoring $1..."
    restic restore "$1" --target / || err "Restore fail"
    ok "Restored!"
}

# 🎮 Main
case "${1:-}" in
    backup) cmd_backup ;;
    restore) cmd_restore "${2:-}" ;;
    snapshots) restic snapshots ;;
    *) echo "Usage: $0 {backup|restore|snapshots}"; exit 1 ;;
esac
