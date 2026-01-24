"""Reddit API activities for Temporal bot."""

from datetime import datetime, timezone
from typing import Optional
from dataclasses import asdict

import prawcore.exceptions
from temporalio import activity

from ..shared import (
    LOGGER,
    SUBREDDIT_NAME,
    MONTHLY_POST_FLAIR_ID,
    FLAIR_PATTERN,
    FLAIR_TEMPLATE_PATTERN,
    get_reddit_client,
    get_subreddit,
    get_bot_user,
    should_process_redditor,
    is_confirming_trade,
    serialize_comment,
    CommentData,
    ValidationResult,
    FlairUpdateResult,
)


# ============================================================================
# Template Management
# ============================================================================


class TemplateManager:
    """Manages loading and caching of message templates."""

    _cache: dict = {}

    @classmethod
    def load(cls, template_name: str, subreddit) -> str:
        """Load template from wiki or local file system."""
        if template_name in cls._cache:
            return cls._cache[template_name]

        try:
            wiki_page = subreddit.wiki[f"trade-confirmation-bot/{template_name}"]
            content = wiki_page.content_md
            LOGGER.info("Loaded template '%s' from wiki", template_name)
        except (prawcore.exceptions.NotFound, prawcore.exceptions.Forbidden):
            with open(f"src/mdtemplates/{template_name}.md", "r", encoding="utf-8") as f:
                content = f.read()
            LOGGER.info("Loaded template '%s' from file", template_name)

        cls._cache[template_name] = content
        return content


# ============================================================================
# Flair Management
# ============================================================================


class FlairManager:
    """Manages user flair operations."""

    _templates: Optional[dict] = None
    _moderators: Optional[list] = None

    @classmethod
    def _load_flair_templates(cls, subreddit) -> dict:
        """Load flair templates from subreddit."""
        if cls._templates is not None:
            return cls._templates

        templates = {}
        for template in subreddit.flair.templates:
            match = FLAIR_TEMPLATE_PATTERN.search(template["text"])
            if match:
                min_trades = int(match.group(2))
                max_trades = int(match.group(3))
                templates[(min_trades, max_trades)] = {
                    "id": template["id"],
                    "template": template["text"],
                    "mod_only": template["mod_only"],
                }
                LOGGER.info("Loaded flair template: %d-%d trades", min_trades, max_trades)

        cls._templates = templates
        return templates

    @classmethod
    def _load_moderators(cls, subreddit) -> list:
        """Load list of current moderators."""
        if cls._moderators is not None:
            return cls._moderators

        cls._moderators = [str(mod) for mod in subreddit.moderator()]
        return cls._moderators

    @classmethod
    def _get_flair_template(cls, trade_count: int, username: str, subreddit) -> Optional[dict]:
        """Get appropriate flair template for trade count."""
        templates = cls._load_flair_templates(subreddit)
        moderators = cls._load_moderators(subreddit)

        for (min_trades, max_trades), template in templates.items():
            if min_trades <= trade_count <= max_trades:
                if template["mod_only"] == (username in moderators):
                    return template
        return None

    @classmethod
    def _format_flair(cls, flair_template: str, count: int) -> str:
        """Format flair text with trade count."""
        match = FLAIR_TEMPLATE_PATTERN.search(flair_template)
        if not match:
            return flair_template
        start, end = match.span(1)
        return flair_template[:start] + str(count) + flair_template[end:]

    @classmethod
    def get_current_flair_text(cls, username: str, subreddit) -> Optional[str]:
        """Get current flair text for a user."""
        try:
            return next(subreddit.flair(username))["flair_text"]
        except Exception as e:
            LOGGER.error("Failed to get flair for u/%s: %s", username, e)
            return None

    @classmethod
    def set_flair(cls, username: str, count: int, subreddit) -> Optional[str]:
        """Set user's flair to specific trade count."""
        template = cls._get_flair_template(count, username, subreddit)
        if not template:
            LOGGER.warning("No flair template found for %d trades", count)
            return None

        new_flair_text = cls._format_flair(template["template"], count)

        try:
            subreddit.flair.set(username, text=new_flair_text, flair_template_id=template["id"])
            return new_flair_text
        except Exception as e:
            LOGGER.error("Failed to set flair for u/%s: %s", username, e)
            return None

    @classmethod
    def increment_flair(cls, username: str, subreddit) -> tuple[Optional[str], Optional[str]]:
        """Increment user's trade count flair. Returns (old_flair, new_flair)."""
        current_flair = cls.get_current_flair_text(username, subreddit)

        # Empty flair - set to 1
        if not current_flair:
            new_flair = cls.set_flair(username, 1, subreddit)
            return (None, new_flair)

        # Check if flair matches our pattern
        match = FLAIR_PATTERN.search(current_flair)
        if match:
            current_count = int(match.group(1))
            new_count = current_count + 1
            new_flair = cls.set_flair(username, new_count, subreddit)
            return (current_flair, new_flair)

        # Custom flair - don't override
        LOGGER.info("u/%s has custom flair, not updating", username)
        return (current_flair, current_flair)

    @classmethod
    def is_moderator(cls, username: str, subreddit) -> bool:
        """Check if user is a moderator."""
        moderators = cls._load_moderators(subreddit)
        return username in moderators


