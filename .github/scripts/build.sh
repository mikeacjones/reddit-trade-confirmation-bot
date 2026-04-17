#!/usr/bin/env bash
set -euo pipefail

# Build and push the Docker image to the local registry.
#
# Required env vars:
#   REGISTRY  - Docker registry host (e.g. localhost:5000)

BUILD_ID=$(git rev-parse --short HEAD)
IMAGE="${REGISTRY}/reddit-trade-confirmation-bot:${BUILD_ID}"

echo "Building image: $IMAGE"
docker build -t "$IMAGE" .
docker push "$IMAGE"

echo "build_id=${BUILD_ID}" >> "$GITHUB_OUTPUT"
