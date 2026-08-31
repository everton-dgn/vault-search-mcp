#!/usr/bin/env bash
# Install the local daemon as a macOS LaunchAgent.

set -euo pipefail

readonly LABEL="com.vault-search.daemon"
readonly PLIST_NAME="$LABEL.plist"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
PROJECT_DIR="$(dirname -- "$SCRIPT_DIR")"
readonly PROJECT_DIR
readonly PLIST_DIR="$HOME/Library/LaunchAgents"
readonly PLIST_DEST="$PLIST_DIR/$PLIST_NAME"
readonly LOG_DIR="$HOME/Library/Logs"
DOMAIN="gui/$(id -u)"
readonly DOMAIN

UV_PATH="$(command -v uv || true)"
if [[ -z "$UV_PATH" ]]; then
    printf '%s\n' "Error: uv was not found in PATH." >&2
    exit 1
fi
if [[ "$(uname -s)" != "Darwin" ]]; then
    printf '%s\n' "Error: this installer requires macOS." >&2
    exit 1
fi
STARTUP_TIMEOUT="${VAULT_SEARCH_DAEMON_STARTUP_TIMEOUT:-300}"
readonly STARTUP_TIMEOUT
if [[ ! "$STARTUP_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "Error: VAULT_SEARCH_DAEMON_STARTUP_TIMEOUT must be a positive integer." >&2
    exit 1
fi

xml_escape() {
    printf '%s' "$1" | sed \
        -e 's/&/\&amp;/g' \
        -e 's/</\&lt;/g' \
        -e 's/>/\&gt;/g' \
        -e 's/"/\&quot;/g' \
        -e "s/'/\&apos;/g"
}

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

PLIST_ENVIRONMENT='        <key>PYTHONUNBUFFERED</key>
        <string>1</string>'

append_plist_environment() {
    local name="$1"
    local value="$2"
    local escaped_value
    if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
        printf 'Error: %s cannot contain line breaks.\n' "$name" >&2
        return 1
    fi
    escaped_value="$(xml_escape "$value")"
    PLIST_ENVIRONMENT="${PLIST_ENVIRONMENT}
        <key>${name}</key>
        <string>${escaped_value}</string>"
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
        append_plist_environment "$service_variable" "$service_value"
    fi
done

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
mkdir -p -- "$PLIST_DIR" "$LOG_DIR"

escaped_daemon="$(xml_escape "$DAEMON_PATH")"
escaped_project="$(xml_escape "$PROJECT_DIR")"
escaped_stdout="$(xml_escape "$LOG_DIR/vault-search-daemon.log")"
escaped_stderr="$(xml_escape "$LOG_DIR/vault-search-daemon.error.log")"
NEW_PLIST="$BACKUP_DIR/$PLIST_NAME.new"

cat >"$NEW_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$escaped_daemon</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$escaped_project</string>
    <key>EnvironmentVariables</key>
    <dict>
$PLIST_ENVIRONMENT
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$escaped_stdout</string>
    <key>StandardErrorPath</key>
    <string>$escaped_stderr</string>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF

plutil -lint "$NEW_PLIST" >/dev/null

restore_previous_plist() {
    launchctl bootout "$DOMAIN" "$PLIST_DEST" 2>/dev/null || true
    if [[ -f "$PLIST_DEST" ]]; then
        mv -- "$PLIST_DEST" "$BACKUP_DIR/$PLIST_NAME.failed"
    fi
    if [[ -f "$BACKUP_DIR/$PLIST_NAME.previous" ]]; then
        cp -- "$BACKUP_DIR/$PLIST_NAME.previous" "$PLIST_DEST"
        chmod 0644 -- "$PLIST_DEST"
        if ! launchctl bootstrap "$DOMAIN" "$PLIST_DEST"; then
            printf '%s\n' "Warning: the previous plist was restored but did not start." >&2
        fi
    fi
}

if [[ -f "$PLIST_DEST" ]]; then
    launchctl bootout "$DOMAIN" "$PLIST_DEST" 2>/dev/null || true
    mv -- "$PLIST_DEST" "$BACKUP_DIR/$PLIST_NAME.previous"
fi
mv -- "$NEW_PLIST" "$PLIST_DEST"
chmod 0644 -- "$PLIST_DEST"
if ! launchctl bootstrap "$DOMAIN" "$PLIST_DEST"; then
    restore_previous_plist
    printf '%s\n' "Error: the LaunchAgent could not be registered." >&2
    printf 'Backup: %s\n' "$BACKUP_DIR" >&2
    exit 1
fi

healthy=false
health_started_at=$SECONDS
while ((SECONDS - health_started_at < STARTUP_TIMEOUT)); do
    managed_pid="$(
        launchctl print "$DOMAIN/$LABEL" 2>/dev/null \
            | sed -n 's/^[[:space:]]*pid = \([1-9][0-9]*\)$/\1/p' \
            | head -n 1 || true
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
    restore_previous_plist
    printf '%s\n' "Error: the LaunchAgent started without passing its health check." >&2
    printf 'Startup window: %ss\n' "$STARTUP_TIMEOUT" >&2
    printf 'Logs: %s\n' "$LOG_DIR/vault-search-daemon.error.log" >&2
    printf 'Backup: %s\n' "$BACKUP_DIR" >&2
    exit 1
fi

printf 'Daemon installed and healthy at %s.\n' "$HEALTH_BASE_URL"
printf 'Process: %s\n' "$managed_pid"
printf 'Status: launchctl print %s/%s\n' "$DOMAIN" "$LABEL"
printf 'Logs: %s\n' "$LOG_DIR/vault-search-daemon.log"
printf 'Previous plist backup, when present: %s\n' "$BACKUP_DIR"
