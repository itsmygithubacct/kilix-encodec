#!/usr/bin/env bash
# usage: consumer_grep.sh <repo> <ref>
# Successor of the F1 enumeration script (F1 verifier finding F4). Read-only
# git grep over one committed ref for anything that consumes kilix-encodec
# epoch/reset semantics, the 24 kHz graph population, or the native package.
# Exit 0: hits printed. Exit 1: the ref resolved and nothing matched.
# Any other exit is a failure and is reported on stderr: git's own status is
# propagated (for example 128 for a bad ref or a missing repository), so an
# error can never read as "no consumers".
set -uo pipefail
if [ "$#" -ne 2 ]; then
  echo "consumer_grep: usage: $0 <repo> <ref>" >&2
  exit 2
fi
repo=$1
ref=$2
PATTERN='encodec|kenc_|KMA2|epoch_packets|epoch[-_ ]reset|cold[-_ ]start|pre-?roll|02201a5a|e151992a|844d8fcf|065746be|19c6f57f|90cb4f26'
git -C "$repo" grep -n -I -i -E "$PATTERN" "$ref" --
status=$?
if [ "$status" -gt 1 ]; then
  echo "consumer_grep: FAILED: git grep exited $status for repo '$repo' ref '$ref'; no count is valid" >&2
fi
exit "$status"
