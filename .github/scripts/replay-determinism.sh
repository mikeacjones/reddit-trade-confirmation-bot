#!/usr/bin/env bash
set -euo pipefail

# For each running workflow in the Temporal namespace, download its history,
# replay it against the current code, and set the versioning override:
#   - auto_upgrade  if replay succeeds
#   - unspecified   if replay fails, let the workflow decide when to upgrade
#
# Required env vars:
#   TEMPORAL_ADDRESS   - Temporal server address (e.g. 192.168.101.240:7233)
#   TEMPORAL_NAMESPACE - Temporal namespace (e.g. reddit-bots)

DOWNLOAD_DIR=$(mktemp -d)
trap 'rm -rf "$DOWNLOAD_DIR"' EXIT

echo "=== Downloading running workflow histories ==="

workflow_ids=$(temporal workflow list \
  --address "$TEMPORAL_ADDRESS" \
  --namespace "$TEMPORAL_NAMESPACE" \
  --query "ExecutionStatus='Running'" \
  --output json \
  | jq -r '.[].execution.workflowId')

if [ -z "$workflow_ids" ]; then
  echo "No running workflows found — skipping replay check"
  exit 0
fi

for wid in $workflow_ids; do
  echo "Downloading history for $wid"
  temporal workflow show \
    --address "$TEMPORAL_ADDRESS" \
    --namespace "$TEMPORAL_NAMESPACE" \
    --workflow-id "$wid" \
    --output json > "${DOWNLOAD_DIR}/${wid}.json"
done

echo "Downloaded $(ls "$DOWNLOAD_DIR"/*.json | wc -l) histories"

echo ""
echo "=== Running replay determinism checks ==="

failed_workflows=()
passed_workflows=()

# Test each workflow individually — the test suite replays every file in
# REPLAY_HISTORIES_DIR, so we isolate one history at a time.
for history_file in "$DOWNLOAD_DIR"/*.json; do
  wid=$(basename "$history_file" .json)
  solo_dir=$(mktemp -d)

  cp "$history_file" "$solo_dir/"

  echo "Replaying $wid ..."
  if REPLAY_HISTORIES_DIR="$solo_dir" uv run python -m pytest tests/test_replay_determinism.py -x -q --tb=short; then
    echo "$wid ... PASSED"
    passed_workflows+=("$wid")
  else
    echo "$wid ... FAILED"
    failed_workflows+=("$wid")
  fi

  rm -rf "$solo_dir"
done

echo ""
echo "=== Setting versioning overrides ==="

for wid in "${passed_workflows[@]}"; do
  echo "Setting $wid -> auto_upgrade"
  temporal workflow update-options \
    --address "$TEMPORAL_ADDRESS" \
    --namespace "$TEMPORAL_NAMESPACE" \
    --workflow-id "$wid" \
    --versioning-override-behavior auto_upgrade
done

for wid in "${failed_workflows[@]}"; do
  # Use unspecified so the application code can control its own upgrade path via CaN
  echo "Setting $wid -> unspecified"
  temporal workflow update-options \
    --address "$TEMPORAL_ADDRESS" \
    --namespace "$TEMPORAL_NAMESPACE" \
    --workflow-id "$wid" \
    --versioning-override-behavior unspecified
done

echo ""
if [ ${#failed_workflows[@]} -gt 0 ]; then
  echo "WARNING: ${#failed_workflows[@]} workflow(s) failed replay and were set to unspecified:"
  printf '  - %s\n' "${failed_workflows[@]}"
else
  echo "All ${#passed_workflows[@]} workflow(s) passed replay — set to auto_upgrade"
fi
