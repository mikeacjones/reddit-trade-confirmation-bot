#!/bin/sh
set -e

echo "Running setup (search attributes + schedules)..."
/app/reddit-bot setup

echo "Starting polling workflow..."
/app/reddit-bot start-polling

echo "Starting worker..."
exec /app/reddit-bot worker
