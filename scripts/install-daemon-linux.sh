#!/usr/bin/env bash
# Install the local daemon as a user systemd service.

set -euo pipefail

readonly SERVICE_NAME="vault-search-daemon"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
PROJECT_DIR="$(dirname -- "$SCRIPT_DIR")"
readonly PROJECT_DIR
readonly SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
readonly SERVICE_FILE="$SERVICE_DIR/$SERVICE_NAME.service"

UV_PATH="$(command -v uv || true)"
if [[ -z "$UV_PATH" ]]; then
    printf '%s\n' "Error: uv was not found in PATH." >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    printf '%s\n' "Error: systemctl is unavailable." >&2
    exit 1
fi
STARTUP_TIMEOUT="${VAULT_SEARCH_DAEMON_STARTUP_TIMEOUT:-300}"
readonly STARTUP_TIMEOUT
if [[ ! "$STARTUP_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "Error: VAULT_SEARCH_DAEMON_STARTUP_TIMEOUT must be a positive integer." >&2
    exit 1
fi

PYTHON_PATH="$({
    cd -- "$PROJECT_DIR"
    "$UV_PATH" run --frozen python -c 'import sys; print(sys.executable)'
})"
DAEMON_PATH="$(dirname -- "$PYTHON_PATH")/vault-search-daemon"
if [[ ! -x "$PYTHON_PATH" || ! -x "$DAEMON_PATH" ]]; then
    printf '%s\n' "Error: uv did not create an executable Vault Search environment." >&2
    exit 1
fi

canonicalize_path_override() {
    "$PYTHON_PATH" -c '
import os
import sys

print(os.path.abspath(os.path.expanduser(sys.argv[1])))
' "$1"
}

if [[ -n "${VAULT_SEARCH_CONFIG:-}" ]]; then
    VAULT_SEARCH_CONFIG="$(canonicalize_path_override "$VAULT_SEARCH_CONFIG")"
    export VAULT_SEARCH_CONFIG
fi
if [[ -n "${VAULT_SEARCH_VAULT_PATH:-}" ]]; then
    VAULT_SEARCH_VAULT_PATH="$(canonicalize_path_override "$VAULT_SEARCH_VAULT_PATH")"
    export VAULT_SEARCH_VAULT_PATH
fi
if [[ -n "${VAULT_PATH:-}" ]]; then
    VAULT_PATH="$(canonicalize_path_override "$VAULT_PATH")"
    export VAULT_PATH
fi
if [[ -n "${VAULT_SEARCH_DATA_DIR:-}" ]]; then
    VAULT_SEARCH_DATA_DIR="$(canonicalize_path_override "$VAULT_SEARCH_DATA_DIR")"
    export VAULT_SEARCH_DATA_DIR
fi

HEALTH_BASE_URL="$({
    cd -- "$PROJECT_DIR"
    "$PYTHON_PATH" -c '
from vault_search.config import get_config

