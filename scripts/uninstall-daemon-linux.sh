#!/usr/bin/env bash
# Unregister the daemon on Linux and move the unit to the trash.

set -euo pipefail

readonly SERVICE_NAME="vault-search-daemon"
readonly SERVICE_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$SERVICE_NAME.service"

TRASH_COMMAND="$(command -v trash || command -v trash-put || true)"
if [[ -z "$TRASH_COMMAND" ]]; then
    printf '%s\n' "Error: trash or trash-put is required for recoverable removal." >&2
    exit 1
fi

systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
if [[ -f "$SERVICE_FILE" ]]; then
    "$TRASH_COMMAND" "$SERVICE_FILE"
fi
systemctl --user daemon-reload

printf '%s\n' "Daemon unregistered. The unit was moved to the trash."
printf '%s\n' "The vault, index, and logs were not changed."
