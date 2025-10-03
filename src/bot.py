"""Trade confirmation bot v2.0 using praw-bot-wrapper"""
import os
import sys
import logging
import re
import json
import praw_bot_wrapper
from praw import Reddit
from datetime import datetime, timezone
from typing import Optional, Dict
import http.client
import urllib

import boto3
import praw
import prawcore.exceptions

# ============================================================================
# Configuration
# ============================================================================

SUBREDDIT_NAME = os.environ["SUBREDDIT_NAME"]
MONTHLY_POST_FLAIR_ID = os.getenv("MONTHLY_POST_FLAIR_ID", None)
FLAIR_PATTERN = re.compile(r"Trades: (\d+)")
FLAIR_TEMPLATE_PATTERN = re.compile(r"Trades: ((\d+)-(\d+))")


# ============================================================================
# Logging Setup
# ============================================================================

def setup_logger(name: str) -> logging.Logger:
    """Set up logger with file and console handlers."""
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # File handler
    file_handler = logging.FileHandler("log.txt", mode="w")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger


LOGGER = setup_logger("trade-confirmation-bot")


# ============================================================================
# Secrets Management
# ============================================================================

def load_secrets(subreddit: str) -> Dict:
    """Load secrets from AWS Secrets Manager or environment."""
    if os.getenv("DEV"):
        # Development mode: load from environment
        return {
            "REDDIT_CLIENT_ID": os.getenv("REDDIT_CLIENT_ID"),
            "REDDIT_CLIENT_SECRET": os.getenv("REDDIT_CLIENT_SECRET"),
            "REDDIT_USER_AGENT": os.getenv("REDDIT_USER_AGENT"),
            "REDDIT_USERNAME": os.getenv("REDDIT_USERNAME"),
            "REDDIT_PASSWORD": os.getenv("REDDIT_PASSWORD"),
            "PUSHOVER_APP_TOKEN": os.getenv("PUSHOVER_APP_TOKEN", ""),
            "PUSHOVER_USER_TOKEN": os.getenv("PUSHOVER_USER_TOKEN", ""),
        }
    
    # Production mode: load from AWS Secrets Manager
    secrets_manager = boto3.client("secretsmanager")
    response = secrets_manager.get_secret_value(
        SecretId=f"trade-confirmation-bot/{subreddit}"
    )
    return json.loads(response["SecretString"])


SECRETS = load_secrets(SUBREDDIT_NAME)


# ============================================================================
# Bot Initialization
# ============================================================================

BOT = BOT = Reddit(
    client_id=SECRETS["REDDIT_CLIENT_ID"],
    client_secret=SECRETS["REDDIT_CLIENT_SECRET"],
    user_agent=SECRETS["REDDIT_USER_AGENT"],
    username=SECRETS["REDDIT_USERNAME"],
    password=SECRETS["REDDIT_PASSWORD"],
)

SUBREDDIT = BOT.subreddit(SUBREDDIT_NAME)


# ============================================================================
# Template Management
# ============================================================================

class TemplateManager:
    """Manages loading and caching of message templates."""
    
    def __init__(self, subreddit: praw.models.Subreddit):
        self.subreddit = subreddit
        self._cache = {}
    
    def load(self, template_name: str) -> str:
        """Load template from wiki or local file system."""
        if template_name in self._cache:
            return self._cache[template_name]
        
        try:
            wiki_page = self.subreddit.wiki[f"trade-confirmation-bot/{template_name}"]
            content = wiki_page.content_md
            LOGGER.info("Loaded template '%s' from wiki", template_name)
        except (prawcore.exceptions.NotFound, prawcore.exceptions.Forbidden):
            with open(f"src/mdtemplates/{template_name}.md", "r", encoding="utf-8") as f:
                content = f.read()
            LOGGER.info("Loaded template '%s' from file", template_name)
        
        self._cache[template_name] = content
        return content


TEMPLATES = TemplateManager(SUBREDDIT)


# ============================================================================
# Notification System
# ============================================================================

