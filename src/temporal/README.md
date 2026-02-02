# Temporal-Based Trade Confirmation Bot

This is a reimplementation of the Reddit trade confirmation bot using [Temporal](https://temporal.io/) for workflow orchestration.

## Benefits Over Original Implementation

| Feature | Original | Temporal |
|---------|----------|----------|
| State persistence | Manual JSON file + Reddit `.save()` | Automatic workflow state |
| Idempotency | Manual checks | Workflow IDs guarantee exactly-once |
| Catch-up on restart | Manual `catch_up_on_missed_comments()` | Automatic workflow resume |
| Scheduled jobs | APScheduler | Temporal Schedules |
| Retries | Manual try/catch | Declarative retry policies |
| Visibility | Log files | Temporal Web UI |
| Error handling | Manual logging + Pushover | Built-in + notifications |

## Architecture

```
src/temporal/
├── __init__.py
├── shared.py              # Configuration, Reddit client, data classes
├── worker.py              # Temporal worker (main entry point)
├── starter.py             # Schedule setup and workflow triggers
├── activities/
│   ├── __init__.py
│   ├── reddit.py          # Reddit API activities
│   └── notifications.py   # Pushover activities
└── workflows/
    ├── __init__.py
    ├── comment_processing.py  # CommentPollingWorkflow, ProcessConfirmationWorkflow
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

2. **Environment variables** (same as original bot):
   ```bash
   export SUBREDDIT_NAME=yoursubreddit
   export REDDIT_CLIENT_ID=xxx
   export REDDIT_CLIENT_SECRET=xxx
   export REDDIT_USERNAME=xxx
   export REDDIT_PASSWORD=xxx
   export REDDIT_USER_AGENT=xxx

   # Optional
   export TEMPORAL_HOST=localhost:7233  # Default
   export PUSHOVER_APP_TOKEN=xxx
   export PUSHOVER_USER_TOKEN=xxx
   ```

## Quick Start

1. **Install dependencies:**
   ```bash
   pip install -r src/temporal/requirements.txt
   ```

2. **Start Temporal server** (see Prerequisites)

3. **Start the worker:**
   ```bash
   cd src
   python -m temporal.worker
   ```

4. **Set up schedules** (one-time):
   ```bash
   python -m temporal.starter setup
   ```

5. **Start polling for comments:**
   ```bash
   python -m temporal.starter start-polling
   ```

6. **View in Temporal UI:**
   Open http://localhost:8233

## Commands

```bash
# Start the worker (required)
python -m temporal.worker

# Set up scheduled workflows (run once)
python -m temporal.starter setup

# Start polling current submission
python -m temporal.starter start-polling

# Manually trigger monthly post
python -m temporal.starter create-monthly

# Manually trigger lock submissions
python -m temporal.starter lock-submissions

# Check status
python -m temporal.starter status
```

## Workflows

### CommentPollingWorkflow

Continuously polls for new comments across all bot submissions in the subreddit.

- Runs indefinitely with a configurable poll interval (default 30s)
- Monitors all submissions created by the bot (current and previous months)
- Spawns child `ProcessConfirmationWorkflow` for each new comment
- Uses comment ID as child workflow ID for idempotency
- Can be stopped via signal

**Signals:**
- `stop()` - Stop the polling loop

**Queries:**
- `get_status()` - Get current status (processed count, last seen ID)

### ProcessConfirmationWorkflow

Processes a single comment for potential trade confirmation.

- Validates the comment is a valid confirmation
- Updates both users' flairs
- Posts confirmation reply
- Handles error cases with template responses

### MonthlyPostWorkflow

Creates the monthly confirmation thread.

- Scheduled for 1st of each month at 00:00 UTC
- Checks for existing post (idempotency)
- Unstickies previous post
- Creates and configures new post
- Sends notifications

### LockSubmissionsWorkflow

Locks old confirmation threads.

- Scheduled for 5th of each month at 00:00 UTC
- Locks all non-stickied submissions

## Activities

All Reddit API calls are wrapped in activities for:
- Automatic retries with exponential backoff
- Timeout handling
- Clean separation of concerns

### Reddit Activities

- `fetch_new_comments(last_seen_id)` - Get new comments from bot submissions
- `validate_confirmation(comment_data)` - Validate a confirmation
- `update_user_flair(username)` - Increment user's trade flair
- `mark_comment_saved(comment_id)` - Mark comment as processed
- `reply_to_comment(comment_id, template_name, format_args)` - Reply with template
- `post_confirmation_reply(...)` - Post trade confirmation reply
- `check_monthly_post_exists()` - Check if monthly post exists
- `create_monthly_post()` - Create new monthly post
- `unsticky_previous_post()` - Unsticky old post
- `lock_previous_submissions()` - Lock old submissions

### Notification Activities

- `send_pushover_notification(message)` - Send Pushover notification

## Error Handling

- **Activity failures**: Automatic retries with exponential backoff (5 attempts)
- **Workflow failures**: Visible in Temporal UI with full history
- **Worker crashes**: Workflows automatically resume when worker restarts

## Monitoring

The Temporal Web UI (http://localhost:8233) provides:

- List of all running/completed workflows
- Full execution history
- Activity inputs/outputs
- Error messages and stack traces
- Schedule status and history

## Migration from Original Bot

The Temporal implementation is a drop-in replacement. Both can run simultaneously if needed during migration:

1. Stop the original bot
2. Start Temporal server
3. Start the worker
4. Set up schedules
5. Start polling

The Reddit API is the source of truth for state (`.save()` on comments), so there's no data migration needed.
