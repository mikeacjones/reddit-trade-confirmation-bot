package shared

import (
	"time"

	"go.temporal.io/sdk/temporal"
)

const WatermarkIDsMax = 1000

var (
	RedditRetry = &temporal.RetryPolicy{
		InitialInterval:        time.Second,
		MaximumInterval:        600 * time.Second,
		BackoffCoefficient:     2.0,
		NonRetryableErrorTypes: []string{"NonRetryableError"},
	}

	RedditRetryConservative = &temporal.RetryPolicy{
		InitialInterval:        time.Second,
		MaximumInterval:        30 * time.Second,
		MaximumAttempts:        3,
		BackoffCoefficient:     2.0,
		NonRetryableErrorTypes: []string{"NonRetryableError"},
	}

	PushoverRetry = &temporal.RetryPolicy{
		InitialInterval:    time.Second,
		MaximumInterval:    30 * time.Second,
		MaximumAttempts:    3,
		BackoffCoefficient: 2.0,
	}

	DeploymentRetry = &temporal.RetryPolicy{
		InitialInterval:    2 * time.Second,
		MaximumInterval:    30 * time.Second,
		MaximumAttempts:    3,
		BackoffCoefficient: 2.0,
	}
)
