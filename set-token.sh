#!/usr/bin/env bash
# Ask for the Telegram bot token without echoing it and write it into bot.env.
set -euo pipefail
env_file="$(dirname "$0")/bot.env"
read -rsp "Paste token: " token; echo
if [[ ! "$token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
    echo "That does not look like a BotFather token (digits:letters). Nothing changed." >&2
    exit 1
fi
sed -i "s|^TELEGRAM_TOKEN=.*|TELEGRAM_TOKEN=$token|" "$env_file"
echo "Saved. Token line filled: $(grep -c "^TELEGRAM_TOKEN=." "$env_file")"