# ============================================================================
# Comment Fetching Activities
# ============================================================================


@activity.defn
async def fetch_new_comments(
    submission_id: str,
    last_seen_id: Optional[str] = None,
) -> list[dict]:
    """Fetch new comments from a submission.

    Returns list of serialized CommentData dicts.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)
    bot_user = get_bot_user(reddit)
    submission = reddit.submission(id=submission_id)

    submission.comment_sort = "new"
    submission.comments.replace_more(limit=0)

    comments = []
    for comment in submission.comments.list():
        # Skip if we've already seen this comment
        if last_seen_id and comment.id <= last_seen_id:
            continue

        # Skip already processed
        if comment.saved:
            continue

        # Skip removed comments
        if comment.banned_by is not None:
            continue

        # Skip bot's own comments
        if comment.author and comment.author.id == bot_user.id:
            continue

        # Skip comments without valid authors
        if not should_process_redditor(comment.author):
            continue

        comments.append(asdict(serialize_comment(comment)))

    LOGGER.info("Fetched %d new comments from submission %s", len(comments), submission_id)
    return comments


@activity.defn
async def fetch_comment_by_id(comment_id: str) -> Optional[dict]:
    """Fetch a specific comment by ID."""
    reddit = get_reddit_client()
    try:
        comment = reddit.comment(id=comment_id)
        comment.refresh()
        return asdict(serialize_comment(comment))
    except Exception as e:
        LOGGER.error("Failed to fetch comment %s: %s", comment_id, e)
        return None


# ============================================================================
# Validation Activities
# ============================================================================


@activity.defn
async def validate_confirmation(comment_data: dict) -> dict:
    """Validate a confirmation comment.

    Returns ValidationResult as dict.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)
    comment = reddit.comment(id=comment_data["id"])

    # Top-level comments can't be confirmations
    if comment_data["is_root"]:
        # Check if this is in an old thread (non-stickied)
        submission = reddit.submission(id=comment_data["submission_id"])
        if not submission.stickied:
            comment_date = datetime.fromtimestamp(comment_data["created_utc"], tz=timezone.utc)
            now = datetime.now(timezone.utc)
            is_current_month = comment_date.year == now.year and comment_date.month == now.month
            if not is_current_month:
                return asdict(ValidationResult(valid=False, reason="old_confirmation_thread"))

        return asdict(ValidationResult(valid=False))

    # Get parent comment
    parent_comment = comment.parent()

    # Validate parent
    if parent_comment is None or parent_comment.banned_by is not None:
        return asdict(ValidationResult(valid=False))

    if not should_process_redditor(parent_comment.author):
        return asdict(ValidationResult(valid=False))

    # Can't confirm your own trade
    if parent_comment.author.name == comment_data["author_name"]:
        return asdict(ValidationResult(valid=False))

    comment_body = comment_data["body"].lower()

    # Handle moderator approval (for replies to confirmations)
    if not parent_comment.is_root:
        if "approved" in comment_body and FlairManager.is_moderator(
            comment_data["author_name"], subreddit
        ):
            grandparent_comment = parent_comment.parent()
            if grandparent_comment and grandparent_comment.is_root:
                return asdict(
                    ValidationResult(
                        valid=True,
                        is_mod_approval=True,
                        parent_author=grandparent_comment.author.name,
                        confirmer=parent_comment.author.name,
                        parent_comment_id=grandparent_comment.id,
                    )
                )
        return asdict(ValidationResult(valid=False))

    # Check if this is a confirmation
    if not is_confirming_trade(comment_body):
        return asdict(ValidationResult(valid=False))

    # Check if already confirmed
    if parent_comment.saved:
        return asdict(ValidationResult(valid=False, reason="already_confirmed"))

    # Verify user is mentioned in parent comment
    username_lower = comment_data["author_name"].lower()
    parent_body_lower = parent_comment.body.lower()
    parent_html_lower = parent_comment.body_html.lower()

    if username_lower not in parent_body_lower and username_lower not in parent_html_lower:
        return asdict(
            ValidationResult(
                valid=False,
                reason="cant_confirm_username",
                parent_author=parent_comment.author.name,
            )
        )

    # All checks passed
    return asdict(
        ValidationResult(
            valid=True,
            parent_author=parent_comment.author.name,
            confirmer=comment_data["author_name"],
            parent_comment_id=parent_comment.id,
        )
    )


