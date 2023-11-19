#! /bin/bash

yum update -y
yum install docker -y
yum install git -y

systemctl enable docker.service
systemctl start docker.service

REPO=reddit-trade-confirmation-bot
git clone https://github.com/mikeacjones/$REPO
cd $REPO
docker build . -t trade-confirmation-bot

secrets_list=$(aws secretsmanager list-secrets --filter Key="name",Values="trade-confirmation-bot")

if [ $? -ne 0 ]; then
  echo "Error listing secrets"
  exit 1
fi

secret_names=$(echo "$secrets_list" | jq -r '.SecretList[] | select(.Name | contains("trade-confirmation-bot/")) | .Name | split("/") | .[1]')

for subreddit_name in $secret_names; do
  docker run \
    --name $subreddit_name \
    -d \
    -e AWS_DEFAULT_REGION='us-east-2' \
    -e SUBREDDIT_NAME=$subreddit_name \
    --restart always \
    trade-confirmation-bot

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
done
