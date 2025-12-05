#! /bin/bash
set -e

BOT_TYPE=reddit-trade-confirmation-bot
subreddit_name=$1

if [ -z "$subreddit_name" ]; then
  echo "Usage: $0 <subreddit_name>"
  exit 1
fi

echo "Building Docker image for $subreddit_name..."
docker build . -t $BOT_TYPE

echo "Starting Docker container for r/$subreddit_name..."
docker run \
  --name $subreddit_name \
  -d \
  -e SUBREDDIT_NAME=$subreddit_name \
  --restart always \
  $BOT_TYPE

echo "Container started successfully for r/$subreddit_name"
echo "Monthly jobs are handled by the bot's internal scheduler (APScheduler)"
