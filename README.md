# Reddit Trade Confirmation Bot

A bot that handles trade confirmations for swap subreddits on Reddit. It creates a monthly pinned post where users can confirm trades by tagging each other. When one user posts a comment tagging another user and that user replies "confirmed", the bot increments both users' trade counts.

## How It Works

1. Bot creates and pins a monthly confirmation thread
2. User A posts a comment tagging User B (e.g., "Sold item to u/UserB")
3. User B replies "confirmed"
4. Bot validates the confirmation and increments both users' flair counts

## Requirements

- Python 3.12+
- [Temporal](https://temporal.io/) server
- Reddit bot account with appropriate permissions

## Environment Variables

```bash
# Required
export SUBREDDIT_NAME=yoursubreddit
export REDDIT_CLIENT_ID=xxx
export REDDIT_CLIENT_SECRET=xxx
export REDDIT_USERNAME=xxx
export REDDIT_PASSWORD=xxx
export REDDIT_USER_AGENT="trade confirmation bot v1.0"

# Optional
export TEMPORAL_HOST=localhost:7233
export MONTHLY_POST_FLAIR_ID=xxx
export PUSHOVER_APP_TOKEN=xxx
export PUSHOVER_USER_TOKEN=xxx
```

## Quick Start

```bash
# Install dependencies
pip install -r src/temporal/requirements.txt

# Start Temporal server
temporal server start-dev --ui-port 8233

# Start the worker
cd src
python -m temporal.worker

# Set up schedules (one-time)
python -m temporal.starter setup

# Start polling for comments
python -m temporal.starter start-polling
```

See [src/temporal/README.md](src/temporal/README.md) for detailed documentation.

## Customizing Messages

The bot replies with certain messages based on interactions with Redditors. The easiest way to override these messages is by hosting the configuration in your subreddit itself! If a wiki template fails to format correctly (e.g. it references an old or misspelled variable name), the bot will log a warning and fall back to the default local template automatically. You can do this by create Wiki entries. All wiki entries should be under the parent entry of `trade-confirmation-bot`. For example, to override the content of the monthly post, you would create a wiki page `trade-confirmation-bot/monthly_post.md` and `trade-confirmation-bot/monthly_post_title.md`. I recommend using the Wiki pages to control this rather than overriding the default MD pages in the bot itself, as this allows fellow moderators control over the messages.

| Template | Usage | Variables |
|----------|-------|-----------|
| [already_confirmed](src/mdtemplates/already_confirmed.md) | Trade already confirmed | *(comment variables — see below)* |
| [cant_confirm_username](src/mdtemplates/cant_confirm_username.md) | User not tagged in parent | *(comment variables — see below)* |
| [monthly_post_title](src/mdtemplates/monthly_post_title.md) | Monthly thread title | strftime codes (e.g. `%B`, `%Y`) |
| [monthly_post](src/mdtemplates/monthly_post.md) | Monthly thread content | `{bot_name}`, `{subreddit_name}`, `{submission.title}`, `{submission.permalink}`, `{previous_month_submission.title}`, `{previous_month_submission.permalink}`, `{now}` |
| [old_confirmation_thread](src/mdtemplates/old_confirmation_thread.md) | Trade in old thread | *(comment variables — see below)* |
| [trade_confirmation](src/mdtemplates/trade_confirmation.md) | Successful confirmation | `{confirmer}`, `{parent_author}`, `{old_comment_flair}`, `{new_comment_flair}`, `{old_parent_flair}`, `{new_parent_flair}`, `{comment_id}` |

The three error reply templates (`already_confirmed`, `cant_confirm_username`, `old_confirmation_thread`) receive the full set of comment variables, plus parent comment info where it was already fetched during validation:

| Variable | Description | Available in |
|----------|-------------|--------------|
| `{author_name}` | Username of the commenter | all |
| `{id}` | Reddit comment ID | all |
| `{permalink}` | Comment permalink | all |
| `{body}` | Comment body text | all |
| `{body_html}` | Comment body as HTML | all |
| `{author_flair_text}` | Commenter's current flair text | all |
| `{created_utc}` | Comment creation timestamp (Unix epoch) | all |
| `{is_root}` | Whether the comment is top-level | all |
| `{parent_id}` | Fullname of the parent comment or submission | all |
| `{submission_id}` | ID of the parent submission | all |
| `{parent_author}` | Username of the parent comment's author | `already_confirmed`, `cant_confirm_username` |
| `{parent_comment_id}` | Reddit ID of the parent comment | `already_confirmed`, `cant_confirm_username` |

## Configuring Flair Templates

The bot requires that you create flair templates in your subreddit for it to assign to users. You should create these flairs and set it in such a way that users can not assign them to themselves.

When creating the flair, you must set the flair in the pattern of `Trades: min-max`. You can put any other text. For example, if I wanted to set a flair for anyone with over 650 confirmed trades, I could create a user flair template with the text `The Fountain Pen Fanatic | Trades: 650-9999`. This allows me to control the color and text color of the flair. 

When creating flairs, avoid overlapping ranges. Example:

- `Trades: 0-1`
- `Trades: 2-10`
- `Trades: 11-50`
- `Trades: 51-100`