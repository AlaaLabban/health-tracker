#!/usr/bin/env bash
# Refuse anything that looks like a secret or a private detail before it reaches a public history.
#   check-secrets.sh --staged    lines added to the index (default; used by the pre-commit hook)
#   check-secrets.sh --tree      every tracked file as it is now
#   check-secrets.sh --history   every line ever added on the current branch
# A line can opt out with a trailing "check-secrets:allow" (use it sparingly, with a reason).
set -euo pipefail
cd "${CHECK_SECRETS_ROOT:-$(dirname "$0")/..}"   # CHECK_SECRETS_ROOT lets a copy of this script scan another checkout

mode=${1:---staged}
case "$mode" in
  --staged)  stream() { git diff --cached -U0 --no-color | grep -E '^\+' | grep -vE '^\+\+\+' || true; } ;;
  --tree)    stream() { git ls-files -z | xargs -0 grep -nI '' || true; } ;;
  --history) stream() { git log -p -U0 --no-color --format= | grep -E '^\+' | grep -vE '^\+\+\+' || true; } ;;
  *) echo "usage: $0 [--staged|--tree|--history]" >&2; exit 2 ;;
esac

patterns=(
  'password[[:space:]]*=[[:space:]]*[^[:space:]@;]'                         # a literal SIP or service password
  '(^|[^0-9.])(10\.[0-9]+|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]+\.[0-9]+'  # private LAN addresses
  '([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}'                                       # MAC addresses
  '/home/[a-z]|/Users/[A-Za-z]'                                              # host paths
  '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}'                            # e-mail addresses
  'sk-ant-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}'
  '-----BEGIN [A-Z ]*PRIVATE KEY'
  'ANTHROPIC_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]]'
)
regex=$(IFS='|'; echo "${patterns[*]}")

hits=$(stream | grep -E "$regex" \
  | grep -vE 'check-secrets:allow|you@example\.com|CHANGE_ME|@[A-Z0-9_]+@|noreply\.github\.com|00:11:22:33:44:55' || true)   # the last one is the placeholder in stock Asterisk samples
if [ -n "$hits" ]; then
  echo "check-secrets: possible secret or private detail found:" >&2
  echo "$hits" | cut -c1-160 >&2
  exit 1
fi
echo "check-secrets: clean ($mode)"