def send_pushover_notification(message: str) -> None:
    """Send notification via Pushover."""
    if not SECRETS.get("PUSHOVER_APP_TOKEN") or not SECRETS.get("PUSHOVER_USER_TOKEN"):
        LOGGER.debug("Pushover not configured, skipping notification")
        return
    
    try:
        conn = http.client.HTTPSConnection("api.pushover.net:443")
        conn.request(
            "POST",
            "/1/messages.json",
            urllib.parse.urlencode({
                "token": SECRETS["PUSHOVER_APP_TOKEN"],
                "user": SECRETS["PUSHOVER_USER_TOKEN"],
                "message": message,
            }),
            {"Content-type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        if response.status != 200:
            LOGGER.warning("Pushover notification failed: %s", response.status)
    except Exception as e:
        LOGGER.error("Failed to send Pushover notification: %s", e)


# ============================================================================
# Flair Management
# ============================================================================

class FlairManager:
    """Manages user flair operations."""
    
    def __init__(self, subreddit: praw.models.Subreddit):
        self.subreddit = subreddit
        self.templates = self._load_flair_templates()
        self.current_mods = self._load_moderators()
    
    def _load_flair_templates(self) -> Dict:
        """Load flair templates from subreddit."""
        templates = {}
        for template in self.subreddit.flair.templates:
            match = FLAIR_TEMPLATE_PATTERN.search(template["text"])
            if match:
                min_trades = int(match.group(2))
                max_trades = int(match.group(3))
                templates[(min_trades, max_trades)] = {
                    "id": template["id"],
                    "template": template["text"],
                    "mod_only": template["mod_only"],
                }
                LOGGER.info(
                    "Loaded flair template: %d-%d trades",
                    min_trades,
                    max_trades
                )
        return templates
    
    def _load_moderators(self) -> list:
        """Load list of current moderators."""
        return [str(mod) for mod in self.subreddit.moderator()]
    
    def _get_flair_template(self, trade_count: int, username: str) -> Optional[Dict]:
        """Get appropriate flair template for trade count."""
        for (min_trades, max_trades), template in self.templates.items():
            if min_trades <= trade_count <= max_trades:
                # Enforce mod_only flag
                if template["mod_only"] == (username in self.current_mods):
                    return template
        return None
    
    def _format_flair(self, flair_template: str, count: int) -> str:
        """Format flair text with trade count."""
        match = FLAIR_TEMPLATE_PATTERN.search(flair_template)
        if not match:
            return flair_template
        start, end = match.span(1)
        return flair_template[:start] + str(count) + flair_template[end:]
    
    def get_current_flair_text(self, username: str) -> Optional[str]:
        """Get current flair text for a user."""
        try:
            return next(self.subreddit.flair(username))["flair_text"]
        except Exception as e:
            LOGGER.error("Failed to get flair for u/%s: %s", username, e)
            return None
    
    def increment_flair(self, username: str) -> Optional[str]:
        """Increment user's trade count flair."""
        current_flair = self.get_current_flair_text(username)
        
        # Empty flair - set to 1
        if not current_flair:
            return self.set_flair(username, 1)
        
        # Check if flair matches our pattern
        match = FLAIR_PATTERN.search(current_flair)
        if match:
            current_count = int(match.group(1))
            new_count = current_count + 1
            return self.set_flair(username, new_count)
        
        # Custom flair - don't override
        LOGGER.info("u/%s has custom flair, not updating", username)
        return current_flair
    
    def set_flair(self, username: str, count: int) -> Optional[str]:
        """Set user's flair to specific trade count."""
        template = self._get_flair_template(count, username)
        if not template:
            LOGGER.warning("No flair template found for %d trades", count)
            return None
        
        new_flair_text = self._format_flair(template["template"], count)
        
        try:
            self.subreddit.flair.set(
                username,
                text=new_flair_text,
                flair_template_id=template["id"]
            )
            return new_flair_text
        except Exception as e:
            LOGGER.error("Failed to set flair for u/%s: %s", username, e)
            return None


FLAIR_MANAGER = FlairManager(SUBREDDIT)


# ============================================================================
# Validation Functions
# ============================================================================

def should_process_redditor(redditor) -> bool:
    """Check if redditor should be processed."""
    try:
        if redditor is None:
            return False
        if not hasattr(redditor, "id"):
            return False
        if redditor.id == BOT.user.me().id:
            return False
        if hasattr(redditor, "is_suspended") and redditor.is_suspended:
            return False
        return True
    except prawcore.exceptions.NotFound:
        return False


def is_confirming_trade(comment_body: str) -> bool:
    """Check if comment is confirming a trade."""
    return "confirmed" in comment_body.lower()


# ============================================================================
# Trade Confirmation Logic
# ============================================================================

def increment_trades(parent_comment, comment) -> None:
    """Increment trade counts for both users."""
    if parent_comment.saved:
        LOGGER.info("Parent comment already processed, skipping")
        return
    
    # Get current flair texts
    parent_old_flair = FLAIR_MANAGER.get_current_flair_text(parent_comment.author.name)
    comment_old_flair = FLAIR_MANAGER.get_current_flair_text(comment.author.name)
    
    # Increment flairs
    parent_new_flair = FLAIR_MANAGER.increment_flair(parent_comment.author.name)
    comment_new_flair = FLAIR_MANAGER.increment_flair(comment.author.name)
    
    # Mark as processed
    parent_comment.save()
    comment.save()
    
    # Reply with confirmation
    try:
        template = TEMPLATES.load("trade_confirmation")
        reply_text = template.format(
            comment=comment,
            parent_comment=parent_comment,
            new_parent_flair=parent_new_flair or parent_old_flair,
            new_comment_flair=comment_new_flair or comment_old_flair,
        )
        reply_comment = comment.reply(reply_text)
        reply_comment.save()
        
        LOGGER.info(
            "u/%s updated from '%s' to '%s'",
            comment.author.name,
            comment_old_flair,
            comment_new_flair,
        )
        LOGGER.info(
            "u/%s updated from '%s' to '%s'",
            parent_comment.author.name,
            parent_old_flair,
            parent_new_flair,
        )
        LOGGER.info("Trade confirmed: https://reddit.com%s", reply_comment.permalink)
    except Exception as e:
        LOGGER.error("Failed to reply to confirmation: %s", e)


# ============================================================================
# Comment Handlers
# ============================================================================

def handle_automoderator_comment(comment) -> None:
    """Handle AutoModerator removal notifications."""
    if "removed" in comment.body.lower():
        try:
            comment.submission.mod.remove()
            LOGGER.info(
                "AutoModerator removed submission: https://reddit.com%s",
                comment.submission.permalink,
            )
            comment.save()
        except Exception as e:
            LOGGER.error("Failed to remove submission: %s", e)


def handle_non_confirmation_thread(comment) -> None:
    """Handle comments outside confirmation threads."""
    # Set default flair for users without one
    if not comment.author_flair_text or comment.author_flair_text == "":
        FLAIR_MANAGER.set_flair(comment.author.name, 0)


def handle_root_confirmation_comment(comment) -> None:
    """Handle top-level comments in old confirmation threads."""
    # Only lock if in non-stickied thread
    if comment.submission.stickied:
        return
    
    # Check if comment is in current month
    comment_date = datetime.fromtimestamp(comment.created_utc, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    is_current_month = (
        comment_date.year == now.year and
        comment_date.month == now.month
    )
    
    if not is_current_month:
        try:
            comment.mod.lock()
            template = TEMPLATES.load("old_confirmation_thread")
            reply_text = template.format(comment=comment)
            comment.reply(reply_text)
            comment.save()
            LOGGER.info("Locked old comment: https://reddit.com%s", comment.permalink)
        except Exception as e:
            LOGGER.error("Failed to lock old comment: %s", e)


def handle_confirmation_thread(comment) -> None:
    """Handle comments in confirmation threads."""
    # Top-level comment
    if comment.is_root:
        handle_root_confirmation_comment(comment)
        return
    
    parent_comment = comment.parent()
    
    # Validate parent
    if (
        not parent_comment or
        parent_comment.banned_by is not None or
        not should_process_redditor(parent_comment.author) or
        parent_comment.author == comment.author
    ):
        comment.save()
        return
    
    comment_body = comment.body.lower()
    
    # Handle moderator approval (for replies to confirmations)
    if not parent_comment.is_root:
        if "approved" in comment_body and comment.author.name in FLAIR_MANAGER.current_mods:
            grandparent_comment = parent_comment.parent()
            if grandparent_comment and grandparent_comment.is_root:
                increment_trades(grandparent_comment, parent_comment)
        comment.save()
        return
    
    # Check if this is a confirmation
    if not is_confirming_trade(comment_body):
        comment.save()
        return
    
    # Check if already confirmed
    if parent_comment.saved:
        try:
            template = TEMPLATES.load("already_confirmed")
            reply_text = template.format(comment=comment, parent_comment=parent_comment)
            comment.reply(reply_text)
            LOGGER.info(
                "u/%s attempted duplicate confirmation: https://reddit.com%s",
                comment.author.name,
                comment.permalink,
            )
        except Exception as e:
            LOGGER.error("Failed to reply about duplicate: %s", e)
        comment.save()
        return
    
    # Verify user is mentioned in parent comment
    username_lower = comment.author.name.lower()
    parent_body_lower = parent_comment.body.lower()
    parent_html_lower = parent_comment.body_html.lower()
    
    if username_lower not in parent_body_lower and username_lower not in parent_html_lower:
        try:
            template = TEMPLATES.load("cant_confirm_username")
            reply_text = template.format(comment=comment, parent_comment=parent_comment)
            comment.reply(reply_text)
            LOGGER.info(
                "u/%s not mentioned in parent: https://reddit.com%s",
                comment.author.name,
                parent_comment.permalink,
            )
        except Exception as e:
            LOGGER.error("Failed to reply about missing username: %s", e)
        comment.save()
        return
    
    # All checks passed - confirm the trade
    LOGGER.info("Confirming trade: https://reddit.com%s", comment.permalink)
    increment_trades(parent_comment, comment)


# ============================================================================
# Stream Handlers (using praw-bot-wrapper decorators)
# ============================================================================

@praw_bot_wrapper.stream_handler(SUBREDDIT.stream.comments)
def handle_comment_stream(comment: praw.models.Comment) -> None:
    """Process comments from the stream."""
    # Skip if already processed
    if comment.saved:
        return
    
    # Skip if removed
    if comment.banned_by is not None:
        return
    
    # Skip if no author or bot's own comment
    if not should_process_redditor(comment.author):
        return
    
    LOGGER.info("Processing comment: https://reddit.com%s", comment.permalink)
    
    try:
        # AutoModerator special handling
        if comment.author and comment.author.name.lower() == "automoderator":
            handle_automoderator_comment(comment)
            return
        
        # Check if comment is in bot's submission (confirmation thread)
        if comment.submission.author == BOT.user.me():
            handle_confirmation_thread(comment)
        else:
            handle_non_confirmation_thread(comment)
    
    except Exception as e:
        LOGGER.error("Error processing comment %s: %s", comment.id, e, exc_info=True)
        # Don't re-raise - let the bot continue


@praw_bot_wrapper.outage_recovery_handler(outage_threshold=10)
def handle_outage_recovery(started_at) -> None:
    """Handle recovery from Reddit API outage."""
    message = f"Bot recovered from outage (started at {started_at}) - r/{SUBREDDIT_NAME}"
    LOGGER.warning(message)
    send_pushover_notification(message)


# ============================================================================
# Monthly Post Management
# ============================================================================

def create_monthly_post() -> None:
    """Create the monthly confirmation thread."""
    bot_user = BOT.user.me()
    previous_submission = next(bot_user.submissions.new(limit=1))
    submission_date = datetime.fromtimestamp(previous_submission.created_utc, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    
    # Check if already created this month
    is_same_month = (
        submission_date.year == now.year and
        submission_date.month == now.month
    )
    
    if is_same_month:
        LOGGER.info("Monthly post already exists for this month")
        return
    
    # Load templates
    post_template = TEMPLATES.load("monthly_post")
    title_template = TEMPLATES.load("monthly_post_title")
    
    LOGGER.info("Creating monthly post for r/%s", SUBREDDIT_NAME)
    send_pushover_notification(f"Creating monthly post for r/{SUBREDDIT_NAME}")
    
    # Unsticky previous post
    if previous_submission.stickied:
        previous_submission.mod.sticky(state=False)
    
    # Create new post
    new_submission = SUBREDDIT.submit(
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


def lock_previous_submissions() -> None:
    """Lock submissions from previous months."""
    LOGGER.info("Locking previous submissions")
    bot_user = BOT.user.me()
    
    for submission in bot_user.submissions.new(limit=10):
        if submission.stickied:
            continue
        if not submission.locked:
            try:
                submission.mod.lock()
                LOGGER.info("Locked: https://reddit.com%s", submission.permalink)
            except Exception as e:
                LOGGER.error("Failed to lock submission: %s", e)


def catch_up_on_missed_comments() -> None:
    """Process any comments that were missed while bot was offline."""
    bot_user = BOT.user.me()
    current_submission = next(bot_user.submissions.new(limit=1))
    current_submission.comment_sort = "new"
    current_submission.comments.replace_more(limit=None)
    
    LOGGER.info("Starting catch-up process")
    processed = 0
    
    for comment in current_submission.comments.list():
        if not comment.saved:
            try:
                handle_confirmation_thread(comment)
                processed += 1
            except Exception as e:
                LOGGER.error("Error in catch-up for %s: %s", comment.id, e)
    
    LOGGER.info("Catch-up complete: processed %d comments", processed)


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            command = sys.argv[1]
            
            if command == "create-monthly":
                create_monthly_post()
            
            elif command == "lock-submissions":
                send_pushover_notification(
                    f"Locking previous month's posts for r/{SUBREDDIT_NAME}"
                )
                lock_previous_submissions()
            
            elif command == "catch-up":
                catch_up_on_missed_comments()
            
            else:
                print(f"Unknown command: {command}")
                print("Available commands: create-monthly, lock-submissions, catch-up")
                sys.exit(1)
        
        else:
            # Normal operation - start the bot
            LOGGER.info("Bot starting up")
            send_pushover_notification(f"Bot startup for r/{SUBREDDIT_NAME}")
            
            # Catch up on any missed comments
            catch_up_on_missed_comments()
            
            # Start streaming (this blocks forever)
            praw_bot_wrapper.run()
    
    except KeyboardInterrupt:
        LOGGER.info("Bot shutdown requested")
        send_pushover_notification(f"Bot shutdown for r/{SUBREDDIT_NAME}")
    
    except Exception as e:
        LOGGER.exception("Fatal error in main")
        send_pushover_notification(f"Bot crashed for r/{SUBREDDIT_NAME}")
        send_pushover_notification(str(e)[:200])  # Truncate error message
        raise