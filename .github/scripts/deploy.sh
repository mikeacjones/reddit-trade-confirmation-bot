#!/usr/bin/env bash
set -euo pipefail

# Deploy trade-confirmation-bot instances directly on Docker.
# One container is started per .env file found under
# $BOTS_DIR/trade-confirmation-bot/.
#
# Required env vars:
#   BOTS_DIR           - Root directory containing bot env files
#   BUILD_ID           - Git short SHA used as the image tag
#   TEMPORAL_ADDRESS   - Temporal frontend address
#   TEMPORAL_NAMESPACE - Temporal namespace
#
# Optional env vars:
#   IMAGE_REPOSITORY                                  (default: reddit-bots/reddit-trade-confirmation-bot)
#   DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_WORKFLOWS  (default: 1)
#   DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_ACTIVITIES (default: 1)
#   DEPLOYMENT_HEALTH_MAX_SECONDS                   (default: 0 / disabled)
#   DEPLOYMENT_HEALTH_CHECK_INTERVAL_SECONDS        (default: 60)
#   DEPLOYMENT_HEALTH_METRICS_URL                   (default: worker local metrics)
#   DEPLOYMENT_HEALTH_REQUIRE_METRICS               (default: false)
#   DEPLOYMENT_HEALTH_MAX_FAILED_WORKFLOWS          (default: 0)
#   DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_FAILURES     (default: 0)
#   DEPLOYMENT_HEALTH_MAX_SDK_ACTIVITY_FAILURES     (default: 0)
#   DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_TASK_FAILURES (default: 0)

BOT_ENV_DIR="${BOTS_DIR}/trade-confirmation-bot"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-reddit-bots/reddit-trade-confirmation-bot}"
IMAGE="${IMAGE_REPOSITORY}:${BUILD_ID}"
BOT_TYPE="trade-confirmation"
CONTAINER_PREFIX="${DOCKER_CONTAINER_PREFIX:-reddit-bots-trade-confirmation}"
DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_WORKFLOWS="${DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_WORKFLOWS:-1}"
DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_ACTIVITIES="${DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_ACTIVITIES:-1}"
DEPLOYMENT_HEALTH_MAX_SECONDS="${DEPLOYMENT_HEALTH_MAX_SECONDS:-0}"
DEPLOYMENT_HEALTH_CHECK_INTERVAL_SECONDS="${DEPLOYMENT_HEALTH_CHECK_INTERVAL_SECONDS:-60}"
DEPLOYMENT_HEALTH_REQUIRE_METRICS="${DEPLOYMENT_HEALTH_REQUIRE_METRICS:-false}"
DEPLOYMENT_HEALTH_MAX_FAILED_WORKFLOWS="${DEPLOYMENT_HEALTH_MAX_FAILED_WORKFLOWS:-0}"
DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_FAILURES="${DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_FAILURES:-0}"
DEPLOYMENT_HEALTH_MAX_SDK_ACTIVITY_FAILURES="${DEPLOYMENT_HEALTH_MAX_SDK_ACTIVITY_FAILURES:-0}"
DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_TASK_FAILURES="${DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_TASK_FAILURES:-0}"

if [ ! -d "$BOT_ENV_DIR" ]; then
  echo "ERROR: Bot env directory not found: $BOT_ENV_DIR"
  exit 1
fi

temporal_cli() {
  temporal \
    --address "$TEMPORAL_ADDRESS" \
    --namespace "$TEMPORAL_NAMESPACE" \
    "$@"
}

temporal_allow_exists() {
  local output
  local status

  set +e
  output=$(temporal_cli "$@" 2>&1)
  status=$?
  set -e

  if [ "$status" -eq 0 ]; then
    echo "$output"
    return 0
  fi

  if echo "$output" | grep -qi "already"; then
    echo "$output"
    return 0
  fi

  echo "$output"
  return "$status"
}

slugify() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9.-]+/-/g; s/-+/-/g; s/^[.-]+//; s/[.-]+$//'
}

wait_for_current_version() {
  local deployment_name="$1"
  local build_id="$2"
  local output
  local status

  for attempt in $(seq 1 30); do
    set +e
    output=$(temporal_cli worker deployment set-current-version \
      --deployment-name "$deployment_name" \
      --build-id "$build_id" \
      --yes 2>&1)
    status=$?
    set -e

    if [ "$status" -eq 0 ]; then
      echo "$output"
      return 0
    fi

    echo "  Waiting for Temporal pollers for $deployment_name/$build_id (attempt $attempt/30)"
    echo "$output"
    sleep 2
  done

  echo "ERROR: Timed out setting current Temporal version for $deployment_name/$build_id"
  return 1
}

