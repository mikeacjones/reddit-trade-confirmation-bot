package models

// CommentData is serializable comment data passed between layers.
type CommentData struct {
	ID           string  `json:"id"`
	Body         string  `json:"body"`
	AuthorName   string  `json:"author_name"`
	CreatedUTC   float64 `json:"created_utc"`
	IsRoot       bool    `json:"is_root"`
	SubmissionID string  `json:"submission_id"`
}

// ValidationResult is the result of validating a confirmation comment.
type ValidationResult struct {
	Valid               bool   `json:"valid"`
	Reason              string `json:"reason,omitempty"`
	ParentAuthor        string `json:"parent_author,omitempty"`
	Confirmer           string `json:"confirmer,omitempty"`
	ParentCommentID     string `json:"parent_comment_id,omitempty"`
	IsModApproval       bool   `json:"is_mod_approval"`
	ReplyToCommentID    string `json:"reply_to_comment_id,omitempty"`
	ParentBodyLower     string `json:"parent_body_lower,omitempty"`
	ParentBodyHTMLLower string `json:"parent_body_html_lower,omitempty"`
}

// ConfirmationContext is pre-fetched parent/grandparent data for validation.
type ConfirmationContext struct {
	ParentExists          bool   `json:"parent_exists"`
	ParentIsBanned        bool   `json:"parent_is_banned"`
	ParentIsProcessable   bool   `json:"parent_is_processable"`
	ParentAuthorName      string `json:"parent_author_name"`
	ParentID              string `json:"parent_id"`
	ParentIsRoot          bool   `json:"parent_is_root"`
	ParentIsSaved         bool   `json:"parent_is_saved"`
	ParentBodyLower       string `json:"parent_body_lower"`
	ParentBodyHTMLLower   string `json:"parent_body_html_lower"`
	IsModerator           bool   `json:"is_moderator"`
	GrandparentExists     bool   `json:"grandparent_exists"`
	GrandparentIsRoot     bool   `json:"grandparent_is_root"`
	GrandparentAuthorName string `json:"grandparent_author_name"`
	GrandparentID         string `json:"grandparent_id"`
}

// FlairUpdateResult is the result of updating a user's flair.
type FlairUpdateResult struct {
	NewFlair *string `json:"new_flair"`
}

// FlairIncrementRequest asks the coordinator to apply a flair increment.
type FlairIncrementRequest struct {
	Username  string `json:"username"`
	RequestID string `json:"request_id"`
	Delta     int    `json:"delta"`
}

// FlairIncrementResult is the result of a coordinated flair increment.
type FlairIncrementResult struct {
	OldFlair *string `json:"old_flair"`
	NewFlair *string `json:"new_flair"`
}

// ActiveSubmissions holds current and previous tracked submission IDs.
type ActiveSubmissions struct {
	CurrentSubmissionID  *string `json:"current_submission_id"`
	PreviousSubmissionID *string `json:"previous_submission_id"`
}

// CreateMonthlyPostInput is input for creating a monthly confirmation post.
type CreateMonthlyPostInput struct {
	PreviousSubmissionID *string `json:"previous_submission_id"`
}

// FetchCommentsInput is input for the long-running comment polling activity.
type FetchCommentsInput struct {
	SeenIDs              []string `json:"seen_ids"`
	ActiveSubmissionIDs  []string `json:"active_submission_ids"`
	CurrentSubmissionID  string   `json:"current_submission_id"`
}

// FetchCommentsResult is the result of fetching new comments.
type FetchCommentsResult struct {
	Comments     []CommentData `json:"comments"`
	ScannedIDs   []string      `json:"scanned_ids"`
	FoundSeen    bool          `json:"found_seen"`
	ScannedCount int           `json:"scanned_count"`
	PossibleGap  bool          `json:"possible_gap"`
}

// ReplyToCommentInput is input for replying to a comment with a template.
type ReplyToCommentInput struct {
	CommentID    string         `json:"comment_id"`
	TemplateName string         `json:"template_name"`
	FormatArgs   map[string]any `json:"format_args,omitempty"`
}

// SetUserFlairInput is input for setting a user's flair.
type SetUserFlairInput struct {
	Username string  `json:"username"`
	NewCount int     `json:"new_count"`
	OldFlair *string `json:"old_flair"`
}

// UserFlairResult is the result of getting a user's current flair.
type UserFlairResult struct {
	FlairText      *string `json:"flair_text"`
	TradeCount     *int    `json:"trade_count"`
	IsTradeTracked bool    `json:"is_trade_tracked"`
}

// FlairTemplate describes a subreddit flair template matching a trade range.
type FlairTemplate struct {
	ID       string
	Template string
	ModOnly  bool
	Min      int
	Max      int
}
