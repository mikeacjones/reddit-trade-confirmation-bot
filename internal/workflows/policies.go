package workflows

import (
	"time"

	"go.temporal.io/sdk/temporal"
)

const watermarkIDsMax = 1000

var (
	redditRetry = &temporal.RetryPolicy{
		InitialInterval:        time.Second,
		MaximumInterval:        600 * time.Second,
		BackoffCoefficient:     2.0,
		NonRetryableErrorTypes: []string{"NonRetryableError"},
	}

	redditRetryConservative = &temporal.RetryPolicy{
		InitialInterval:        time.Second,
		MaximumInterval:        30 * time.Second,
		MaximumAttempts:        3,
		BackoffCoefficient:     2.0,
		NonRetryableErrorTypes: []string{"NonRetryableError"},
	}

	pushoverRetry = &temporal.RetryPolicy{
		InitialInterval:    time.Second,
		MaximumInterval:    30 * time.Second,
		MaximumAttempts:    3,
		BackoffCoefficient: 2.0,
	}

	deploymentRetry = &temporal.RetryPolicy{
		InitialInterval:    2 * time.Second,
		MaximumInterval:    30 * time.Second,
		MaximumAttempts:    3,
		BackoffCoefficient: 2.0,
	}
)
