"""Template loading helpers."""
import prawcore.exceptions
from logger import LOGGER


def load_template(subreddit, template: str) -> str:
    """Loads a template either from local file or Reddit Wiki, returned as a string."""
    try:
        wiki = subreddit.wiki[f"trade-confirmation-bot/{template}"]
        LOGGER.info("Loaded template %s from wiki", template)
        return wiki.content_md
    except (prawcore.exceptions.NotFound, prawcore.exceptions.Forbidden):
        with open(f"src/mdtemplates/{template}.md", "r", encoding="utf-8") as file:
            LOGGER.info(
                "Loading template %s from src/mdtemplates/%s.md", template, template
            )
            return file.read()
