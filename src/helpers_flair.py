"""Flair management helper functions."""
from logger import LOGGER

def load_flair_templates(subreddit):
    """Loads flair templates from Reddit, returned as a list."""
    templates = []
    for template in subreddit.flair.templates:
        templates.append(template)
    LOGGER.info("Loaded %s flair templates from r/%s", len(templates), subreddit.display_name)
    return templates


def current_flair_text(redditor, subreddit):
    """Uses an API call to ensure we have the latest flair text."""
    return next(subreddit.flair(redditor))["flair_text"]


def get_flair_template(trade_count: int, settings):
    """Gets the appropriate flair template for a given trade count."""
    for template in settings.FLAIR_TEMPLATES:
        text = template["flair_text"]
        match = settings.FLAIR_TEMPLATE_PATTERN.search(text)
        if match:
            min_trades = int(match.group(2))
            max_trades = int(match.group(3))
            if min_trades <= trade_count <= max_trades:
                return template
    return None


def set_flair(username: str, trade_count: int, settings):
    """Sets the flair for a user based on their trade count."""
    template = get_flair_template(trade_count, settings)
    if not template:
        LOGGER.warning("No flair template found for %s trades", trade_count)
        return
    
    flair_text = template["flair_text"].replace(
        settings.FLAIR_TEMPLATE_PATTERN.search(template["flair_text"]).group(1),
        str(trade_count)
    )
    
    settings.SUBREDDIT.flair.set(
        username,
        text=flair_text,
        flair_template_id=template["flair_template_id"]
    )
    LOGGER.info("Set flair for u/%s to %s", username, flair_text)


def increment_trades(parent_comment, comment, settings):
    """Increments trade count for both users involved in a trade."""
    
    # Get current trade counts
    parent_flair = current_flair_text(parent_comment.author, settings.SUBREDDIT)
    comment_flair = current_flair_text(comment.author, settings.SUBREDDIT)
    
    parent_trades = 0
    comment_trades = 0
    
    if parent_flair:
        match = settings.FLAIR_PATTERN.search(parent_flair)
        if match:
            parent_trades = int(match.group(1))
    
    if comment_flair:
        match = settings.FLAIR_PATTERN.search(comment_flair)
        if match:
            comment_trades = int(match.group(1))
    
    # Increment counts
    parent_trades += 1
    comment_trades += 1
    
    # Set new flairs
    set_flair(parent_comment.author.name, parent_trades, settings)
    set_flair(comment.author.name, comment_trades, settings)

    # Save comments to mark as processed
    comment.save()
    parent_comment.save()
    
    # Get new flair text for response
    new_parent_flair = current_flair_text(parent_comment.author, settings.SUBREDDIT)
    new_comment_flair = current_flair_text(comment.author, settings.SUBREDDIT)

    # Reply with confirmation
    comment.reply(
        settings.TRADE_CONFIRMATION_TEMPLATE.format(
            comment=comment,
            parent_comment=parent_comment,
            new_parent_flair=new_parent_flair,
            new_comment_flair=new_comment_flair,
        )
    )
    
    LOGGER.info(
        "Confirmed trade between u/%s and u/%s at https://reddit.com%s",
        parent_comment.author.name,
        comment.author.name,
        comment.permalink,
    )
