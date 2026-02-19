# Temporal-Based Trade Confirmation Bot

A Reddit trade confirmation bot using [Temporal](https://temporal.io/) for workflow orchestration.

## Architecture

```
src/temporal/
├── __init__.py
├── shared.py              # Configuration, data classes, retry policies
├── worker.py              # Temporal worker (main entry point)
├── starter.py             # CLI for schedule setup and workflow triggers
├── activities/
│   ├── __init__.py
│   ├── reddit.py          # Reddit client utilities (singleton praw instance)
│   ├── comments.py        # Comment fetching, validation, replies
│   ├── flair.py           # User flair management
│   ├── temporal_bridge.py # Temporal client bridge for coordinator updates
│   ├── submissions.py     # Monthly post creation, locking
│   ├── notifications.py   # Pushover notifications
│   └── helpers.py         # Template loading utilities
└── workflows/
    ├── __init__.py
    ├── comment_processing.py  # CommentPollingWorkflow, ProcessConfirmationWorkflow
    ├── flair_coordinator.py   # FlairCoordinatorWorkflow
    ├── monthly_post.py        # MonthlyPostWorkflow
    └── lock_submissions.py    # LockSubmissionsWorkflow
```

## Prerequisites

1. **Temporal Server** running locally:
   ```bash
   # Using Docker
   docker run -d --name temporal \
     -p 7233:7233 \
     -p 8233:8233 \
     temporalio/auto-setup:latest

   # Or using Temporal CLI
   temporal server start-dev --ui-port 8233
   ```

2. **Environment variables:**
   ```bash
   # Required
   export SUBREDDIT_NAME=yoursubreddit
   export REDDIT_CLIENT_ID=xxx
   export REDDIT_CLIENT_SECRET=xxx
   export REDDIT_USERNAME=xxx
   export REDDIT_PASSWORD=xxx
   export REDDIT_USER_AGENT=xxx

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

# Start Temporal server (see Prerequisites)

# Start the worker
cd src
python -m temporal.worker

# Set up schedules (one-time)
python -m temporal.starter setup

# Start polling for comments
python -m temporal.starter start-polling

# View in Temporal UI
open http://localhost:8233
```

## CLI Commands

```bash
python -m temporal.starter <command>
```

| Command | Description |
|---------|-------------|
| `setup` | Create scheduled workflows (run once) |
| `start-polling` | Start comment polling workflow |
| `create-monthly` | Manually trigger monthly post creation |
| `lock-submissions` | Manually trigger submission locking |
| `status` | Show polling workflow status and schedules |

## Workflows

### CommentPollingWorkflow

Continuously polls for new comments across the subreddit.

- Polls every 30 seconds (configurable)
- Filters to comments on unlocked bot submissions
- Starts independent `ProcessConfirmationWorkflow` executions for each comment
- Uses comment ID as workflow ID for idempotency

**Signals:** `stop()` - Gracefully stop polling

**Queries:** `get_status()` - Returns `{last_seen_id, processed_count, running}`

### ProcessConfirmationWorkflow

Processes a single comment for trade confirmation.

1. Validates the comment via `validate_confirmation`
2. Marks comment as saved
3. On invalid with reason: replies with error template
4. On valid: requests flair increments through `FlairCoordinatorWorkflow`, then posts confirmation reply

**Returns:** `{status, comment_id, parent_author, confirmer, flair_changes}`

### FlairCoordinatorWorkflow

Centralized long-running workflow that processes flair increment requests one-at-a-time.

- Deduplicates by request ID
- Performs `get_user_flair` then deterministic `set_user_flair`
- Uses continue-as-new to cap event history

### MonthlyPostWorkflow

Creates the monthly confirmation thread.

- **Schedule:** 1st of each month at 00:00 UTC
- Unstickies previous post
- Creates new post (idempotent - checks if already exists)
- Sends Pushover notifications

### LockSubmissionsWorkflow

Locks old confirmation threads.

- **Schedule:** 5th of each month at 00:00 UTC
- Locks all non-stickied bot submissions

## Activities

### Comment Activities (`comments.py`)

| Activity | Description |
|----------|-------------|
| `fetch_new_comments(last_seen_id)` | Fetch new comments from subreddit, filtered to bot submissions |
| `validate_confirmation(comment_data)` | Validate a confirmation comment |
| `mark_comment_saved(comment_id)` | Mark comment as processed |
| `reply_to_comment(comment_id, template_name, format_args)` | Reply with template |
| `post_confirmation_reply(...)` | Post trade confirmation with flair info |

### Flair Activities (`flair.py`)

| Activity | Description |
|----------|-------------|
| `get_user_flair(username)` | Get user's current flair text and trade count |
| `set_user_flair(username, new_count)` | Set user's flair to exact trade count (idempotent) |

### Temporal Bridge Activities (`temporal_bridge.py`)

| Activity | Description |
|----------|-------------|
| `request_flair_increment(request)` | Update-with-start call into `FlairCoordinatorWorkflow` |
| `start_confirmation_workflow(workflow_id, comment_data)` | Starts independent `ProcessConfirmationWorkflow` by ID |

### Submission Activities (`submissions.py`)

| Activity | Description |
|----------|-------------|
| `create_monthly_post()` | Create monthly confirmation thread |
| `unsticky_previous_post()` | Unsticky the previous bot submission |
| `lock_previous_submissions()` | Lock all non-stickied bot submissions |

### Notification Activities (`notifications.py`)

| Activity | Description |
|----------|-------------|
| `send_pushover_notification(message)` | Send Pushover notification |

## Comment Filtering

The `fetch_new_comments` activity filters comments:

- **Skipped:** Already saved, not on bot submission, on locked submission, removed, bot's own
- **Root comments in current thread:** Skipped (not confirmations)
- **Root comments in old threads:** Processed with `old_confirmation_thread` error
- **Non-root without "confirmed"/"approved":** Marked saved and skipped

## Validation Rules

A confirmation is valid when:

- Comment contains "confirmed" (case-insensitive)
- Parent comment exists and is not removed
- Parent author is valid (not bot, not suspended)
- Not self-confirmation
- Parent not already confirmed (checked via saved flag)
- Confirmer username mentioned in parent comment body

**Moderator approval:** A mod can reply "approved" to a confirmation comment to manually approve it.

## Templates

Templates are loaded from:
1. Subreddit wiki: `trade-confirmation-bot/{template_name}`
2. Fallback: `src/mdtemplates/{template_name}.md`

Templates are cached after first load. All templates use Python's `str.format()` syntax (`{variable_name}`). Extra variables passed to the template are silently ignored.

If a wiki template fails to format (e.g. it references a variable name that no longer exists), the bot logs a warning, falls back to the local file for that template, and updates the in-process cache so subsequent calls use the local file without hitting the wiki again. If the local file also fails to format, the error propagates normally.

### `trade_confirmation`

Posted as a reply after a trade is successfully confirmed.

| Variable | Description |
|----------|-------------|
| `{confirmer}` | Username of the person who wrote "confirmed" |
| `{parent_author}` | Username of the person whose comment was confirmed |
| `{old_comment_flair}` | Confirmer's flair text before the increment |
| `{new_comment_flair}` | Confirmer's flair text after the increment |
| `{old_parent_flair}` | Parent author's flair text before the increment |
| `{new_parent_flair}` | Parent author's flair text after the increment |
| `{comment_id}` | Reddit ID of the confirming comment |

### `already_confirmed`, `cant_confirm_username`, `old_confirmation_thread`

Posted as error replies. All three receive the full set of data from the triggering comment. Two also include parent comment data that was already fetched during validation — no extra API calls are made.

**Available in all three:**

| Variable | Description |
|----------|-------------|
| `{author_name}` | Username of the commenter |
| `{id}` | Reddit comment ID |
| `{permalink}` | Comment permalink |
| `{body}` | Comment body text |
| `{body_html}` | Comment body as HTML |
| `{author_flair_text}` | Commenter's current flair text (may be `None`) |
| `{created_utc}` | Comment creation timestamp (Unix epoch float) |
| `{is_root}` | `True` if the comment is top-level |
| `{parent_id}` | Fullname of the parent comment or submission (e.g. `t1_abc123`) |
| `{submission_id}` | ID of the parent submission |
| `{saved}` | `True` if the comment has been marked as processed |
| `{submission_stickied}` | `True` if the parent submission is the current stickied thread |

**Also available in `already_confirmed` and `cant_confirm_username`** (the parent comment is fetched during validation for these cases; `old_confirmation_thread` fires before any parent fetch since the triggering comment is top-level):

| Variable | Description |
|----------|-------------|
| `{parent_author}` | Username of the parent comment's author |
| `{parent_comment_id}` | Reddit ID of the parent comment |

### `monthly_post`

Body of the monthly confirmation thread. Uses `str.format()`.

| Variable | Description |
|----------|-------------|
| `{bot_name}` | Bot's Reddit username |
| `{subreddit_name}` | Subreddit name (from `SUBREDDIT_NAME` env var) |
| `{submission.title}` | Previous month's thread title |
| `{submission.permalink}` | Previous month's thread permalink |
| `{previous_month_submission.title}` | Same as `{submission.title}` |
| `{previous_month_submission.permalink}` | Same as `{submission.permalink}` |
| `{now}` | Current UTC `datetime` object |

### `monthly_post_title`

Title of the monthly confirmation thread. Uses Python's `strftime()` format codes rather than `str.format()`, so use `%B`, `%Y`, etc.

| Code | Example output |
|------|----------------|
| `%B` | `January` |
| `%Y` | `2025` |
| `%m` | `01` |

## Error Handling

- **Activity failures:** Automatic retries with exponential backoff (1s→30s)
- **Non-retryable errors:** TypeError, ValueError, prawcore.Forbidden, etc.
- **Workflow failures:** Visible in Temporal UI with full history
- **Worker crashes:** Workflows automatically resume on restart

## Monitoring

The Temporal Web UI (http://localhost:8233) provides:

- Running/completed workflow list
- Full execution history
- Activity inputs/outputs
- Error messages and stack traces
- Schedule status and history
