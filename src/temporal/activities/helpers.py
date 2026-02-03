"""Shared helpers for Reddit activities."""

import logging

import prawcore.exceptions


class TemplateManager:
    """Manages loading and caching of message templates."""

    _cache: dict = {}

    @classmethod
    def load(cls, template_name: str, subreddit) -> str:
        """Load template from wiki or local file system."""
        if template_name in cls._cache:
            return cls._cache[template_name]

        logger = logging.getLogger(__name__)
        try:
            wiki_page = subreddit.wiki[f"trade-confirmation-bot/{template_name}"]
            content = wiki_page.content_md
            logger.info("Loaded template '%s' from wiki", template_name)
        except (prawcore.exceptions.NotFound, prawcore.exceptions.Forbidden):
            with open(
                f"src/mdtemplates/{template_name}.md", "r", encoding="utf-8"
            ) as f:
                content = f.read()
            logger.info("Loaded template '%s' from file", template_name)

        cls._cache[template_name] = content
        return content
