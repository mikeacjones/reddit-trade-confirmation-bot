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
│   ├── submissions.py     # Monthly post creation, locking
│   ├── notifications.py   # Pushover notifications
│   └── helpers.py         # Template loading utilities
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
- Spawns `ProcessConfirmationWorkflow` child for each comment
- Uses comment ID as workflow ID for idempotency

**Signals:** `stop()` - Gracefully stop polling

**Queries:** `get_status()` - Returns `{last_seen_id, processed_count, running}`

### ProcessConfirmationWorkflow

Processes a single comment for trade confirmation.

1. Validates the comment via `validate_confirmation`
2. On invalid with reason: replies with error template
3. On valid: reads current flairs, calculates new values, sets flairs, marks the root trade comment saved for dedupe, and posts confirmation reply

**Returns:** `{status, comment_id, parent_author, confirmer, flair_changes}`

On non-retryable processing failures, it sends a moderator notification and returns
`status: manual_review_required` so the child workflow ID is not relaunched automatically.
Cancellation/termination paths are propagated so Temporal still records canceled/terminated
workflow outcomes.

### Workflow Versioning Guards

Temporal workflow replay is deterministic, so changing workflow command order (activities,
child workflows, timers, continue-as-new, etc.) can break existing histories.
This project uses `workflow.patched(...)` guards in `comment_processing.py` to keep both
old and new command paths available during rollout:

- `comment-polling-behavior-v2-2026-02-10`
- `process-confirmation-behavior-v2-2026-02-10`

Cleanup progression:
1. Deploy with guards and monitor until pre-patch runs are drained.
2. For long-running polling workflows, ensure at least one full continue-as-new cycle.
3. Remove legacy branches and replace `workflow.patched(...)` with
   `workflow.deprecate_patch(...)`.
4. After all executions include the patch marker, remove the deprecate call and patch ID.

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
| `set_user_flair(username, new_count, old_flair)` | Set user's flair to exact trade count (idempotent) |

Users with custom non-trade flair are treated as untracked for count updates and their flair text is preserved.

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

- Uses paginated subreddit listing reads until it reaches the last-seen watermark
- **Skipped:** Already saved, not on bot submission, on locked submission, removed, bot's own
- **Root comments in current thread:** Skipped (not confirmations)
- **Root comments in old threads:** Replied to with `old_confirmation_thread` and locked
- **Non-root without "confirmed"/"approved":** Skipped
- If the listing is exhausted without finding the previous watermark, the workflow emits a warning/notification for potential listing-window gaps

## Validation Rules

A confirmation is valid when:

- Comment contains "confirmed" (case-insensitive)
- Parent comment exists and is not removed
- Parent author is valid (not bot)
- Not self-confirmation
- Parent not already confirmed (checked via saved flag)
- Confirmer username mentioned in parent comment body

**Moderator approval:** A mod can reply "approved" to a confirmation comment to manually approve it.

## Templates

Templates are loaded from:
1. Subreddit wiki: `trade-confirmation-bot/{template_name}`
2. Fallback: `src/mdtemplates/{template_name}.md`

Templates are cached after first load.

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
