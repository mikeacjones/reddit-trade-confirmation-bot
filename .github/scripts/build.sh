#!/usr/bin/env bash
set -euo pipefail

# Build and tag the Docker image in the local Docker daemon.
#
# Required env vars: none
#
# Optional env vars:
#   IMAGE_REPOSITORY - Local image repository name (default: reddit-bots/reddit-trade-confirmation-bot)

BUILD_ID=$(git rev-parse --short HEAD)
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-reddit-bots/reddit-trade-confirmation-bot}"
IMAGE="${IMAGE_REPOSITORY}:${BUILD_ID}"

echo "Building image: $IMAGE"
docker build \
  --label "com.reddit-bots.bot-type=trade-confirmation" \
  --label "com.reddit-bots.build-id=${BUILD_ID}" \
  -t "$IMAGE" .

echo "Image tagged locally: $IMAGE"

echo "build_id=${BUILD_ID}" >> "$GITHUB_OUTPUT"
echo "image=${IMAGE}" >> "$GITHUB_OUTPUT"
