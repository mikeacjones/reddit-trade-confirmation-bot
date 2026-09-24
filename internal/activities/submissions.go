package activities

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"time"

	"go.temporal.io/sdk/activity"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/templates"
)

// FetchActiveSubmissionIDs discovers current and previous submission IDs from Reddit.
func (a *Activities) FetchActiveSubmissionIDs(ctx context.Context) (models.ActiveSubmissions, error) {
	subs, err := a.Reddit.BotSubmissions(5)
	if err != nil {
		return models.ActiveSubmissions{}, err
	}
	var current, previous *string
	for _, s := range subs {
		id := s.ID
		if s.Stickied && current == nil {
			current = &id
		} else if !s.Stickied && !s.Locked && previous == nil {
			previous = &id
		}
		if current != nil && previous != nil {
			break
		}
	}
	activity.GetLogger(ctx).Info("Discovered submissions",
		"current", ptrStr(current), "previous", ptrStr(previous))
	return models.ActiveSubmissions{
		CurrentSubmissionID:  current,
		PreviousSubmissionID: previous,
	}, nil
}

// StickySubmission stickies a submission (idempotent).
func (a *Activities) StickySubmission(ctx context.Context, input models.SubmissionInput) (bool, error) {
	sub, err := a.Reddit.GetSubmission(input.SubmissionID)
	if err != nil {
		return false, err
	}
	if sub != nil && !sub.Stickied {
		if err := a.Reddit.StickySubmission(input.SubmissionID, true); err != nil {
			return false, err
		}
		activity.GetLogger(ctx).Info("Stickied", "url", "https://reddit.com"+sub.Permalink)
	}
	return true, nil
}

// UnstickySubmission unstickies a submission (idempotent).
func (a *Activities) UnstickySubmission(ctx context.Context, input models.SubmissionInput) (bool, error) {
	sub, err := a.Reddit.GetSubmission(input.SubmissionID)
	if err != nil {
		return false, err
	}
	if sub != nil && sub.Stickied {
		if err := a.Reddit.StickySubmission(input.SubmissionID, false); err != nil {
			return false, err
		}
		activity.GetLogger(ctx).Info("Unstickied", "url", "https://reddit.com"+sub.Permalink)
	}
	return true, nil
}

// LockSubmission locks a submission (idempotent).
func (a *Activities) LockSubmission(ctx context.Context, input models.SubmissionInput) (bool, error) {
	sub, err := a.Reddit.GetSubmission(input.SubmissionID)
	if err != nil {
		return false, err
	}
	if sub != nil && !sub.Locked {
		if err := a.Reddit.LockSubmission(input.SubmissionID); err != nil {
			return false, err
		}
		activity.GetLogger(ctx).Info("Locked", "url", "https://reddit.com"+sub.Permalink)
	}
	return true, nil
}

// CreateMonthlyPost creates a monthly confirmation thread (idempotent).
func (a *Activities) CreateMonthlyPost(ctx context.Context, input models.CreateMonthlyPostInput) (string, error) {
	now := time.Now().UTC()
	botName, _, err := a.Reddit.BotUser()
	if err != nil {
		return "", err
	}

	var prevTitle, prevPermalink string
	var prevCreated float64
	var prevID string
	hasPrev := false

	if input.PreviousSubmissionID != nil && *input.PreviousSubmissionID != "" {
		sub, err := a.Reddit.GetSubmission(*input.PreviousSubmissionID)
		if err != nil {
			return "", err
		}
		if sub != nil {
			prevID = sub.ID
			prevTitle = sub.Title
			prevPermalink = sub.Permalink
			prevCreated = sub.CreatedUTC
			hasPrev = true
		}
	} else {
		subs, err := a.Reddit.BotSubmissions(1)
		if err != nil {
			return "", err
		}
		if len(subs) > 0 {
			prevID = subs[0].ID
			prevTitle = subs[0].Title
			prevPermalink = subs[0].Permalink
			prevCreated = subs[0].CreatedUTC
			hasPrev = true
		}
	}

	if hasPrev {
		postDate := time.Unix(int64(prevCreated), 0).UTC()
		if postDate.Year() == now.Year() && postDate.Month() == now.Month() {
			activity.GetLogger(ctx).Info("Monthly post already exists for this month (idempotent)", "id", prevID)
			return prevID, nil
		}
	}

	if !hasPrev {
		prevTitle = "Previous monthly thread"
		prevPermalink = fmt.Sprintf("https://www.reddit.com/r/%s/", a.Cfg.SubredditName)
	}

	postTmpl, err := a.Templates.Load("monthly_post")
	if err != nil {
		return "", err
	}
	titleTmpl, err := a.Templates.Load("monthly_post_title")
	if err != nil {
		return "", err
	}

	body, err := templates.FormatString(postTmpl, map[string]any{
		"bot_name":       botName,
		"subreddit_name": a.Cfg.SubredditName,
		"previous_month_submission": map[string]any{
			"title":     prevTitle,
			"permalink": prevPermalink,
		},
		"now": now.Format(time.RFC3339),
	})
	if err != nil {
		return "", err
	}
	title := templates.FormatTitle(titleTmpl, now)

	activity.GetLogger(ctx).Info("Creating monthly post", "subreddit", a.Cfg.SubredditName)
	id, permalink, err := a.Reddit.SubmitSelfPost(title, body, a.Cfg.MonthlyPostFlairID)
	if err != nil {
		return "", err
	}
	_ = a.Reddit.SetSuggestedSort(id, "new")
	activity.GetLogger(ctx).Info("Created monthly post", "url", "https://reddit.com"+permalink)
	return id, nil
}

// SendPushoverNotification sends a Pushover notification (no-op if unconfigured).
func (a *Activities) SendPushoverNotification(ctx context.Context, message string) (bool, error) {
	appToken := os.Getenv("PUSHOVER_APP_TOKEN")
	userToken := os.Getenv("PUSHOVER_USER_TOKEN")
	if appToken == "" || userToken == "" {
		activity.GetLogger(ctx).Debug("Pushover not configured, skipping notification")
		return true, nil
	}
	form := url.Values{
		"token":   {appToken},
		"user":    {userToken},
		"message": {message},
	}
	resp, err := http.PostForm("https://api.pushover.net/1/messages.json", form)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return false, fmt.Errorf("Pushover notification failed with status %d", resp.StatusCode)
	}
	return true, nil
}

func ptrStr(p *string) string {
	if p == nil {
		return "<nil>"
	}
	return *p
}