#!/bin/bash

is_numeric_host() {
    local label
    local -a labels=()
    IFS=. read -r -a labels <<< "$1"
    [ "${#labels[@]}" -gt 0 ] || return 1
    for label in "${labels[@]}"; do
        [[ "$label" =~ ^[0-9]+$ || "$label" =~ ^0[xX][0-9A-Fa-f]+$ ]] || return 1
    done
}

valid_dns_name() {
    local name="$1"
    [ "${#name}" -le 253 ] \
        && ! is_numeric_host "$name" \
        && [[ "$name" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]]
}

valid_host_port() {
    local authority="$1" host port
    case "$authority" in
        '' | *@* | *//* | *[[:space:][:cntrl:]\\?#%]*) return 1 ;;
    esac
    host="$authority" port=""
    if [[ "$authority" == *:* ]]; then
        host="${authority%:*}" port="${authority##*:}"
        [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && [ "$port" -le 65535 ] || return 1
    fi
    valid_dns_name "$host"
}

validate_https_url() {
    local value="$1" authority rest
    [ "${#value}" -le 2048 ] || { echo "Error: HTTPS URL is too long" >&2; return 2; }
    case "$value" in
        *[[:space:][:cntrl:]]*) echo "Error: HTTPS URL must not contain whitespace or control characters" >&2; return 2 ;;
        https://?*) rest="${value#https://}" ;;
        *) echo "Error: HTTPS URL must start with https://" >&2; return 2 ;;
    esac
    authority="${rest%%/*}"
    valid_host_port "$authority" || { echo "Error: HTTPS URL has an invalid host or port" >&2; return 2; }
    [ "${#rest}" -eq "${#authority}" ] && return 0
    case "${rest#*/}" in
        '' | *//* | *'/../'* | ../* | */.. | *'/./'* | */.* | *[\\?#%]*) 
            echo "Error: HTTPS URL has an unsafe or ambiguous path" >&2
            return 2
            ;;
    esac
}

# Restic targets are an absolute path or an unambiguous `s3://host/bucket/path`.
validate_restic_repository() {
    local repository="${RESTIC_REPOSITORY:-}" authority host path
    [ -n "$repository" ] || return 0
    case "$repository" in
        *[[:space:][:cntrl:]]*) echo "Error: RESTIC_REPOSITORY must not contain whitespace" >&2; return 2 ;;
        /*)
            [ "${#repository}" -le 2048 ] || { echo "Error: RESTIC_REPOSITORY is too long" >&2; return 2; }
            case "$repository" in / | *'/../'* | ../* | */.. | *'//'* | *[\\?#%]*) return 2 ;; esac
            return 0
            ;;
        s3://?*) ;;
        *) echo "Error: RESTIC_REPOSITORY must be an absolute path or an s3:// URI" >&2; return 2 ;;
    esac
    [ "${#repository}" -le 2048 ] || { echo "Error: RESTIC_REPOSITORY is too long" >&2; return 2; }
    authority="${repository#s3://}"
    case "$authority" in *@*) echo "Error: RESTIC_REPOSITORY must not contain userinfo" >&2; return 2 ;; esac
    host="${authority%%/*}" path="${authority#*/}"
    if [ "$path" = "$authority" ] || [ -z "$path" ] || ! valid_host_port "$host"; then
        echo "Error: RESTIC_REPOSITORY has an invalid host or an empty path" >&2
        return 2
    fi
    case "$path" in
        /* | *//* | *'/../'* | ../* | */.. | *[\\?#%@]*) echo "Error: RESTIC_REPOSITORY has an unsafe path" >&2; return 2 ;;
    esac
}

# One host rule for the whole repository. Caddy cannot start with an empty but
# set CADDY_DOMAINS (`host` adapts to null), so an empty value is unset here and
# both the default and the "allow all" path stay a single matcher in Caddy.
normalize_caddy_domains() {
    local item index seen=' ' IFS=' '
    local -a domains=()
    CADDY_DOMAINS="$(printf '%s' "${CADDY_DOMAINS:-}" | tr -d '\r')"
    [ -n "${CADDY_DOMAINS//[[:space:]]/}" ] || { unset CADDY_DOMAINS; return 0; }
    case "$CADDY_DOMAINS" in
        *[[:cntrl:]]*) echo "Error: CADDY_DOMAINS must not contain control characters" >&2; return 2 ;;
    esac
    read -r -a domains <<< "$CADDY_DOMAINS"
    for index in "${!domains[@]}"; do
        item="${domains[index]}"
        item="$(printf '%s' "$item" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
        if ! valid_dns_name "$item"; then
            echo "Error: CADDY_DOMAINS entry $((index + 1)) is not a bare DNS name (got '${item}')" >&2
            return 2
        fi
        case "$seen" in *" $item "*) echo "Error: CADDY_DOMAINS contains the duplicate '${item}'" >&2; return 2 ;; esac
        domains[index]="$item"
        seen+=" $item "
    done
    CADDY_DOMAINS="${domains[*]}"
}

audit_addresses() {
    normalize_caddy_domains || return
    validate_restic_repository || return
    [ -z "${HEALTHCHECK_URL:-}" ] || validate_https_url "$HEALTHCHECK_URL"
}

# Fail closed on values that would be spliced line by line into msmtprc.
validate_smtp() {
    local name value
    for name in SMTP_HOST SMTP_PORT SMTP_FROM SMTP_TO SMTP_USERNAME SMTP_PASSWORD; do
        value="${!name:-}"
        case "$value" in
            *$'\n'* | *$'\r'*) echo "Error: ${name} must be a single line" >&2; return 2 ;;
        esac
    done
    [ -z "${SMTP_PORT:-}" ] && return 0
    [[ "$SMTP_PORT" =~ ^[1-9][0-9]{0,4}$ ]] && [ "$SMTP_PORT" -le 65535 ] \
        || { echo "Error: SMTP_PORT must be 1-65535" >&2; return 2; }
}
