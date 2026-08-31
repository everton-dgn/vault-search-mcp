#!/usr/bin/env bash
# Desregistra o daemon no Linux e move a unidade para a lixeira.

set -euo pipefail

readonly SERVICE_NAME="vault-search-daemon"
readonly SERVICE_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$SERVICE_NAME.service"

TRASH_COMMAND="$(command -v trash || command -v trash-put || true)"
if [[ -z "$TRASH_COMMAND" ]]; then
    printf '%s\n' "Erro: trash ou trash-put é obrigatório para remoção recuperável." >&2
    exit 1
fi

systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
if [[ -f "$SERVICE_FILE" ]]; then
    "$TRASH_COMMAND" "$SERVICE_FILE"
fi
systemctl --user daemon-reload

printf '%s\n' "Daemon desregistrado. A unidade foi movida para a lixeira."
printf '%s\n' "O vault, o índice e os logs não foram alterados."
