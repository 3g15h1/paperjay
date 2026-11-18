#!/usr/bin/env bash
# Refuse to commit private details (home IPs, names, hostnames …) into this public-to-be repo.
# The patterns live OUTSIDE the repo, one extended regex per line, in $PAPERJAY_PRIVATE_PATTERNS
# (default ~/.config/paperjay/private-patterns). Install as a hook:
#   ln -s ../../scripts/check-private.sh .git/hooks/pre-commit
set -euo pipefail
patterns="${PAPERJAY_PRIVATE_PATTERNS:-$HOME/.config/paperjay/private-patterns}"
[ -s "$patterns" ] || { echo "check-private: no patterns file at $patterns, skipping" >&2; exit 0; }
if [ "${1:-}" = "--all" ]; then
  files=$(git ls-files)
else
  files=$(git diff --cached --name-only --diff-filter=ACMR)
fi
[ -n "$files" ] || exit 0
# shellcheck disable=SC2086
if hits=$(grep -nIE -f "$patterns" $files 2>/dev/null); then
  echo "check-private: private details found, not committing:" >&2
  echo "$hits" >&2
  exit 1
fi
