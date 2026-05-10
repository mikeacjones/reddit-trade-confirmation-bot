"""Temporal search attributes used by the bot workflows."""

import logging
from typing import TYPE_CHECKING

from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)

if TYPE_CHECKING:
    from temporalio.client import Client

logger = logging.getLogger(__name__)

REDDIT_SUBREDDIT = SearchAttributeKey.for_keyword("RedditSubreddit")
REDDIT_COMMENT_ID = SearchAttributeKey.for_keyword("RedditCommentId")
REDDIT_SUBMISSION_ID = SearchAttributeKey.for_keyword("RedditSubmissionId")
REDDIT_CONFIRMATION_STATUS = SearchAttributeKey.for_keyword(
    "RedditConfirmationStatus"
)

CUSTOM_SEARCH_ATTRIBUTE_KEYS = (
    REDDIT_SUBREDDIT,
    REDDIT_COMMENT_ID,
    REDDIT_SUBMISSION_ID,
    REDDIT_CONFIRMATION_STATUS,
)


def subreddit_search_attributes(subreddit_name: str) -> TypedSearchAttributes:
    """Build the common visibility attributes for subreddit-scoped workflows."""
    return TypedSearchAttributes(
        [SearchAttributePair(REDDIT_SUBREDDIT, subreddit_name)]
    )


def confirmation_search_attributes(
    subreddit_name: str,
    comment_id: str,
    submission_id: str,
    status: str,
) -> TypedSearchAttributes:
    """Build visibility attributes for a single confirmation workflow."""
    return TypedSearchAttributes(
        [
            SearchAttributePair(REDDIT_SUBREDDIT, subreddit_name),
            SearchAttributePair(REDDIT_COMMENT_ID, comment_id),
            SearchAttributePair(REDDIT_SUBMISSION_ID, submission_id),
            SearchAttributePair(REDDIT_CONFIRMATION_STATUS, status),
        ]
    )


async def ensure_search_attributes(client: "Client", namespace: str) -> None:
    """Create required custom search attributes if the namespace needs them."""
    from temporalio.api.operatorservice.v1 import (
        AddSearchAttributesRequest,
        ListSearchAttributesRequest,
    )
    from temporalio.service import RPCError, RPCStatusCode

    response = await client.operator_service.list_search_attributes(
        ListSearchAttributesRequest(namespace=namespace),
        retry=True,
    )

    missing: dict[str, int] = {}
    mismatched: list[str] = []
    for key in CUSTOM_SEARCH_ATTRIBUTE_KEYS:
        expected_type = int(key.indexed_value_type)
        if key.name in response.custom_attributes:
            actual_type = response.custom_attributes[key.name]
        elif key.name in response.system_attributes:
            actual_type = response.system_attributes[key.name]
        else:
            missing[key.name] = expected_type
            continue

        if actual_type != expected_type:
            mismatched.append(
                f"{key.name} exists as {actual_type}, expected {expected_type}"
            )

    if mismatched:
        raise RuntimeError(
            "Temporal search attribute type mismatch: " + "; ".join(mismatched)
        )

    if not missing:
        logger.info("Temporal search attributes already registered")
        return

    try:
        await client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=namespace,
                search_attributes=missing,
            ),
            retry=True,
        )
    except RPCError as err:
        if err.status == RPCStatusCode.ALREADY_EXISTS:
            logger.info("Temporal search attributes already registered")
            return
        raise

    logger.info(
        "Registered Temporal search attributes: %s",
        ", ".join(sorted(missing)),
    )
