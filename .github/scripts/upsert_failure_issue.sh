#!/usr/bin/env bash
# Upsert the one tracked failure issue for a scheduled workflow, so a dead
# job is never silent: the FIRST failure opens "<title>", every later one
# comments its run link on the same issue (search is --state open, so a
# closed issue naturally starts a fresh one on the next failure).
#
# usage: upsert_failure_issue.sh TITLE RUN_URL [EXTRA_NOTE]
# env:   GH_TOKEN, GITHUB_REPOSITORY (both set by the workflow)
set -euo pipefail

title="${1:?usage: upsert_failure_issue.sh TITLE RUN_URL [EXTRA_NOTE]}"
run_url="${2:?usage: upsert_failure_issue.sh TITLE RUN_URL [EXTRA_NOTE]}"
note="${3:-}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"

num=$(gh issue list --repo "$repo" --state open \
        --json number,title --jq '.[] | "\(.number) \(.title)"' \
      | grep -F "$title" | awk '{print $1}' | head -1 || true)

if [ -n "$num" ]; then
  gh issue comment "$num" --repo "$repo" --body "Failed again: $run_url"
else
  body=$(printf '%s\n\n%s' \
    "The scheduled workflow failed, so committed data stays at the last successful refresh. Newest failing run: $run_url" \
    "$note")
  gh issue create --repo "$repo" --title "$title" --body "$body"
fi
