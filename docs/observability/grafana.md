# Grafana Dashboards for Reddit Trade Confirmation Bot

This guide focuses on useful Temporal dashboards for this bot:

- Temporal Server health/capacity
- Temporal Python SDK worker health
- Bot workflow and activity outcomes

## 1. Expose Metrics

Set this before starting the worker:

```bash
export TEMPORAL_SDK_METRICS_BIND_ADDRESS=0.0.0.0:9000
```

The worker will then expose SDK metrics at `http://<worker-host>:9000/metrics`.

For Temporal Server metrics in local dev, run the dev server with a fixed metrics port:

```bash
temporal server start-dev --ui-port 8233 --metrics-port 9001
```

## 2. Prometheus Scrape Config

Example `prometheus.yml` scrape jobs:

```yaml
scrape_configs:
  - job_name: temporal-server
    static_configs:
      - targets: ["<temporal-host>:9001"]

  - job_name: trade-confirmation-bot
    static_configs:
      - targets: ["<worker-host>:9000"]
```

## 3. Import Baseline Dashboards

Temporal maintains official dashboard templates:

- Server General:
  `https://github.com/temporalio/dashboards/blob/v0.1.8/server/server-general.json`
- Python SDK:
  `https://github.com/temporalio/dashboards/blob/v0.1.8/sdk/python/python-sdk.json`

If you prefer `curl` + import file:

```bash
curl -L -o server-general.json \
  https://raw.githubusercontent.com/temporalio/dashboards/v0.1.8/server/server-general.json

curl -L -o python-sdk.json \
  https://raw.githubusercontent.com/temporalio/dashboards/v0.1.8/sdk/python/python-sdk.json
```

## 4. Build a Bot-Focused Dashboard

Create these Grafana template variables (Prometheus data source):

- `namespace`: `label_values(service_requests, namespace)`
- `task_queue`: `label_values(temporal_workflow_completed{namespace=~"$namespace"}, task_queue)`
- `subreddit`: `label_values(temporal_request_latency_count, subreddit)`

Recommended panels (PromQL):

1. Workflow completions by type (rate)

```promql
sum by (workflow_type) (
  rate(temporal_workflow_completed{
    namespace=~"$namespace",
    task_queue=~"$task_queue",
    subreddit=~"$subreddit"
  }[$__rate_interval])
)
```

2. Workflow failures by type (rate)

```promql
sum by (workflow_type) (
  rate(temporal_workflow_failed{
    namespace=~"$namespace",
    task_queue=~"$task_queue",
    subreddit=~"$subreddit"
  }[$__rate_interval])
)
```

3. Continue-as-new events by workflow type

```promql
sum by (workflow_type) (
  rate(temporal_workflow_continue_as_new{
    namespace=~"$namespace",
    task_queue=~"$task_queue",
    subreddit=~"$subreddit"
  }[$__rate_interval])
)
```

4. Activity failures by activity type (rate)

```promql
sum by (activity_type) (
  rate(temporal_activity_execution_failed{
    namespace=~"$namespace",
    task_queue=~"$task_queue",
    subreddit=~"$subreddit"
  }[$__rate_interval])
)
```

5. Activity execution latency p95

```promql
histogram_quantile(
  0.95,
  sum by (le, activity_type) (
    rate(temporal_activity_execution_latency_bucket{
      namespace=~"$namespace",
      task_queue=~"$task_queue",
      subreddit=~"$subreddit"
    }[$__rate_interval])
  )
)
```

6. Workflow end-to-end latency p95

```promql
histogram_quantile(
  0.95,
  sum by (le, workflow_type) (
    rate(temporal_workflow_endtoend_latency_bucket{
      namespace=~"$namespace",
      task_queue=~"$task_queue",
      subreddit=~"$subreddit"
    }[$__rate_interval])
  )
)
```

7. Workflow task replay latency p95

```promql
histogram_quantile(
  0.95,
  sum by (le, workflow_type) (
    rate(temporal_workflow_task_replay_latency_bucket{
      namespace=~"$namespace",
      task_queue=~"$task_queue",
      subreddit=~"$subreddit"
    }[$__rate_interval])
  )
)
```

8. Worker poll health (successful vs empty polls)

```promql
sum(
  rate(temporal_workflow_task_queue_poll_succeed{
    namespace=~"$namespace",
    task_queue=~"$task_queue",
    subreddit=~"$subreddit"
  }[$__rate_interval])
)
```

```promql
sum(
  rate(temporal_workflow_task_queue_poll_empty{
    namespace=~"$namespace",
    task_queue=~"$task_queue",
    subreddit=~"$subreddit"
  }[$__rate_interval])
)
```

9. Temporal server frontend request errors (rate)

```promql
sum by (operation, error_type) (
  rate(service_error_with_type{
    service_name="frontend",
    namespace=~"$namespace"
  }[$__rate_interval])
)
```

10. Temporal server frontend p95 latency by operation

```promql
histogram_quantile(
  0.95,
  sum by (le, operation) (
    rate(service_latency_bucket{
      service_name="frontend",
      namespace=~"$namespace"
    }[$__rate_interval])
  )
)
```

## 5. High-Value Alerts

1. Worker stopped polling task queue

```promql
sum(rate(temporal_workflow_task_queue_poll_succeed{
  namespace=~"$namespace",
  task_queue=~"$task_queue",
  subreddit=~"$subreddit"
}[5m])) < 0.01
```

2. Workflow failures sustained

```promql
sum(rate(temporal_workflow_failed{
  namespace=~"$namespace",
  task_queue=~"$task_queue",
  subreddit=~"$subreddit"
}[5m])) > 0
```

3. Activity failure ratio over 5%

```promql
sum(rate(temporal_activity_execution_failed{
  namespace=~"$namespace",
  task_queue=~"$task_queue",
  subreddit=~"$subreddit"
}[5m]))
/
clamp_min(
  sum(rate(temporal_activity_execution_latency_count{
    namespace=~"$namespace",
    task_queue=~"$task_queue",
    subreddit=~"$subreddit"
  }[5m])),
  1
) > 0.05
```

## 6. Notes

- The worker sets SDK metric tags `app="reddit-trade-confirmation-bot"` and
  `subreddit="<SUBREDDIT_NAME>"`.
- Keep the official Temporal dashboards as your base and add the bot-focused
  panels above in a separate dashboard for easier maintenance.
