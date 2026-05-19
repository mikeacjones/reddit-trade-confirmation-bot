#!/usr/bin/env bash
set -euo pipefail

# Build and push the Docker image to the local registry.
#
# Required env vars:
#   REGISTRY  - Docker registry host (e.g. localhost:5000)
#
# Optional env vars:
#   PUSH_IMAGE - Push the built image after building (default: true)

BUILD_ID=$(git rev-parse --short HEAD)
IMAGE="${REGISTRY}/reddit-trade-confirmation-bot:${BUILD_ID}"
PUSH_IMAGE="${PUSH_IMAGE:-true}"

echo "Building image: $IMAGE"
docker build \
  --label "com.reddit-bots.bot-type=trade-confirmation" \
  --label "com.reddit-bots.build-id=${BUILD_ID}" \
  -t "$IMAGE" .

if [ "$PUSH_IMAGE" = "true" ]; then
  docker push "$IMAGE"
else
  echo "Skipping image push because PUSH_IMAGE=$PUSH_IMAGE"
fi

echo "build_id=${BUILD_ID}" >> "$GITHUB_OUTPUT"
echo "image=${IMAGE}" >> "$GITHUB_OUTPUT"
