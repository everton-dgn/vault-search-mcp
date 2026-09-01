#!/usr/bin/env bash
# Unregister the daemon on macOS and move its plists to the trash.

set -euo pipefail

DOMAIN="gui/$(id -u)"
readonly DOMAIN
readonly PLIST_DIR="$HOME/Library/LaunchAgents"

TRASH_COMMAND="$(command -v trash || command -v trash-put || true)"
if [[ -z "$TRASH_COMMAND" ]]; then
    printf '%s\n' "Error: trash or trash-put is required for recoverable removal." >&2
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

printf '%s\n' "Daemon unregistered. Existing plists were moved to the trash."
printf '%s\n' "The vault, index, and logs were not changed."