daemon = get_config().daemon
host = f"[{daemon.host}]" if ":" in daemon.host else daemon.host
print(f"http://{host}:{daemon.port}")
'
})"
if [[ "$HEALTH_BASE_URL" != http://* ]]; then
    printf '%s\n' "Error: could not resolve the local daemon endpoint." >&2
    exit 1
fi

BACKUP_DIR="$(mktemp -d /tmp/vault-search-daemon-backups.XXXXXX)"
readonly BACKUP_DIR
mkdir -p -- "$SERVICE_DIR"

systemd_escape() {
    local value="$1"
    if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
        printf '%s\n' "Error: service paths cannot contain line breaks." >&2
        return 1
    fi
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//%/%%}"
    printf '%s' "$value"
}

systemd_path_value() {
    local value="$1"
    if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
        printf '%s\n' "Error: service paths cannot contain line breaks." >&2
        return 1
    fi
    value="${value//%/%%}"
    printf '%s' "$value"
}

SYSTEMD_ENVIRONMENT='Environment=PYTHONUNBUFFERED=1'

append_systemd_environment() {
    local name="$1"
    local value="$2"
    local escaped_value
    escaped_value="$(systemd_escape "$value")"
    SYSTEMD_ENVIRONMENT="${SYSTEMD_ENVIRONMENT}
Environment=\"${name}=${escaped_value}\""
}

for service_variable in \
    VAULT_SEARCH_CONFIG \
    VAULT_SEARCH_VAULT_PATH \
    VAULT_PATH \
    VAULT_SEARCH_DATA_DIR \
    VAULT_SEARCH_WRITE_LOCK_TIMEOUT_SECONDS \
    VAULT_SEARCH_ENV \
    VAULT_SEARCH_LOG_LEVEL \
    PYTORCH_ENABLE_MPS_FALLBACK; do
    service_value="${!service_variable:-}"
    if [[ -n "$service_value" ]]; then
        append_systemd_environment "$service_variable" "$service_value"
    fi
done

escaped_daemon="$(systemd_escape "$DAEMON_PATH")"
systemd_project="$(systemd_path_value "$PROJECT_DIR")"

NEW_SERVICE="$BACKUP_DIR/$SERVICE_NAME.service.new"
cat >"$NEW_SERVICE" <<EOF
[Unit]
Description=Vault Search local model daemon
After=default.target

[Service]
Type=simple
WorkingDirectory=$systemd_project
ExecStart="$escaped_daemon"
Restart=on-failure
RestartSec=10
$SYSTEMD_ENVIRONMENT
StandardOutput=journal
StandardError=journal
SyslogIdentifier=vault-search-daemon

[Install]
WantedBy=default.target
EOF

restore_previous_service() {
    systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
    if [[ -f "$SERVICE_FILE" ]]; then
        mv -- "$SERVICE_FILE" "$BACKUP_DIR/$SERVICE_NAME.service.failed"
    fi
    if [[ -f "$BACKUP_DIR/$SERVICE_NAME.service.previous" ]]; then
        cp -- "$BACKUP_DIR/$SERVICE_NAME.service.previous" "$SERVICE_FILE"
        chmod 0644 -- "$SERVICE_FILE"
        systemctl --user daemon-reload
        if ! systemctl --user enable --now "$SERVICE_NAME"; then
            printf '%s\n' "Warning: the previous unit was restored but did not start." >&2
        fi
    else
        systemctl --user daemon-reload
    fi
}

if [[ -f "$SERVICE_FILE" ]]; then
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    mv -- "$SERVICE_FILE" "$BACKUP_DIR/$SERVICE_NAME.service.previous"
fi
mv -- "$NEW_SERVICE" "$SERVICE_FILE"
chmod 0644 -- "$SERVICE_FILE"

systemctl --user daemon-reload
if ! systemctl --user enable --now "$SERVICE_NAME"; then
    restore_previous_service
    printf '%s\n' "Error: the systemd unit could not be enabled." >&2
    printf 'Backup: %s\n' "$BACKUP_DIR" >&2
    exit 1
fi

healthy=false
health_started_at=$SECONDS
while ((SECONDS - health_started_at < STARTUP_TIMEOUT)); do
    managed_pid="$(
        systemctl --user show "$SERVICE_NAME" --property=MainPID --value 2>/dev/null || true
    )"
    if [[ "$managed_pid" =~ ^[1-9][0-9]*$ ]] && (
        cd -- "$PROJECT_DIR"
        "$PYTHON_PATH" -c '
import sys
from vault_search.daemon.client import is_daemon_running

expected_pid = int(sys.argv[1])
raise SystemExit(
    0 if is_daemon_running(timeout=2, retries=1, expected_pid=expected_pid) else 1
)
' "$managed_pid"
    ); then
        healthy=true
        break
    fi
    sleep 1
done

if [[ "$healthy" != true ]]; then
    restore_previous_service
    printf '%s\n' "Error: the service started without passing its health check." >&2
    printf 'Startup window: %ss\n' "$STARTUP_TIMEOUT" >&2
    printf 'Logs: journalctl --user -u %s -n 100\n' "$SERVICE_NAME" >&2
    printf 'Backup: %s\n' "$BACKUP_DIR" >&2
    exit 1
fi

printf 'Daemon installed and healthy at %s.\n' "$HEALTH_BASE_URL"
printf 'Process: %s\n' "$managed_pid"
printf 'Status: systemctl --user status %s\n' "$SERVICE_NAME"
printf 'Logs: journalctl --user -u %s -f\n' "$SERVICE_NAME"
printf 'Previous unit backup, when present: %s\n' "$BACKUP_DIR"
