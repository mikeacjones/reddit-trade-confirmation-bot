package models

import "go.temporal.io/sdk/temporal"

// Custom Temporal search attribute keys used for visibility filtering.
var (
	RedditSubreddit          = temporal.NewSearchAttributeKeyKeyword("RedditSubreddit")
	RedditCommentID          = temporal.NewSearchAttributeKeyKeyword("RedditCommentId")
	RedditSubmissionID       = temporal.NewSearchAttributeKeyKeyword("RedditSubmissionId")
	RedditConfirmationStatus = temporal.NewSearchAttributeKeyKeyword("RedditConfirmationStatus")
)
