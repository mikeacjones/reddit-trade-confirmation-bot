package config

import (
	"os"
	"regexp"
	"strings"
)

var nonSlug = regexp.MustCompile(`[^a-z0-9.-]+`)
var multiDash = regexp.MustCompile(`-+`)

// Config holds process-wide settings loaded from the environment.
type Config struct {
	SubredditName      string
	SubredditSlug      string
	BuildID            string
	DeploymentName     string
	TemporalHost       string
	TemporalNamespace  string
	MonthlyPostFlairID string
	TaskQueue          string

	RedditClientID     string
	RedditClientSecret string
	RedditUsername     string
	RedditPassword     string
	RedditUserAgent    string
}

// MustLoad reads required environment variables or panics.
func MustLoad() Config {
	cfg := LoadWithoutReddit()
	cfg.RedditClientID = mustEnv("REDDIT_CLIENT_ID")
	cfg.RedditClientSecret = mustEnv("REDDIT_CLIENT_SECRET")
	cfg.RedditUsername = mustEnv("REDDIT_USERNAME")
	cfg.RedditPassword = mustEnv("REDDIT_PASSWORD")
	cfg.RedditUserAgent = mustEnv("REDDIT_USER_AGENT")
	cfg.MonthlyPostFlairID = os.Getenv("MONTHLY_POST_FLAIR_ID")
	return cfg
}

// LoadWithoutReddit loads Temporal/subreddit config without Reddit credentials.
func LoadWithoutReddit() Config {
	subreddit := mustEnv("SUBREDDIT_NAME")
	slug := SlugifySubredditName(subreddit)
	buildID := firstNonEmpty(os.Getenv("TEMPORAL_WORKER_BUILD_ID"), os.Getenv("BUILD_ID"), "dev")
	deployment := firstNonEmpty(
		os.Getenv("TEMPORAL_DEPLOYMENT_NAME"),
		"reddit-bots-trade-confirmation-"+slug,
	)
	host := firstNonEmpty(os.Getenv("TEMPORAL_ADDRESS"), os.Getenv("TEMPORAL_HOST"), "localhost:7233")

	return Config{
		SubredditName:     subreddit,
		SubredditSlug:     slug,
		BuildID:           buildID,
		DeploymentName:    deployment,
		TemporalHost:      host,
		TemporalNamespace: firstNonEmpty(os.Getenv("TEMPORAL_NAMESPACE"), "reddit-bots"),
		TaskQueue:         "trade-confirmation-bot-" + subreddit,
	}
}

// SlugifySubredditName converts a subreddit name into a Docker/Temporal-safe slug.
func SlugifySubredditName(name string) string {
	slug := strings.ToLower(name)
	slug = nonSlug.ReplaceAllString(slug, "-")
	slug = multiDash.ReplaceAllString(slug, "-")
	slug = strings.Trim(slug, "-.")
	if slug == "" {
		return "unknown"
	}
	return slug
}

func mustEnv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		panic("required environment variable not set: " + key)
	}
	return v
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}
