#!/bin/sh
set -e

echo "Running setup (schedules)..."
python -m temporal.starter setup

echo "Starting polling workflow..."
python -m temporal.starter start-polling

echo "Starting worker..."
exec python -m temporal.worker
