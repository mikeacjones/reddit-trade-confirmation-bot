[![deploy-bot](https://github.com/mikeacjones/reddit-trade-confirmation-bot/actions/workflows/deploy.yml/badge.svg)](https://github.com/mikeacjones/reddit-trade-confirmation-bot/actions/workflows/deploy.yml)

# Reddit Trade Confirmation Bot

This bot handles trade confirmations for swap subreddits on reddit.com. It does this by creating and pinning a monthly post, on which Redditors can post a comment tagging another Redditor. When that other Redditor replies "confirmed", the bot will evaluate the two users, and increment their trade counts if it is able to find a post created by one of the Redditors, which was commented on by the other. Eg: Redditor 1 creates a WTS post, and Redditor 2 commented "sending a PM for item X".


These instructions are minimal and assume you're already familiar with create a Reddit bot account.

# Deploying

The bot was designed with the assumption that you will be hosting it in AWS. As of right now, all config is hosted in AWS secrets manager and pulled in by the bot on startup. If you want to migrate to another hosting platform, will need to find an alternative for passing in the config.

#### Create Secret

In AWS Secrets Manager, create a new secret for the bot to pull configuration from.

1. Name secret in pattern `trade-confirmation-bot/<subreddit>`. Eg: `trade-confirmation-bot/pen_swap`
2. Set secret type to "Other type of secret"
3. Change the input to Plaintext, and paste and populate the following secret:

```json
{
  "REDDIT_CLIENT_ID": "",
  "REDDIT_CLIENT_SECRET": "",
  "REDDIT_USERNAME": "",
  "REDDIT_PASSWORD": "",
  "REDDIT_USER_AGENT": "trade swap bot v1.0 by u/thisisreallytricky",
  "OPENAI_API_KEY": "",
  "PUSHOVER_APP_TOKEN": "",
  "PUSHOVER_USER_TOKEN": "",
  "MONTHLY_POST_FLAIR_ID": ""
}
```

#### Deploying the bot

1. Run `docker build . -t trade-confirmation-bot` in order to build the image
2. Run `docker run -d -e AWS_DEFAULT_REGION='<region>' -e SUBREDDIT_NAME=<subreddit> trade-confirmation-bot`

By default, the container assumes it is running with an attached IAM role. The `AWS_DEFAULT_REGION` is used to control where the bot looks for the configuration value. The bot will automatically look for the configuration based on the subreddit name, in the pattern of `trade-confirmation-bot/$SUBREDDIT_NAME`.

Example IAM policy for bot:

```json
{
    "Statement": [
        {
            "Action": [
                "secretsmanager:GetSecretValue"
            ],
            "Effect": "Allow",
            "Resource": "arn:aws:secretsmanager:us-east-2:111111111111:secret:trade-confirmation-bot/pen_swap*"
        }
    ],
    "Version": "2012-10-17"
}
```

#### Overriding Default Messages

The bot replies with certain messages based on interactions with Redditors. The easiest way to override these messages is by hosting the configuration in your subreddit itself! You can do this by create Wiki entries. All wiki entries should be under the parent entry of `trade-confirmation-bot`. For example, to override the content of the monthly post, you would create a wiki page `trade-confirmation-bot/monthly_post.md` and `trade-confirmation-bot/monthly_post_title.md`. I recommend using the Wiki pages to control this rather than overriding the default MD pages in the bot itself, as this allows fellow moderators control over the messages.

The following pages can be created for overrides:

| Page Name              | When is this message used?                                                                                    | Variables available |
| ---------------------- | ------------------------------------------------------------------------------------------------------------- | ----------- |
| [already_confirmed](src/mdtemplates/already_confirmed.md)      | When a user attempts to confirm a trade which has already been confirmed by the bot                           | [`comment`](https://praw.readthedocs.io/en/stable/code_overview/models/comment.html) [`parent_comment`](https://praw.readthedocs.io/en/stable/code_overview/models/comment.html) |
| [cant_confirm_username](src/mdtemplates/cant_confirm_username.md)  | When a user attempts to confirm a trade where they were not tagged                                            | [`comment`](https://praw.readthedocs.io/en/stable/code_overview/models/comment.html) [`parent_comment`](https://praw.readthedocs.io/en/stable/code_overview/models/comment.html) |
| [monthly_post_title](src/mdtemplates/monthly_post_title.md)     | When the bot creates the monthly post thread, this is used for the title                                      | [`now`](https://docs.python.org/3/library/datetime.html)                      |
| [monthly_post](src/mdtemplates/monthly_post.md)           | When the bot creates the monthly post, this is used for the content                                           | [`bot_name`](https://docs.python.org/3/library/string.html#module-string) [`subreddit_name`](https://docs.python.org/3/library/string.html#module-string) [`previous_month_submission`](https://praw.readthedocs.io/en/latest/code_overview/models/submission.html) [`now`](https://docs.python.org/3/library/datetime.html) |
| [no_history](src/mdtemplates/no_history.md)             | When two users are attempting to confirm a trade, but the bot can't find a WTB/WTS post where they interacted | [`comment`](https://praw.readthedocs.io/en/stable/code_overview/models/comment.html) [`parent_comment`](https://praw.readthedocs.io/en/stable/code_overview/models/comment.html) |
| [old_confirmaton_thread](src/mdtemplates/old_confirmation_thread.md) | When a user attempts to initiate a trade in a previous month's trade thread                                   | [`comment`](https://praw.readthedocs.io/en/stable/code_overview/models/comment.html) |
| [trade_confirmation](src/mdtemplates/trade_confirmation.md)     | When users have successfully confirmed their trades                                                           | [`comment`](https://praw.readthedocs.io/en/stable/code_overview/models/comment.html) [`parent_comment`](https://praw.readthedocs.io/en/stable/code_overview/models/comment.html) [`new_parent_flair`](https://docs.python.org/3/library/string.html#module-string) [`new_comment_flair`](https://docs.python.org/3/library/string.html#module-string) |

#### Configuring Flair Templates

The bot requires that you create flair templates in your subreddit for it to assign to users. You should create these flairs and set it in such a way that users can not assign them to themselves.

When creating the flair, you must set the flair in the pattern of `Trades: min-max`. You can put any other text. For example, if I wanted to set a flair for anyone with over 650 confirmed trades, I could create a user flair template with the text `The Fountain Pen Fanatic | Trades: 650-9999`. This allows me to control the color and text color of the flair. 

When creating flairs, avoid overlapping flairs. For example, you might create the following flairs:

`Trades: 0-1`

`Trades: 2-10`

`Trades: 11-50`

`Trades: 51-100` 

etc