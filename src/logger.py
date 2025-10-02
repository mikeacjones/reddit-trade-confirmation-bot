"""Logging configuration for the trade confirmation bot."""
import logging
import sys


def setup_custom_logger(name: str):
    """Set up the logger."""
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler = logging.FileHandler("log.txt", mode="w")
    handler.setFormatter(formatter)
    screen_handler = logging.StreamHandler(stream=sys.stdout)
    screen_handler.setFormatter(formatter)
    loggr = logging.getLogger(name)
    loggr.setLevel(logging.INFO)
    loggr.addHandler(handler)
    loggr.addHandler(screen_handler)
    return loggr


LOGGER = setup_custom_logger("trade-confirmation-bot")
