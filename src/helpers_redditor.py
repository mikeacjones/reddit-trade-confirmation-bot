"""Redditor-related helper functions."""
import prawcore.exceptions


def should_process_redditor(redditor, settings) -> bool:
    """Checks if this is an author where we should process their comment/submission."""
    try:
        if redditor is None:
            return False

        if not hasattr(redditor, "id"):
            return False

        if redditor.id == settings.ME.id:
            return False

        if hasattr(redditor, "is_suspended"):
            return not redditor.is_suspended
        return True
    except prawcore.exceptions.NotFound:
        return False


def get_redditor(bot, username: str):
    """Safely retrieves a Redditor object."""
    try:
        return bot.redditor(username)
    except prawcore.exceptions.NotFound:
        return None
