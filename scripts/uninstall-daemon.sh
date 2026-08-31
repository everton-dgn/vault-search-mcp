#!/usr/bin/env bash
# Desregistra o daemon no macOS e move os plists para a lixeira.

set -euo pipefail

DOMAIN="gui/$(id -u)"
readonly DOMAIN
readonly PLIST_DIR="$HOME/Library/LaunchAgents"

TRASH_COMMAND="$(command -v trash || command -v trash-put || true)"
if [[ -z "$TRASH_COMMAND" ]]; then
    printf '%s\n' "Erro: trash ou trash-put é obrigatório para remoção recuperável." >&2
    exit 1
fi

for plist in \
    "$PLIST_DIR/com.vault-search.daemon.plist" \
    "$PLIST_DIR/com.vault-search.mcp.plist"; do
    if [[ ! -f "$plist" ]]; then
        continue
    fi
    launchctl bootout "$DOMAIN" "$plist" 2>/dev/null || true
    "$TRASH_COMMAND" "$plist"
done

printf '%s\n' "Daemon desregistrado. Os plists existentes foram movidos para a lixeira."
printf '%s\n' "O vault, o índice e os logs não foram alterados."
