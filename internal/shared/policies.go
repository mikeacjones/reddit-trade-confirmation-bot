package shared

import (
	"time"

	"go.temporal.io/sdk/temporal"
)

const WatermarkIDsMax = 1000

var nonRetryableErrors = []string{
	"TypeError",
	"ValueError",
	"KeyError",
	"AttributeError",
	"IndexError",
	"AssertionError",
	"*errors.errorString", // Go sentinel-style; activities should use typed non-retryable errors
}

// RedditRetryPolicy retries transient Reddit API failures.
func RedditRetryPolicy() *temporal.RetryPolicy {
	return &temporal.RetryPolicy{
		InitialInterval:        time.Second,
		MaximumInterval:        600 * time.Second,
		BackoffCoefficient:     2.0,
		NonRetryableErrorTypes: []string{"NonRetryableError"},
	}
}

// RedditRetryPolicyConservative is a shorter retry policy for less critical ops.
func RedditRetryPolicyConservative() *temporal.RetryPolicy {
	return &temporal.RetryPolicy{
		InitialInterval:        time.Second,
		MaximumInterval:        30 * time.Second,
		MaximumAttempts:        3,
		BackoffCoefficient:     2.0,
		NonRetryableErrorTypes: []string{"NonRetryableError"},
	}
}

// PushoverRetryPolicy is for non-critical notifications.
func PushoverRetryPolicy() *temporal.RetryPolicy {
	return &temporal.RetryPolicy{
		InitialInterval:    time.Second,
		MaximumInterval:    30 * time.Second,
		MaximumAttempts:    3,
		BackoffCoefficient: 2.0,
	}
}

// DeploymentRetryPolicy is a short retry for local Docker/deployment inspection.
func DeploymentRetryPolicy() *temporal.RetryPolicy {
	return &temporal.RetryPolicy{
		InitialInterval:    2 * time.Second,
		MaximumInterval:    30 * time.Second,
		MaximumAttempts:    3,
		BackoffCoefficient: 2.0,
	}
}

// Suppress unused warning for the shared list kept for documentation parity.
var _ = nonRetryableErrors
