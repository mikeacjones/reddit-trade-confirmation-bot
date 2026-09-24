package reddit

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/config"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/rules"
)

const (
	authURL    = "https://www.reddit.com/api/v1/access_token"
	oauthBase  = "https://oauth.reddit.com"
	pageLimit  = 100
)

// Client is a minimal Reddit OAuth API client.
type Client struct {
	cfg        config.Config
	http       *http.Client
	mu         sync.Mutex
	token      string
	tokenExp   time.Time
	botName    string
	botID      string
	moderators []string
	flairTmpls []models.FlairTemplate
}

// New creates a Reddit client from config.
func New(cfg config.Config) *Client {
	return &Client{
		cfg:  cfg,
		http: &http.Client{Timeout: 60 * time.Second},
	}
}

func (c *Client) ensureToken() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.token != "" && time.Now().Before(c.tokenExp.Add(-30*time.Second)) {
		return nil
	}
	form := url.Values{
		"grant_type": {"password"},
		"username":   {c.cfg.RedditUsername},
		"password":   {c.cfg.RedditPassword},
	}
	req, err := http.NewRequest(http.MethodPost, authURL, strings.NewReader(form.Encode()))
	if err != nil {
		return err
	}
	req.SetBasicAuth(c.cfg.RedditClientID, c.cfg.RedditClientSecret)
	req.Header.Set("User-Agent", c.cfg.RedditUserAgent)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return fmt.Errorf("reddit auth failed: %s: %s", resp.Status, body)
	}
	var tok struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.Unmarshal(body, &tok); err != nil {
		return err
	}
	c.token = tok.AccessToken
	c.tokenExp = time.Now().Add(time.Duration(tok.ExpiresIn) * time.Second)
	return nil
}

