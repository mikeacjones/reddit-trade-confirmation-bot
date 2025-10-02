"""General helper functions for the trade confirmation bot."""
import os
import json
import boto3


def load_secrets(subreddit_name: str) -> dict:
    """Loads secrets from AWS Secrets Manager."""
    secrets_manager = boto3.client("secretsmanager")
    secrets_response = secrets_manager.get_secret_value(
        SecretId=f"trade-confirmation-bot/{subreddit_name}"
    )
    return json.loads(secrets_response["SecretString"])


def sint(value, default=0):
    """Safe integer conversion."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
