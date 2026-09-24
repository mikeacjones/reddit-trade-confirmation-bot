package workflows

import (
	"fmt"
	"time"

	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/workflow"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/shared"
)

const maxFlairCache = 30

// FlairCoordinatorWorkflow serializes flair increments per-user.
func FlairCoordinatorWorkflow(ctx workflow.Context, carriedFlairCounts map[string]int) error {
	usersInProgress := map[string]struct{}{}
	draining := false
	lastKnownCount := map[string]int{}
	order := []string{} // LRU order, oldest first

	if carriedFlairCounts != nil {
		for k, v := range carriedFlairCounts {
			lastKnownCount[k] = v
			order = append(order, k)
		}
	}

	err := workflow.SetUpdateHandlerWithOptions(ctx, "apply_increment",
		func(ctx workflow.Context, req models.FlairIncrementRequest) (models.FlairIncrementResult, error) {
			username := req.Username
			for {
				if _, busy := usersInProgress[username]; !busy {
					break
				}
				u := username
				_ = workflow.Await(ctx, func() bool {
					_, busy := usersInProgress[u]
					return !busy
				})
			}
			usersInProgress[username] = struct{}{}
			defer delete(usersInProgress, username)

			if req.Delta == 0 {
				req.Delta = 1
			}

			ao := workflow.ActivityOptions{
				StartToCloseTimeout: 30 * time.Second,
				RetryPolicy:         shared.RedditRetryPolicy(),
				Summary:             req.Username,
			}
			var current models.UserFlairResult
			if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "get_user_flair", req.Username).Get(ctx, &current); err != nil {
				return models.FlairIncrementResult{}, err
			}

			if !current.IsTradeTracked || current.TradeCount == nil {
				return models.FlairIncrementResult{
					OldFlair: current.FlairText,
					NewFlair: current.FlairText,
				}, nil
			}

			apiCount := *current.TradeCount
			currentCount := apiCount
			if cached, ok := lastKnownCount[req.Username]; ok && cached > apiCount {
				currentCount = cached
			}
			targetCount := currentCount + req.Delta

			sao := workflow.ActivityOptions{
				StartToCloseTimeout: 30 * time.Second,
				RetryPolicy:         shared.RedditRetryPolicy(),
				Summary:             fmt.Sprintf("%s:%d", req.Username, targetCount),
			}
			var setResult models.FlairUpdateResult
			if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, sao), "set_user_flair", models.SetUserFlairInput{
				Username: req.Username,
				NewCount: targetCount,
				OldFlair: current.FlairText,
			}).Get(ctx, &setResult); err != nil {
				return models.FlairIncrementResult{}, err
			}

			lastKnownCount[req.Username] = targetCount
			// move to end of LRU
			order = removeFromOrder(order, req.Username)
			order = append(order, req.Username)
			for len(order) > maxFlairCache {
				oldest := order[0]
				order = order[1:]
				delete(lastKnownCount, oldest)
			}

			oldFlair := current.FlairText
			if oldFlair == nil {
				s := "Trades: 0"
				oldFlair = &s
			}
			return models.FlairIncrementResult{
				OldFlair: oldFlair,
				NewFlair: setResult.NewFlair,
			}, nil
		},
		workflow.UpdateHandlerOptions{
			Validator: func(req models.FlairIncrementRequest) error {
				if draining {
					return temporal.NewApplicationError("Workflow is draining for continue-as-new; retry", "Draining")
				}
				return nil
			},
		},
	)
	if err != nil {
		return err
	}

	_ = workflow.Await(ctx, func() bool {
		return workflow.GetInfo(ctx).GetContinueAsNewSuggested()
	})
	draining = true
	_ = workflow.Await(ctx, func() bool { return workflow.AllHandlersFinished(ctx) })

	return continueAsNewAutoUpgrade(ctx, FlairCoordinatorWorkflow, lastKnownCount)
}

func removeFromOrder(order []string, key string) []string {
	out := order[:0]
	for _, k := range order {
		if k != key {
			out = append(out, k)
		}
	}
	return out
}
