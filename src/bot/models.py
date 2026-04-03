"""Plain data models for the trade confirmation bot."""

from dataclasses import dataclass, field


@dataclass
class CommentData:
    """Serializable comment data for passing between layers."""

    id: str
    body: str
    body_html: str
    author_name: str
    author_flair_text: str | None
    permalink: str
    created_utc: float
    is_root: bool
    parent_id: str
    submission_id: str
    saved: bool


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
class FlairUpdateResult:
    """Result of updating a user's flair."""

    username: str
    old_flair: str | None
    new_flair: str | None
    success: bool


@dataclass
class FlairIncrementRequest:
    """Request to apply a flair increment for a user."""

    username: str
    request_id: str
    delta: int = 1


@dataclass
class FlairIncrementResult:
    """Result of a coordinated flair increment operation."""

    username: str
    applied: bool
    old_count: int | None = None
    new_count: int | None = None
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
    listing_exhausted: bool = False
    scanned_count: int = 0


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

    username: str
    flair_text: str | None
    trade_count: int | None
    is_trade_tracked: bool