# ============================================================================
# Flair Update Activities
# ============================================================================


@activity.defn
async def update_user_flair(username: str) -> dict:
    """Update a user's flair by incrementing their trade count.

    Returns FlairUpdateResult as dict.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)

    old_flair, new_flair = FlairManager.increment_flair(username, subreddit)

    LOGGER.info("u/%s updated from '%s' to '%s'", username, old_flair, new_flair)

    return asdict(
        FlairUpdateResult(
            username=username,
            old_flair=old_flair,
            new_flair=new_flair,
            success=new_flair is not None,
        )
    )


@activity.defn
async def set_default_flair(username: str) -> bool:
    """Set a user's flair to default (0 trades) if they have no flair."""
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)

    current_flair = FlairManager.get_current_flair_text(username, subreddit)
    if not current_flair or current_flair == "":
        FlairManager.set_flair(username, 0, subreddit)
        return True
    return False


# ============================================================================
# Comment Action Activities
# ============================================================================


@activity.defn
async def mark_comment_saved(comment_id: str) -> bool:
    """Mark a comment as saved (processed)."""
    reddit = get_reddit_client()
    try:
        comment = reddit.comment(id=comment_id)
        comment.save()
        return True
    except Exception as e:
        LOGGER.error("Failed to save comment %s: %s", comment_id, e)
        return False


@activity.defn
async def reply_to_comment(
    comment_id: str,
    template_name: str,
    format_args: Optional[dict] = None,
) -> Optional[str]:
    """Reply to a comment using a template.

    Returns the reply comment ID if successful.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)

    try:
        comment = reddit.comment(id=comment_id)
        template = TemplateManager.load(template_name, subreddit)

        if format_args:
            reply_text = template.format(**format_args)
        else:
            reply_text = template

        reply = comment.reply(reply_text)
        reply.save()
        LOGGER.info("Replied to comment: https://reddit.com%s", reply.permalink)
        return reply.id
    except Exception as e:
        LOGGER.error("Failed to reply to comment %s: %s", comment_id, e)
        return None


@activity.defn
async def post_confirmation_reply(
    comment_id: str,
    parent_author: str,
    confirmer: str,
    parent_new_flair: Optional[str],
    confirmer_new_flair: Optional[str],
) -> Optional[str]:
    """Post a trade confirmation reply.

    Returns the reply comment ID if successful.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)

    try:
        comment = reddit.comment(id=comment_id)
        parent_comment = comment.parent()
        template = TemplateManager.load("trade_confirmation", subreddit)

        reply_text = template.format(
            comment=comment,
            parent_comment=parent_comment,
            new_parent_flair=parent_new_flair or "unknown",
            new_comment_flair=confirmer_new_flair or "unknown",
        )

        reply = comment.reply(reply_text)
        reply.save()
        LOGGER.info("Trade confirmed: https://reddit.com%s", reply.permalink)
        return reply.id
    except Exception as e:
        LOGGER.error("Failed to post confirmation reply: %s", e)
        return None


