"""Trade history verification helpers."""
from datetime import datetime, timedelta
from logger import LOGGER


def check_trade_history(user1, user2, settings) -> bool:
    """
    Checks if two users have a history of interaction on the subreddit.
    Returns True if user1 created a post that user2 commented on, or vice versa.
    """
    try:
        # Check if user1 created a post that user2 commented on
        for submission in user1.submissions.new(limit=100):
            if submission.subreddit.display_name != settings.SUBREDDIT_NAME:
                continue
            
            # Skip old posts (older than 6 months)
            submission_time = datetime.utcfromtimestamp(submission.created_utc)
            if datetime.utcnow() - submission_time > timedelta(days=180):
                continue
            
            submission.comments.replace_more(limit=0)
            for comment in submission.comments.list():
                if comment.author and comment.author.name == user2.name:
                    LOGGER.info(
                        "Found interaction: u/%s commented on u/%s's post at https://reddit.com%s",
                        user2.name,
                        user1.name,
                        submission.permalink,
                    )
                    return True
        
        # Check if user2 created a post that user1 commented on
        for submission in user2.submissions.new(limit=100):
            if submission.subreddit.display_name != settings.SUBREDDIT_NAME:
                continue
            
            # Skip old posts (older than 6 months)
            submission_time = datetime.utcfromtimestamp(submission.created_utc)
            if datetime.utcnow() - submission_time > timedelta(days=180):
                continue
            
            submission.comments.replace_more(limit=0)
            for comment in submission.comments.list():
                if comment.author and comment.author.name == user1.name:
                    LOGGER.info(
                        "Found interaction: u/%s commented on u/%s's post at https://reddit.com%s",
                        user1.name,
                        user2.name,
                        submission.permalink,
                    )
                    return True
        
        return False
    except Exception as ex:
        LOGGER.exception("Error checking trade history: %s", ex)
        return False