for env_file in "$BOT_ENV_DIR"/*.env; do
  [ -f "$env_file" ] || continue

  subreddit_name=$(basename "$env_file" .env)
  subreddit_slug=$(slugify "$subreddit_name")
  if [ -z "$subreddit_slug" ]; then
    subreddit_slug="unknown"
  fi
  deployment_name="reddit-bots-trade-confirmation-${subreddit_slug}"
  # Docker container names cannot contain slashes; keep the slash-style name as
  # a label for filtering/display, and use a Docker-safe actual name.
  container_name="${CONTAINER_PREFIX}-${subreddit_slug}-${BUILD_ID}"
  logical_name="reddit-bots/${BOT_TYPE}/${subreddit_name}-${BUILD_ID}"

  echo "=== Deploying: ${BOT_TYPE} / ${subreddit_name} ==="
  echo "  Image: $IMAGE"
  echo "  Temporal deployment: $deployment_name"
  echo "  Docker container: $container_name"
  echo "  Logical container name: $logical_name"

  echo "  Ensuring Temporal Worker Deployment exists"
  temporal_allow_exists worker deployment create \
    --name "$deployment_name"

  echo "  Reading previous Temporal current version"
  previous_build_id=$(PYTHONPATH=src \
    SUBREDDIT_NAME="$subreddit_name" \
    TEMPORAL_ADDRESS="$TEMPORAL_ADDRESS" \
    TEMPORAL_NAMESPACE="$TEMPORAL_NAMESPACE" \
    TEMPORAL_DEPLOYMENT_NAME="$deployment_name" \
      uv run python -m temporal.starter deployment-current-build "$deployment_name")
  if [ "$previous_build_id" = "$BUILD_ID" ]; then
    previous_build_id=""
  fi
  if [ -n "$previous_build_id" ]; then
    echo "  Previous current build: $previous_build_id"
  else
    echo "  Previous current build: <none>"
  fi

  echo "  Ensuring Temporal Worker Deployment Version exists"
  temporal_allow_exists worker deployment create-version \
    --deployment-name "$deployment_name" \
    --build-id "$BUILD_ID"

  if docker container inspect "$container_name" >/dev/null 2>&1; then
    echo "  Removing existing container with same name: $container_name"
    docker rm -f "$container_name"
  fi

  echo "  Starting Docker container"
  docker run -d \
    --name "$container_name" \
    --restart unless-stopped \
    --env-file "$env_file" \
    -e "SUBREDDIT_NAME=$subreddit_name" \
    -e "TEMPORAL_ADDRESS=$TEMPORAL_ADDRESS" \
    -e "TEMPORAL_HOST=$TEMPORAL_ADDRESS" \
    -e "TEMPORAL_NAMESPACE=$TEMPORAL_NAMESPACE" \
    -e "TEMPORAL_DEPLOYMENT_NAME=$deployment_name" \
    -e "TEMPORAL_WORKER_BUILD_ID=$BUILD_ID" \
    -e "BUILD_ID=$BUILD_ID" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    --label "com.reddit-bots.bot-type=${BOT_TYPE}" \
    --label "com.reddit-bots.subreddit=${subreddit_name}" \
    --label "com.reddit-bots.subreddit-slug=${subreddit_slug}" \
    --label "com.reddit-bots.deployment-name=${deployment_name}" \
    --label "com.reddit-bots.build-id=${BUILD_ID}" \
    --label "com.reddit-bots.image=${IMAGE}" \
    --label "com.reddit-bots.logical-name=${logical_name}" \
    "$IMAGE"

  echo "  Promoting Temporal Worker Deployment Version"
  wait_for_current_version "$deployment_name" "$BUILD_ID"

  echo "  Signal-with-start deployment cleanup workflow"
  PYTHONPATH=src \
  SUBREDDIT_NAME="$subreddit_name" \
  TEMPORAL_ADDRESS="$TEMPORAL_ADDRESS" \
  TEMPORAL_NAMESPACE="$TEMPORAL_NAMESPACE" \
  TEMPORAL_DEPLOYMENT_NAME="$deployment_name" \
  DEPLOYMENT_PREVIOUS_BUILD_ID="$previous_build_id" \
  DEPLOYMENT_CONTAINER_NAME="$container_name" \
  DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_WORKFLOWS="$DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_WORKFLOWS" \
  DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_ACTIVITIES="$DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_ACTIVITIES" \
  DEPLOYMENT_HEALTH_MAX_SECONDS="$DEPLOYMENT_HEALTH_MAX_SECONDS" \
  DEPLOYMENT_HEALTH_CHECK_INTERVAL_SECONDS="$DEPLOYMENT_HEALTH_CHECK_INTERVAL_SECONDS" \
  DEPLOYMENT_HEALTH_METRICS_URL="${DEPLOYMENT_HEALTH_METRICS_URL:-}" \
  DEPLOYMENT_HEALTH_REQUIRE_METRICS="$DEPLOYMENT_HEALTH_REQUIRE_METRICS" \
  DEPLOYMENT_HEALTH_MAX_FAILED_WORKFLOWS="$DEPLOYMENT_HEALTH_MAX_FAILED_WORKFLOWS" \
  DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_FAILURES="$DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_FAILURES" \
  DEPLOYMENT_HEALTH_MAX_SDK_ACTIVITY_FAILURES="$DEPLOYMENT_HEALTH_MAX_SDK_ACTIVITY_FAILURES" \
  DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_TASK_FAILURES="$DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_TASK_FAILURES" \
    uv run python -m temporal.starter deployment-signal-with-start \
      "$BUILD_ID" \
      "$deployment_name"

  echo ""
done
