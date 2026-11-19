#!/usr/bin/env bash
# For each fix branch on the fork, report its tip and how many of its commits
# (since main) are not yet on the integration branch, matched by patch so
# cherry-picks count. Prints a markdown table.
#
#   scripts/integration-status.sh [integration-ref] [branch...]
set -euo pipefail

INTEGRATION="${1:-HEAD}"
shift || true
BASE="${INTEGRATION_BASE:-origin/main}"
if [ "$#" -eq 0 ]; then
  set -- $(git for-each-ref --format='%(refname:short)' 'refs/remotes/origin/fix-*' 'refs/remotes/origin/bt-f*')
fi

echo "| branch | tip | commits since main | not on integration |"
echo "| --- | --- | --- | --- |"
for ref in "$@"; do
  total="$(git rev-list --count "$BASE..$ref")"
  missing="$(git cherry "$INTEGRATION" "$ref" "$BASE" | grep -c '^+' || true)"
  echo "| ${ref#origin/} | $(git rev-parse --short=9 "$ref") | $total | $missing |"
done
