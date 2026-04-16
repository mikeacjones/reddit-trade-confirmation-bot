"""Plain data models for the trade confirmation bot."""

from dataclasses import dataclass, field


@dataclass
class CommentData:
    """Serializable comment data for passing between layers."""

    id: str
    body: str
    author_name: str
    created_utc: float
    is_root: bool
    submission_id: str


@dataclass
class ValidationResult:
    """Result of validating a confirmation comment."""

    valid: bool
    reason: str | None = None
    parent_author: str | None = None
    confirmer: str | None = None
    parent_comment_id: str | None = None
    is_mod_approval: bool = False
    reply_to_comment_id: str | None = None


@dataclass
class ConfirmationContext:
    """Pre-fetched parent/grandparent data for confirmation validation."""

    parent_exists: bool
    parent_is_banned: bool = False
    parent_is_processable: bool = False
    parent_author_name: str = ""
    parent_id: str = ""
    parent_is_root: bool = False
    parent_is_saved: bool = False
    parent_body_lower: str = ""
    parent_body_html_lower: str = ""
    is_moderator: bool = False
    grandparent_exists: bool = False
    grandparent_is_root: bool = False
    grandparent_author_name: str = ""
    grandparent_id: str = ""


@dataclass
class FlairUpdateResult:
    """Result of updating a user's flair."""

    new_flair: str | None


@dataclass
class FlairIncrementRequest:
    """Request to apply a flair increment for a user."""

    username: str
    request_id: str
    delta: int = 1


@dataclass
class FlairIncrementResult:
    """Result of a coordinated flair increment operation."""

    old_flair: str | None = None
    new_flair: str | None = None


@dataclass
class ActiveSubmissions:
    """Current and previous tracked submission IDs."""

    current_submission_id: str | None = None
    previous_submission_id: str | None = None


@dataclass
class SubmissionInput:
    """Input for single-submission operations."""

    submission_id: str


@dataclass
class CreateMonthlyPostInput:
    """Input for creating a monthly confirmation post."""

    previous_submission_id: str | None = None


@dataclass
class FetchCommentsInput:
    """Input for the long-running comment polling activity."""

    seen_ids: list[str] = field(default_factory=list)
    active_submission_ids: list[str] = field(default_factory=list)
    current_submission_id: str = ""


@dataclass
class FetchCommentsResult:
    """Result of fetching new comments."""

    comments: list[CommentData] = field(default_factory=list)
    scanned_ids: list[str] = field(default_factory=list)
    found_seen: bool = True
    scanned_count: int = 0
    possible_gap: bool = False


@dataclass
class ReplyToCommentInput:
    """Input for replying to a comment with a template."""

    comment_id: str
    template_name: str
    format_args: dict | None = None


@dataclass
class SetUserFlairInput:
    """Input for setting a user's flair."""

    username: str
    new_count: int
    old_flair: str | None = None


@dataclass
class UserFlairResult:
    """Result of getting a user's current flair."""

    flair_text: str | None
    trade_count: int | None
    is_trade_tracked: bool
