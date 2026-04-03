"""Flair management activities for Temporal bot."""

from temporalio import activity
from temporalio.client import Client, WithStartWorkflowOperation
from temporalio.common import WorkflowIDConflictPolicy

from bot.config import SUBREDDIT_NAME, TASK_QUEUE
from bot.models import FlairIncrementRequest, FlairIncrementResult, FlairUpdateResult, SetUserFlairInput, UserFlairResult
from bot.reddit import get_reddit_client, get_subreddit
from bot.rules import FLAIR_TEMPLATE_PATTERN, format_flair_from_template, parse_trade_count

_flair_templates: dict | None = None
_moderators: list | None = None


def _load_flair_templates(subreddit) -> dict:
    """Load flair templates from subreddit."""
    global _flair_templates
    if _flair_templates is not None:
        return _flair_templates

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
            activity.logger.info(
                "Loaded flair template: %d-%d trades", min_trades, max_trades
            )

    _flair_templates = templates
    return templates


def _load_moderators(subreddit) -> list:
    """Load list of current moderators."""
    global _moderators
    if _moderators is not None:
        return _moderators

    _moderators = [str(mod) for mod in subreddit.moderator()]
    return _moderators


def _get_flair_template(trade_count: int, username: str, subreddit) -> dict | None:
    """Get appropriate flair template for trade count."""
    templates = _load_flair_templates(subreddit)
    moderators = _load_moderators(subreddit)

    for (min_trades, max_trades), template in templates.items():
        if min_trades <= trade_count <= max_trades:
            if template["mod_only"] == (username in moderators):
                return template
    return None


def apply_flair(username: str, count: int, subreddit) -> str | None:
    """Set user's flair to specific trade count. Returns new flair text or None."""
    template = _get_flair_template(count, username, subreddit)
    if not template:
        activity.logger.warning("No flair template found for %d trades", count)
        return None

    new_flair_text = format_flair_from_template(template["template"], count)
    subreddit.flair.set(username, text=new_flair_text, flair_template_id=template["id"])
    return new_flair_text


def is_moderator(username: str, subreddit) -> bool:
    """Check if user is a moderator."""
    moderators = _load_moderators(subreddit)
    return username in moderators


@activity.defn
def get_user_flair(username: str) -> UserFlairResult:
    """Get a user's current flair information.

    This is a read-only activity that returns the user's current flair text
    and trade count. Used by workflows to calculate new flair values before
    calling set_user_flair.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)

    flair_text = next(subreddit.flair(username))["flair_text"]
    trade_count = parse_trade_count(flair_text)

    return UserFlairResult(
        username=username,
        flair_text=flair_text,
        trade_count=trade_count,
        is_trade_tracked=trade_count is not None,
    )

class FlairCoordinatorActivity:
    """Activity wrapper that reuses the worker's Temporal client."""

    def __init__(self, client: Client) -> None:
        self._client = client

    @activity.defn
    async def request_flair_increment(
        self, request: FlairIncrementRequest
    ) -> FlairIncrementResult:
        """Route increment requests through the centralized coordinator workflow."""
        # Lazy import avoids a circular dependency with the coordinator workflow.
        from ..workflows.flair_coordinator import FlairCoordinatorWorkflow

        start_op = WithStartWorkflowOperation(
            FlairCoordinatorWorkflow.run,
            id=f"flair-coordinator-{SUBREDDIT_NAME}",
            task_queue=TASK_QUEUE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )

        return await self._client.execute_update_with_start_workflow(
            FlairCoordinatorWorkflow.apply_increment,
            request,
            start_workflow_operation=start_op,
        )


@activity.defn
def set_user_flair(input: SetUserFlairInput) -> FlairUpdateResult:
    """Set a user's flair to a specific trade count.

    This activity is idempotent - calling it multiple times with the same
    new_count will always result in the same flair being set.

    The workflow is responsible for:
    1. Reading current flair via get_user_flair
    2. Calculating the new count (current + 1)
    3. Passing the exact new_count to this activity

    With a single worker, this ensures that even if the activity retries
    after a crash, the same value is always set.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)

    # Set flair to the exact value specified by the workflow
    new_flair = apply_flair(input.username, input.new_count, subreddit)
    activity.logger.info("u/%s flair set: '%s' -> '%s'", input.username, input.old_flair, new_flair)

    return FlairUpdateResult(
        username=input.username,
        old_flair=input.old_flair,
        new_flair=new_flair,
        success=new_flair is not None,
    )