# ============================================================================
# Monthly Post Activities
# ============================================================================


@activity.defn
async def check_monthly_post_exists() -> bool:
    """Check if a monthly post already exists for the current month."""
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    try:
        last_post = next(bot_user.submissions.new(limit=1))
        post_date = datetime.fromtimestamp(last_post.created_utc, tz=timezone.utc)
        now = datetime.now(timezone.utc)

        is_same_month = post_date.year == now.year and post_date.month == now.month
        if is_same_month:
            LOGGER.info("Monthly post already exists for this month")
            return True
    except StopIteration:
        LOGGER.info("No existing posts found")

    return False


@activity.defn
async def get_current_submission_id() -> Optional[str]:
    """Get the ID of the current (most recent) submission."""
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    try:
        last_post = next(bot_user.submissions.new(limit=1))
        return last_post.id
    except StopIteration:
        return None


@activity.defn
async def unsticky_previous_post() -> bool:
    """Unsticky the previous month's post."""
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    try:
        previous_submission = next(bot_user.submissions.new(limit=1))
        if previous_submission.stickied:
            previous_submission.mod.sticky(state=False)
            LOGGER.info("Unstickied previous post: %s", previous_submission.permalink)
        return True
    except StopIteration:
        LOGGER.info("No previous post to unsticky")
        return True
    except Exception as e:
        LOGGER.error("Failed to unsticky previous post: %s", e)
        return False


@activity.defn
async def create_monthly_post() -> Optional[str]:
    """Create a new monthly confirmation thread.

    Returns the submission ID if successful.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)
    bot_user = get_bot_user(reddit)

    try:
        # Get previous submission for template
        try:
            previous_submission = next(bot_user.submissions.new(limit=1))
        except StopIteration:
            previous_submission = None

        # Load templates
        post_template = TemplateManager.load("monthly_post", subreddit)
        title_template = TemplateManager.load("monthly_post_title", subreddit)

        now = datetime.now(timezone.utc)

        LOGGER.info("Creating monthly post for r/%s", SUBREDDIT_NAME)

        # Create new post
        new_submission = subreddit.submit(
            title=now.strftime(title_template),
            selftext=post_template.format(
                bot_name=bot_user.name,
                subreddit_name=SUBREDDIT_NAME,
                previous_month_submission=previous_submission,
                now=now,
            ),
            flair_id=MONTHLY_POST_FLAIR_ID,
            send_replies=False,
        )

        # Configure new post
        new_submission.mod.sticky(bottom=False)
        new_submission.mod.suggested_sort(sort="new")

        LOGGER.info("Created monthly post: https://reddit.com%s", new_submission.permalink)
        return new_submission.id
    except Exception as e:
        LOGGER.error("Failed to create monthly post: %s", e)
        return None


@activity.defn
async def lock_previous_submissions() -> int:
    """Lock submissions from previous months.

    Returns the number of submissions locked.
    """
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    locked_count = 0
    for submission in bot_user.submissions.new(limit=10):
        if submission.stickied:
            continue
        if not submission.locked:
            try:
                submission.mod.lock()
                LOGGER.info("Locked: https://reddit.com%s", submission.permalink)
                locked_count += 1
            except Exception as e:
                LOGGER.error("Failed to lock submission: %s", e)

    return locked_count
