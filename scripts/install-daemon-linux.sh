#!/usr/bin/env bash
# Instala o daemon local como serviço systemd do usuário.

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
    printf '%s\n' "Erro: uv não foi encontrado no PATH." >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    printf '%s\n' "Erro: systemctl não está disponível." >&2
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
mkdir -p -- "$SERVICE_DIR"

systemd_escape() {
    local value="$1"
    if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
        printf '%s\n' "Erro: paths do serviço não podem conter quebras de linha." >&2
        return 1
    fi
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//%/%%}"
    printf '%s' "$value"
}

escaped_uv="$(systemd_escape "$UV_PATH")"
escaped_project="$(systemd_escape "$PROJECT_DIR")"

NEW_SERVICE="$BACKUP_DIR/$SERVICE_NAME.service.new"
cat >"$NEW_SERVICE" <<EOF
[Unit]
Description=Vault Search local model daemon
After=default.target

[Service]
Type=simple
WorkingDirectory="$escaped_project"
ExecStart="$escaped_uv" run --frozen --project "$escaped_project" vault-search-daemon
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
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
            printf '%s\n' "Aviso: a unidade anterior foi restaurada, mas não iniciou." >&2
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
    printf '%s\n' "Erro: a unidade systemd não pôde ser ativada." >&2
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
    restore_previous_service
    printf '%s\n' "Erro: o serviço iniciou sem responder ao health check." >&2
    printf 'Janela de inicialização: %ss\n' "$STARTUP_TIMEOUT" >&2
    printf 'Logs: journalctl --user -u %s -n 100\n' "$SERVICE_NAME" >&2
    printf 'Backup: %s\n' "$BACKUP_DIR" >&2
    exit 1
fi

printf 'Daemon instalado e saudável em %s.\n' "$HEALTH_BASE_URL"
printf 'Status: systemctl --user status %s\n' "$SERVICE_NAME"
printf 'Logs: journalctl --user -u %s -f\n' "$SERVICE_NAME"
printf 'Backup da unidade anterior, quando existia: %s\n' "$BACKUP_DIR"
