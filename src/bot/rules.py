"""Pure business rules for the trade confirmation bot."""

import re

FLAIR_PATTERN = re.compile(r"Trades: (\d+)")
FLAIR_TEMPLATE_PATTERN = re.compile(r"Trades: ((\d+)-(\d+))")


def is_confirming_trade(comment_body: str) -> bool:
    """Check if comment is confirming a trade."""
    return "confirmed" in comment_body.lower()


def build_confirmation_key(parent_comment_id: str | None, confirmer: str) -> str:
    """Build the idempotency key used for paired flair increments."""
    return f"{parent_comment_id}:{confirmer}".lower()


def parse_trade_count(flair_text: str | None) -> int | None:
    """Extract tracked trade count from flair text."""
    if not flair_text:
        return 0

    match = FLAIR_PATTERN.search(flair_text)
    return int(match.group(1)) if match else None


def format_flair_from_template(flair_template: str, count: int) -> str:
    """Replace the tracked trade range in a flair template with the exact count."""
    match = FLAIR_TEMPLATE_PATTERN.search(flair_template)
    if not match:
        return flair_template

    start, end = match.span(1)
    return flair_template[:start] + str(count) + flair_template[end:]
