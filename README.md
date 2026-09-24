# Reddit Trade Confirmation Bot

A Go bot that handles trade confirmations for swap subreddits on Reddit. It creates a monthly pinned post where users can confirm trades by tagging each other. When one user posts a comment tagging another user and that user replies "confirmed", the bot increments both users' trade counts.

## How It Works

1. Bot creates and pins a monthly confirmation thread
2. User A posts a comment tagging User B (e.g., "Sold item to u/UserB")
3. User B replies "confirmed"
4. Bot validates the confirmation and increments both users' flair counts

## Requirements

- Go 1.26+
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
export TEMPORAL_SDK_METRICS_BIND_ADDRESS=0.0.0.0:9000
export MONTHLY_POST_FLAIR_ID=xxx
export PUSHOVER_APP_TOKEN=xxx
export PUSHOVER_USER_TOKEN=xxx
```

## Quick Start

```bash
# Build
go build -o reddit-bot ./cmd/bot

# Start Temporal server
temporal server start-dev --ui-port 8233

# Start the worker (also run setup + start-polling via entrypoint, or separately)
./reddit-bot setup
./reddit-bot start-polling
./reddit-bot worker
```

Or with Docker:

```bash
docker build -t reddit-trade-confirmation-bot .
docker run --env-file .env reddit-trade-confirmation-bot
```

## CLI Commands

```bash
./reddit-bot <command>
```

| Command | Description |
|---------|-------------|
| `worker` | Start the Temporal worker |
| `setup` | Create scheduled workflows (run once) |
| `start-polling` | Start comment polling workflow |
| `create-monthly` | Manually trigger monthly post creation |
| `status` | Show polling workflow status and schedules |
| `deployment-signal-with-start <build-id>` | Signal Docker deployment cleanup |
| `deployment-current-build` | Print current Worker Deployment build ID |

## Architecture

```
cmd/bot/                 CLI entrypoint (worker + starter commands)
internal/
  activities/            Reddit + deployment activities
  workflows/             Temporal workflows
  reddit/                Minimal Reddit OAuth HTTP client (stdlib only)
  rules/                 Pure confirmation / flair business rules
  services/              Reply / flair request builders
  config/                Environment config
  metrics/               Stdlib Prometheus exposition for SDK metrics
mdtemplates/             Default reply / post templates
```

Direct dependencies: `go.temporal.io/sdk` (and `go.temporal.io/api`). Everything else is the Go standard library.

## Observability

When `TEMPORAL_SDK_METRICS_BIND_ADDRESS` is set (example: `0.0.0.0:9000`), the worker exposes Temporal SDK metrics at `/metrics`.

See [docs/observability/grafana.md](docs/observability/grafana.md).

## Customizing Messages

Override messages via subreddit wiki pages under `trade-confirmation-bot/` (for example `trade-confirmation-bot/monthly_post.md`). If a wiki template fails to format, the bot falls back to the local file in `mdtemplates/`.

| Template | Usage |
|----------|-------|
| [already_confirmed](mdtemplates/already_confirmed.md) | Trade already confirmed |
| [cant_confirm_username](mdtemplates/cant_confirm_username.md) | User not tagged in parent |
| [monthly_post_title](mdtemplates/monthly_post_title.md) | Monthly thread title (`%B`, `%Y`, …) |
| [monthly_post](mdtemplates/monthly_post.md) | Monthly thread content |
| [old_confirmation_thread](mdtemplates/old_confirmation_thread.md) | Trade in old thread |
| [trade_confirmation](mdtemplates/trade_confirmation.md) | Successful confirmation |

## Configuring Flair Templates

Create flair templates in the pattern `Trades: min-max` (for example `The Fountain Pen Fanatic | Trades: 650-9999`). Avoid overlapping ranges.
