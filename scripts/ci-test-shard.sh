#!/usr/bin/env bash
# Print the test files for shard INDEX of COUNT (INDEX from 1).
#
# Each shard runs on its own machine: the suite shares DATA_DIR and builds a
# fresh database per test, so it is not safe to parallelise in one process.
# Files are dealt round-robin, largest first, which keeps shards close in
# run time without keeping a timing file.
#
#   pytest $(scripts/ci-test-shard.sh 2 8)
set -euo pipefail

INDEX="$1"
COUNT="$2"
find tests -name 'test_*.py' -type f -exec wc -c {} + |
  grep -v ' total$' | sort -rn | awk '{print $2}' |
  awk -v i="$INDEX" -v n="$COUNT" '(NR - 1) % n == i - 1'
