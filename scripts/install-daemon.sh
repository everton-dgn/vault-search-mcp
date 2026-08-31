#!/usr/bin/env bash
# Instala o daemon local como LaunchAgent no macOS.

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
    printf '%s\n' "Erro: uv não foi encontrado no PATH." >&2
    exit 1
fi
if [[ "$(uname -s)" != "Darwin" ]]; then
    printf '%s\n' "Erro: este instalador requer macOS." >&2
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    printf '%s\n' "Erro: curl é necessário para verificar o health check." >&2
    exit 1
fi
STARTUP_TIMEOUT="${VAULT_SEARCH_DAEMON_STARTUP_TIMEOUT:-300}"
readonly STARTUP_TIMEOUT
if [[ ! "$STARTUP_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "Erro: VAULT_SEARCH_DAEMON_STARTUP_TIMEOUT deve ser um inteiro positivo." >&2
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

HEALTH_BASE_URL="$({
    cd -- "$PROJECT_DIR"
    "$UV_PATH" run --frozen python -c '
from vault_search.config import get_config

daemon = get_config().daemon
host = f"[{daemon.host}]" if ":" in daemon.host else daemon.host
print(f"http://{host}:{daemon.port}")
'
})"
if [[ "$HEALTH_BASE_URL" != http://* ]]; then
    printf '%s\n' "Erro: não foi possível resolver o endpoint local do daemon." >&2
    exit 1
fi

BACKUP_DIR="$(mktemp -d /tmp/vault-search-daemon-backups.XXXXXX)"
readonly BACKUP_DIR
mkdir -p -- "$PLIST_DIR" "$LOG_DIR"

escaped_uv="$(xml_escape "$UV_PATH")"
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
        <string>$escaped_uv</string>
        <string>run</string>
        <string>--frozen</string>
        <string>--project</string>
        <string>$escaped_project</string>
        <string>vault-search-daemon</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$escaped_project</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
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
            printf '%s\n' "Aviso: o plist anterior foi restaurado, mas não iniciou." >&2
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
    printf '%s\n' "Erro: o LaunchAgent não pôde ser registrado." >&2
    printf 'Backup: %s\n' "$BACKUP_DIR" >&2
    exit 1
fi

healthy=false
health_started_at=$SECONDS
while ((SECONDS - health_started_at < STARTUP_TIMEOUT)); do
    if curl --fail --silent --max-time 2 \
        "$HEALTH_BASE_URL/health" >/dev/null; then
        healthy=true
        break
    fi
    sleep 1
done

if [[ "$healthy" != true ]]; then
    restore_previous_plist
    printf '%s\n' "Erro: o LaunchAgent iniciou sem responder ao health check." >&2
    printf 'Janela de inicialização: %ss\n' "$STARTUP_TIMEOUT" >&2
    printf 'Logs: %s\n' "$LOG_DIR/vault-search-daemon.error.log" >&2
    printf 'Backup: %s\n' "$BACKUP_DIR" >&2
    exit 1
fi

printf 'Daemon instalado e saudável em %s.\n' "$HEALTH_BASE_URL"
printf 'Status: launchctl print %s/%s\n' "$DOMAIN" "$LABEL"
printf 'Logs: %s\n' "$LOG_DIR/vault-search-daemon.log"
printf 'Backup do plist anterior, quando existia: %s\n' "$BACKUP_DIR"
