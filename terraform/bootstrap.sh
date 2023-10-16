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

#echo '0 * * * * docker exec $(docker ps | grep trade-confirmation-bot | awk '\''{print $1}'\'') python3 bot.py create-monthly' >>dailycron.cron
#echo '0 0 5 * * docker exec $(docker ps | grep trade-confirmation-bot | awk '\''{print $1}'\'') python3 bot.py lock-submissions' >>dailycron.cron
#crontab -l -u root >>dailycron.cron
#crontab -u root dailycron.cron
#rm dailycron.cron

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
done
