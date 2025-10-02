"""Settings and configuration management for the trade confirmation bot."""
import os
import re
from praw import Reddit
from logger import LOGGER
from helpers_template import load_template
from helpers_flair import load_flair_templates


class Settings:
    """Manages bot settings and configuration."""
    
    def __init__(self, bot: Reddit, subreddit_name: str):
        self.BOT = bot
        self.SUBREDDIT_NAME = subreddit_name
        self.SUBREDDIT = bot.subreddit(subreddit_name)
        self.ME = bot.user.me()
        self.BOT_NAME = self.ME.name
        self.FULLNAME = self.ME.fullname
        
        # Load templates
        self.TRADE_CONFIRMATION_TEMPLATE = load_template(
            self.SUBREDDIT, "trade_confirmation"
        )
        self.ALREADY_CONFIRMED_TEMPLATE = load_template(
            self.SUBREDDIT, "already_confirmed"
        )
        self.CANT_CONFIRM_USERNAME_TEMPLATE = load_template(
            self.SUBREDDIT, "cant_confirm_username"
        )
        self.NO_HISTORY_TEMPLATE = load_template(self.SUBREDDIT, "no_history")
        self.OLD_CONFIRMATION_THREAD = load_template(
            self.SUBREDDIT, "old_confirmation_thread"
        )
        
        # Load flair templates
        self.FLAIR_TEMPLATES = load_flair_templates(self.SUBREDDIT)
        
        # Get current moderators
        self.CURRENT_MODS = [str(mod) for mod in self.SUBREDDIT.moderator()]
        
        # Load environment variables
        self.MONTHLY_POST_FLAIR_ID = os.getenv("MONTHLY_POST_FLAIR_ID", None)
        
        # Regex patterns
        self.FLAIR_PATTERN = re.compile(r"Trades: (\d+)")
        self.FLAIR_TEMPLATE_PATTERN = re.compile(r"Trades: ((\d+)-(\d+))")
        
        LOGGER.info("Settings loaded for r/%s", subreddit_name)
    
    def reload(self, bot: Reddit, subreddit_name: str):
        """Reloads all settings from Reddit."""
        self.__init__(bot, subreddit_name)
        LOGGER.info("Settings reloaded")
