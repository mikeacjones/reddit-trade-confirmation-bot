#!/usr/bin/env bash
set -euo pipefail

# Deploy trade-confirmation-bot instances to k8s via Helm.
# One Helm release per .env file found under $BOTS_DIR/trade-confirmation-bot/.
#
# Required env vars:
#   BOTS_DIR      - Root directory containing bot env files
#   REGISTRY      - Docker registry host
#   BUILD_ID      - Git short SHA used as the image tag
#   K8S_NAMESPACE - Kubernetes namespace for deployments

IMAGE_REPO="${REGISTRY}/reddit-trade-confirmation-bot"
BOT_ENV_DIR="${BOTS_DIR}/trade-confirmation-bot"

if [ ! -d "$BOT_ENV_DIR" ]; then
  echo "ERROR: Bot env directory not found: $BOT_ENV_DIR"
  exit 1
fi

for env_file in "$BOT_ENV_DIR"/*.env; do
  [ -f "$env_file" ] || continue

  subreddit_name=$(basename "$env_file" .env)
  release_name="reddit-trade-confirmation-bot-$(echo "$subreddit_name" | tr '[:upper:]' '[:lower:]' | tr '_' '-')"
  secret_name="bot-trade-confirmation-bot-$(echo "$subreddit_name" | tr '[:upper:]' '[:lower:]' | tr '_' '-')-env"

  echo "=== Deploying: trade-confirmation-bot / $subreddit_name ==="

  echo "  Creating secret: $secret_name"
  kubectl create secret generic "$secret_name" \
    --from-env-file="$env_file" \
    --namespace="$K8S_NAMESPACE" \
    --dry-run=client -o yaml | kubectl apply -f -

  echo "  Deploying Helm release: $release_name"
  helm upgrade --install "$release_name" \
    ./charts/reddit-bot \
    --namespace "$K8S_NAMESPACE" \
    --set image.repository="$IMAGE_REPO" \
    --set image.tag="$BUILD_ID" \
    --set botType="trade-confirmation-bot" \
    --set subredditName="$subreddit_name" \
    --set secretName="$secret_name"

  echo ""
done