func (c *Client) do(method, path string, form url.Values, out any) error {
	if err := c.ensureToken(); err != nil {
		return err
	}
	var body io.Reader
	u := oauthBase + path
	if form != nil {
		if method == http.MethodGet {
			u += "?" + form.Encode()
		} else {
			body = strings.NewReader(form.Encode())
		}
	}
	req, err := http.NewRequest(method, u, body)
	if err != nil {
		return err
	}
	c.mu.Lock()
	token := c.token
	c.mu.Unlock()
	req.Header.Set("Authorization", "bearer "+token)
	req.Header.Set("User-Agent", c.cfg.RedditUserAgent)
	if form != nil && method != http.MethodGet {
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode == 401 {
		// force refresh once
		c.mu.Lock()
		c.token = ""
		c.mu.Unlock()
		return fmt.Errorf("reddit unauthorized: %s", raw)
	}
	if resp.StatusCode == 403 {
		return nonRetryable("Forbidden: %s", raw)
	}
	if resp.StatusCode == 404 {
		return nonRetryable("NotFound: %s", raw)
	}
	if resp.StatusCode == 400 {
		return nonRetryable("BadRequest: %s", raw)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("reddit %s %s: %s: %s", method, path, resp.Status, raw)
	}
	if out == nil || len(raw) == 0 || string(raw) == "{}" {
		return nil
	}
	return json.Unmarshal(raw, out)
}

type nonRetryableError struct{ msg string }

func (e *nonRetryableError) Error() string { return e.msg }
func (e *nonRetryableError) NonRetryable() {}

func nonRetryable(format string, args ...any) error {
	return &nonRetryableError{msg: fmt.Sprintf(format, args...)}
}

// Thing is a Reddit API listing child.
type Thing struct {
	Kind string          `json:"kind"`
	Data json.RawMessage `json:"data"`
}

type listing struct {
	Data struct {
		Children []Thing `json:"children"`
		After    string  `json:"after"`
	} `json:"data"`
}

// Comment is a Reddit comment.
type Comment struct {
	ID         string  `json:"id"`
	Name       string  `json:"name"`
	Body       string  `json:"body"`
	BodyHTML   string  `json:"body_html"`
	Author     string  `json:"author"`
	CreatedUTC float64 `json:"created_utc"`
	LinkID     string  `json:"link_id"`
	ParentID   string  `json:"parent_id"`
	Saved      bool    `json:"saved"`
	BannedBy   any     `json:"banned_by"`
	AuthorFull *struct {
		ID          string `json:"id"`
		IsSuspended bool   `json:"is_suspended"`
	} `json:"-"`
}

// IsRoot returns true if the comment is top-level on a submission.
func (c Comment) IsRoot() bool {
	return strings.HasPrefix(c.ParentID, "t3_")
}

// SubmissionID extracts the raw submission id from link_id.
func (c Comment) SubmissionID() string {
	return strings.TrimPrefix(c.LinkID, "t3_")
}

// IsBanned returns whether the comment was removed by a mod.
func (c Comment) IsBanned() bool {
	return c.BannedBy != nil
}

// Submission is a Reddit submission.
type Submission struct {
	ID         string  `json:"id"`
	Title      string  `json:"title"`
	Permalink  string  `json:"permalink"`
	CreatedUTC float64 `json:"created_utc"`
	Stickied   bool    `json:"stickied"`
	Locked     bool    `json:"locked"`
	Author     string  `json:"author"`
}

// BotUser returns the authenticated username and id.
func (c *Client) BotUser() (name, id string, err error) {
	c.mu.Lock()
	if c.botName != "" {
		name, id = c.botName, c.botID
		c.mu.Unlock()
		return name, id, nil
	}
	c.mu.Unlock()

	var me struct {
		Name string `json:"name"`
		ID   string `json:"id"`
	}
	if err := c.do(http.MethodGet, "/api/v1/me", nil, &me); err != nil {
		return "", "", err
	}
	c.mu.Lock()
	c.botName, c.botID = me.Name, me.ID
	c.mu.Unlock()
	return me.Name, me.ID, nil
}

// ShouldProcessRedditor mirrors the Python should_process_redditor check.
func ShouldProcessRedditor(author string, authorDeleted bool, isSuspended bool, botName string) bool {
	if authorDeleted || author == "" || author == "[deleted]" {
		return false
	}
	if strings.EqualFold(author, botName) {
		return false
	}
	if isSuspended {
		return false
	}
	return true
}

// SerializeComment converts a Reddit comment to CommentData.
func SerializeComment(c Comment) models.CommentData {
	return models.CommentData{
		ID:           c.ID,
		Body:         strings.ReplaceAll(c.Body, "\\", ""),
		AuthorName:   c.Author,
		CreatedUTC:   c.CreatedUTC,
		IsRoot:       c.IsRoot(),
		SubmissionID: c.SubmissionID(),
	}
}

// IterComments calls fn for each new comment in the subreddit listing (newest first).
// Stops when fn returns false. Pages of 100 until limit exhausted or stop.
func (c *Client) IterComments(fn func(Comment, int) bool) error {
	after := ""
	scanned := 0
	for {
		form := url.Values{"limit": {fmt.Sprintf("%d", pageLimit)}}
		if after != "" {
			form.Set("after", after)
		}
		var list listing
		path := fmt.Sprintf("/r/%s/comments", c.cfg.SubredditName)
		if err := c.do(http.MethodGet, path, form, &list); err != nil {
			return err
		}
		if len(list.Data.Children) == 0 {
			return nil
		}
		for _, child := range list.Data.Children {
			if child.Kind != "t1" {
				continue
			}
			var cm Comment
			if err := json.Unmarshal(child.Data, &cm); err != nil {
				return err
			}
			scanned++
			if !fn(cm, scanned) {
				return nil
			}
		}
		if list.Data.After == "" || len(list.Data.Children) < pageLimit {
			return nil
		}
		after = list.Data.After
	}
}

// GetComment fetches a comment by id.
func (c *Client) GetComment(id string) (*Comment, error) {
	form := url.Values{"id": {"t1_" + id}}
	var list listing
	if err := c.do(http.MethodGet, "/api/info", form, &list); err != nil {
		return nil, err
	}
	if len(list.Data.Children) == 0 {
		return nil, nil
	}
	var cm Comment
	if err := json.Unmarshal(list.Data.Children[0].Data, &cm); err != nil {
		return nil, err
	}
	return &cm, nil
}

// SaveComment marks a comment as saved.
func (c *Client) SaveComment(id string) error {
	return c.do(http.MethodPost, "/api/save", url.Values{"id": {"t1_" + id}}, nil)
}

// ReplyToComment posts a reply and returns the new comment id.
func (c *Client) ReplyToComment(parentID, text string) (string, string, error) {
	form := url.Values{
		"api_type": {"json"},
		"thing_id": {"t1_" + parentID},
		"text":     {text},
	}
	var resp struct {
		JSON struct {
			Errors []any `json:"errors"`
			Data   struct {
				Things []Thing `json:"things"`
			} `json:"data"`
		} `json:"json"`
	}
	if err := c.do(http.MethodPost, "/api/comment", form, &resp); err != nil {
		return "", "", err
	}
	if len(resp.JSON.Errors) > 0 {
		return "", "", fmt.Errorf("comment reply errors: %v", resp.JSON.Errors)
	}
	if len(resp.JSON.Data.Things) == 0 {
		return "", "", fmt.Errorf("confirmation reply failed to post")
	}
	var cm Comment
	if err := json.Unmarshal(resp.JSON.Data.Things[0].Data, &cm); err != nil {
		return "", "", err
	}
	return cm.ID, cmPermalink(cm), nil
}

func cmPermalink(cm Comment) string {
	// Permalink isn't always in comment response; construct best-effort.
	if cm.LinkID != "" {
		return fmt.Sprintf("/r/comments/%s/_/%s", strings.TrimPrefix(cm.LinkID, "t3_"), cm.ID)
	}
	return "/comments/" + cm.ID
}

// GetSubmission fetches a submission by id.
func (c *Client) GetSubmission(id string) (*Submission, error) {
	form := url.Values{"id": {"t3_" + id}}
	var list listing
	if err := c.do(http.MethodGet, "/api/info", form, &list); err != nil {
		return nil, err
	}
	if len(list.Data.Children) == 0 {
		return nil, nil
	}
	var s Submission
	if err := json.Unmarshal(list.Data.Children[0].Data, &s); err != nil {
		return nil, err
	}
	return &s, nil
}

// BotSubmissions returns the bot's newest submissions (limit).
func (c *Client) BotSubmissions(limit int) ([]Submission, error) {
	name, _, err := c.BotUser()
	if err != nil {
		return nil, err
	}
	form := url.Values{"limit": {fmt.Sprintf("%d", limit)}}
	var list listing
	path := fmt.Sprintf("/user/%s/submitted", name)
	if err := c.do(http.MethodGet, path, form, &list); err != nil {
		return nil, err
	}
	out := make([]Submission, 0, len(list.Data.Children))
	for _, child := range list.Data.Children {
		var s Submission
		if err := json.Unmarshal(child.Data, &s); err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, nil
}

// StickySubmission stickies or unstickies a submission.
func (c *Client) StickySubmission(id string, state bool) error {
	form := url.Values{
		"id":    {"t3_" + id},
		"state": {fmt.Sprintf("%t", state)},
	}
	if state {
		form.Set("num", "1") // top sticky
	}
	return c.do(http.MethodPost, "/api/set_subreddit_sticky", form, nil)
}

// LockSubmission locks a submission.
func (c *Client) LockSubmission(id string) error {
	return c.do(http.MethodPost, "/api/lock", url.Values{"id": {"t3_" + id}}, nil)
}

// SetSuggestedSort sets the suggested comment sort.
func (c *Client) SetSuggestedSort(id, sort string) error {
	return c.do(http.MethodPost, "/api/set_suggested_sort", url.Values{
		"id":   {"t3_" + id},
		"sort": {sort},
	}, nil)
}

// SubmitSelfPost creates a text post and returns the new submission id and permalink.
func (c *Client) SubmitSelfPost(title, text, flairID string) (string, string, error) {
	form := url.Values{
		"api_type":     {"json"},
		"kind":         {"self"},
		"sr":           {c.cfg.SubredditName},
		"title":        {title},
		"text":         {text},
		"sendreplies":  {"false"},
	}
	if flairID != "" {
		form.Set("flair_id", flairID)
	}
	var resp struct {
		JSON struct {
			Errors []any `json:"errors"`
			Data   struct {
				ID        string `json:"id"`
				Name      string `json:"name"`
				URL       string `json:"url"`
				Permalink string `json:"permalink"`
			} `json:"data"`
		} `json:"json"`
	}
	if err := c.do(http.MethodPost, "/api/submit", form, &resp); err != nil {
		return "", "", err
	}
	if len(resp.JSON.Errors) > 0 {
		return "", "", fmt.Errorf("submit errors: %v", resp.JSON.Errors)
	}
	id := resp.JSON.Data.ID
	if id == "" {
		id = strings.TrimPrefix(resp.JSON.Data.Name, "t3_")
	}
	return id, resp.JSON.Data.Permalink, nil
}

// LoadWikiPage loads a wiki page markdown body.
func (c *Client) LoadWikiPage(page string) (string, error) {
	path := fmt.Sprintf("/r/%s/wiki/%s", c.cfg.SubredditName, page)
	var resp struct {
		Data struct {
			ContentMD string `json:"content_md"`
		} `json:"data"`
	}
	if err := c.do(http.MethodGet, path, nil, &resp); err != nil {
		return "", err
	}
	return resp.Data.ContentMD, nil
}

// Moderators returns cached subreddit moderator usernames.
func (c *Client) Moderators() ([]string, error) {
	c.mu.Lock()
	if c.moderators != nil {
		mods := c.moderators
		c.mu.Unlock()
		return mods, nil
	}
	c.mu.Unlock()

	path := fmt.Sprintf("/r/%s/about/moderators", c.cfg.SubredditName)
	var resp struct {
		Data struct {
			Children []struct {
				Name string `json:"name"`
			} `json:"children"`
		} `json:"data"`
	}
	if err := c.do(http.MethodGet, path, nil, &resp); err != nil {
		return nil, err
	}
	mods := make([]string, 0, len(resp.Data.Children))
	for _, m := range resp.Data.Children {
		mods = append(mods, m.Name)
	}
	c.mu.Lock()
	c.moderators = mods
	c.mu.Unlock()
	return mods, nil
}

// IsModerator checks if username is a moderator.
func (c *Client) IsModerator(username string) (bool, error) {
	mods, err := c.Moderators()
	if err != nil {
		return false, err
	}
	for _, m := range mods {
		if m == username {
			return true, nil
		}
	}
	return false, nil
}

// FlairTemplates loads and caches user flair templates with trade ranges.
func (c *Client) FlairTemplates() ([]models.FlairTemplate, error) {
	c.mu.Lock()
	if c.flairTmpls != nil {
		t := c.flairTmpls
		c.mu.Unlock()
		return t, nil
	}
	c.mu.Unlock()

	path := fmt.Sprintf("/r/%s/api/user_flair_v2", c.cfg.SubredditName)
	var raw []struct {
		ID      string `json:"id"`
		Text    string `json:"text"`
		ModOnly bool   `json:"mod_only"`
	}
	if err := c.do(http.MethodGet, path, nil, &raw); err != nil {
		return nil, err
	}
	var templates []models.FlairTemplate
	for _, t := range raw {
		min, max, ok := rules.ParseFlairRange(t.Text)
		if !ok {
			continue
		}
		templates = append(templates, models.FlairTemplate{
			ID:       t.ID,
			Template: t.Text,
			ModOnly:  t.ModOnly,
			Min:      min,
			Max:      max,
		})
	}
	c.mu.Lock()
	c.flairTmpls = templates
	c.mu.Unlock()
	return templates, nil
}

// GetUserFlair returns flair text for a user.
func (c *Client) GetUserFlair(username string) (*string, error) {
	path := fmt.Sprintf("/r/%s/api/flairlist", c.cfg.SubredditName)
	form := url.Values{"name": {username}, "limit": {"1"}}
	var resp struct {
		Users []struct {
			User      string  `json:"user"`
			FlairText *string `json:"flair_text"`
		} `json:"users"`
	}
	if err := c.do(http.MethodGet, path, form, &resp); err != nil {
		return nil, err
	}
	if len(resp.Users) == 0 {
		return nil, nil
	}
	return resp.Users[0].FlairText, nil
}

// SetUserFlair sets a user's flair text and template.
func (c *Client) SetUserFlair(username, text, templateID string) error {
	form := url.Values{
		"api_type": {"json"},
		"name":     {username},
		"text":     {text},
	}
	if templateID != "" {
		form.Set("flair_template_id", templateID)
	}
	path := fmt.Sprintf("/r/%s/api/flair", c.cfg.SubredditName)
	return c.do(http.MethodPost, path, form, nil)
}
