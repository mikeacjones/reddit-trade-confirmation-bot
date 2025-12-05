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

# Only create systemd timers if running on host (not in container)
if [ ! -f /.dockerenv ] && command -v systemctl &> /dev/null; then
  echo "Setting up systemd timers for r/$subreddit_name..."

  # Create the service file for creating the monthly post
  echo "[Unit]" >>/etc/systemd/system/$subreddit_name-monthly-post.service
  echo "Description=Creates monthly post for r/$subreddit_name" >>/etc/systemd/system/$subreddit_name-monthly-post.service
  echo "" >>/etc/systemd/system/$subreddit_name-monthly-post.service
  echo "[Service]" >>/etc/systemd/system/$subreddit_name-monthly-post.service
  echo "Type=oneshot" >>/etc/systemd/system/$subreddit_name-monthly-post.service
  echo "ExecStart=docker exec $subreddit_name python3 bot.py create-monthly" >>/etc/systemd/system/$subreddit_name-monthly-post.service

  # Create the timer file for creating the monthly post
  echo "[Unit]" >>/etc/systemd/system/$subreddit_name-monthly-post.timer
  echo "Description=Trigger for monthly post for r/$subreddit_name" >>/etc/systemd/system/$subreddit_name-monthly-post.timer
  echo "" >>/etc/systemd/system/$subreddit_name-monthly-post.timer
  echo "[Timer]" >>/etc/systemd/system/$subreddit_name-monthly-post.timer
  echo "OnCalendar=monthly" >>/etc/systemd/system/$subreddit_name-monthly-post.timer
  echo "Persistent=true" >>/etc/systemd/system/$subreddit_name-monthly-post.timer
  echo "" >>/etc/systemd/system/$subreddit_name-monthly-post.timer
  echo "[Install]" >>/etc/systemd/system/$subreddit_name-monthly-post.timer
  echo "WantedBy=timers.target" >>/etc/systemd/system/$subreddit_name-monthly-post.timer

  # Create the service file for locking previous months
  echo "[Unit]" >>/etc/systemd/system/$subreddit_name-lock-post.service
  echo "Description=Locks old posts for r/$subreddit_name" >>/etc/systemd/system/$subreddit_name-lock-post.service
  echo "" >>/etc/systemd/system/$subreddit_name-lock-post.service
  echo "[Service]" >>/etc/systemd/system/$subreddit_name-lock-post.service
  echo "Type=oneshot" >>/etc/systemd/system/$subreddit_name-lock-post.service
  echo "ExecStart=docker exec $subreddit_name python3 bot.py lock-submissions" >>/etc/systemd/system/$subreddit_name-lock-post.service

  # Create the timer file for locking previous months
  echo "[Unit]" >>/etc/systemd/system/$subreddit_name-lock-post.timer
  echo "Description=Locks old posts for r/$subreddit_name" >>/etc/systemd/system/$subreddit_name-lock-post.timer
  echo "" >>/etc/systemd/system/$subreddit_name-lock-post.timer
  echo "[Timer]" >>/etc/systemd/system/$subreddit_name-lock-post.timer
  echo "OnCalendar=*-*-05 00:00:00" >>/etc/systemd/system/$subreddit_name-lock-post.timer
  echo "Persistent=true" >>/etc/systemd/system/$subreddit_name-lock-post.timer
  echo "" >>/etc/systemd/system/$subreddit_name-lock-post.timer
  echo "[Install]" >>/etc/systemd/system/$subreddit_name-lock-post.timer
  echo "WantedBy=timers.target" >>/etc/systemd/system/$subreddit_name-lock-post.timer

  systemctl daemon-reload
  systemctl enable $subreddit_name-monthly-post.timer
  systemctl start $subreddit_name-monthly-post.timer
  echo "Systemd timers enabled and started."
else
  echo "Skipping systemd timer creation (not running on host or systemctl not available)."
  echo "Note: Monthly jobs must be scheduled externally (e.g., via reddit-bot-pipeline or cron)."
fi
