"""Fraud detection system for trade confirmation bot."""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import praw.models

LOGGER = logging.getLogger(__name__)


class FraudIndicator:
    """Represents a potential fraud indicator."""

    def __init__(self, severity: str, reason: str, details: Optional[Dict] = None):
        """
        Initialize a fraud indicator.

        Args:
            severity: 'low', 'medium', or 'high'
            reason: Human-readable reason for the indicator
            details: Additional context about the indicator
        """
        self.severity = severity
        self.reason = reason
        self.details = details or {}

    def __repr__(self) -> str:
        return f"FraudIndicator({self.severity}: {self.reason})"

    def to_string(self) -> str:
        """Convert to human-readable format."""
        return f"**[{self.severity.upper()}]** {self.reason}\n"


class FraudDetector:
    """Detects potential fraud in trade confirmations."""

    # Configuration thresholds
    NEW_ACCOUNT_DAYS = 30  # Account younger than this is considered "new"
    NEW_ACCOUNT_MAX_TRADES = 5  # New accounts claiming more trades are suspicious
    LOW_KARMA_THRESHOLD = 100  # Low karma threshold
    LOW_KARMA_MAX_TRADES = 5  # Low karma users claiming more trades are suspicious
    RAPID_TRADE_HOURS = 24  # Window for rapid trade detection
    RAPID_TRADE_THRESHOLD = 3  # Number of trades in window to be suspicious
    ACTIVITY_GAP_MONTHS = 6  # Long gap between account creation and first trade

    def __init__(self):
        """Initialize fraud detector."""
        pass

    def check_user(
        self, redditor: praw.models.Redditor, claimed_trades: int
    ) -> List[FraudIndicator]:
        """
        Check a user for fraud indicators based on their profile.

        Args:
            redditor: The Reddit user to check
            claimed_trades: Number of trades they're claiming

        Returns:
            List of FraudIndicator objects if issues found, empty list otherwise
        """
        indicators = []

        try:
            # Check 1: New account with high trade claims
            account_age = self._get_account_age_days(redditor)
            if (
                account_age < self.NEW_ACCOUNT_DAYS
                and claimed_trades > self.NEW_ACCOUNT_MAX_TRADES
            ):
                indicators.append(
                    FraudIndicator(
                        severity="high",
                        reason=f"New account ({account_age} days old) claiming {claimed_trades} trades",
                        details={
                            "account_age_days": account_age,
                            "claimed_trades": claimed_trades,
                        },
                    )
                )

            # Check 2: Low karma with high trade claims
            if redditor.link_karma + redditor.comment_karma < self.LOW_KARMA_THRESHOLD:
                if claimed_trades > self.LOW_KARMA_MAX_TRADES:
                    indicators.append(
                        FraudIndicator(
                            severity="medium",
                            reason=f"Low karma ({redditor.link_karma + redditor.comment_karma}) claiming {claimed_trades} trades",
                            details={
                                "total_karma": redditor.link_karma
                                + redditor.comment_karma,
                                "claimed_trades": claimed_trades,
                            },
                        )
                    )

            # Check 3: Suspicious account activity gap
            if account_age > self.ACTIVITY_GAP_MONTHS * 30:
                activity_gap = self._get_activity_gap_days(redditor)
                if activity_gap > self.ACTIVITY_GAP_MONTHS * 30:
                    indicators.append(
                        FraudIndicator(
                            severity="low",
                            reason=f"Long inactivity gap ({activity_gap} days) before trade activity",
                            details={"activity_gap_days": activity_gap},
                        )
                    )

        except Exception as e:
            LOGGER.error("Error checking user %s for fraud: %s", redditor.name, e)

        return indicators

    def check_rapid_trades(
        self, redditor: praw.models.Redditor, hours: int = 24
    ) -> List[FraudIndicator]:
        """
        Check if user has made too many trades in a short timeframe.

        Args:
            redditor: The Reddit user to check
            hours: Number of hours to look back (default 24)

        Returns:
            List of FraudIndicator objects if rapid trading detected
        """
        indicators = []

        try:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=hours)

            # Count comments in the subreddit within the time window
            # This is a simplified check - in real use, you'd need to track confirmation thread
            recent_comments = 0
            try:
                for comment in redditor.comments.new(limit=100):
                    comment_time = datetime.fromtimestamp(
                        comment.created_utc, tz=timezone.utc
                    )
                    if comment_time > cutoff:
                        recent_comments += 1
                    else:
                        break  # Comments are in reverse chronological order
            except Exception:
                pass  # User may have deleted comments or restricted profile

            if recent_comments >= self.RAPID_TRADE_THRESHOLD:
                indicators.append(
                    FraudIndicator(
                        severity="low",
                        reason=f"User made {recent_comments} confirmations in last {hours} hours (possible wash trading)",
                        details={
                            "confirmations": recent_comments,
                            "hours": hours,
                        },
                    )
                )

        except Exception as e:
            LOGGER.error(
                "Error checking rapid trades for %s: %s", redditor.name, e
            )

        return indicators

    def check_all(
        self, redditor: praw.models.Redditor, claimed_trades: int
    ) -> List[FraudIndicator]:
        """
        Run all fraud checks on a user.

        Args:
            redditor: The Reddit user to check
            claimed_trades: Number of trades they're claiming

        Returns:
            List of all FraudIndicator objects found
        """
        indicators = []
        indicators.extend(self.check_user(redditor, claimed_trades))
        indicators.extend(self.check_rapid_trades(redditor))
        return indicators

    @staticmethod
    def _get_account_age_days(redditor: praw.models.Redditor) -> int:
        """Get account age in days."""
        created = datetime.fromtimestamp(redditor.created_utc, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - created).days

    @staticmethod
    def _get_activity_gap_days(redditor: praw.models.Redditor) -> int:
        """Get gap between account creation and first activity."""
        try:
            created = datetime.fromtimestamp(redditor.created_utc, tz=timezone.utc)
            first_post = next(redditor.submissions.new(limit=1))
            first_post_time = datetime.fromtimestamp(
                first_post.created_utc, tz=timezone.utc
            )
            return (first_post_time - created).days
        except StopIteration:
            # No submissions found
            return 0


def format_fraud_report(
    username: str, trade_count: int, indicators: List[FraudIndicator]
) -> str:
    """
    Format fraud indicators into a moderator notification.

    Args:
        username: The suspected user's username
        trade_count: The trade count they're claiming
        indicators: List of FraudIndicator objects

    Returns:
        Formatted message for moderators
    """
    if not indicators:
        return ""

    severity_levels = {"high": 0, "medium": 0, "low": 0}
    for indicator in indicators:
        severity_levels[indicator.severity] += 1

    report = f"⚠️ **Fraud Detection Alert** ⚠️\n\n"
    report += f"**User:** u/{username}\n"
    report += f"**Claimed Trades:** {trade_count}\n"
    report += f"**Alert Level:** "

    if severity_levels["high"] > 0:
        report += "🔴 **HIGH**\n\n"
    elif severity_levels["medium"] > 0:
        report += "🟠 **MEDIUM**\n\n"
    else:
        report += "🟡 **LOW**\n\n"

    report += "**Indicators:**\n\n"
    for indicator in indicators:
        report += indicator.to_string()

    report += "\n**Recommendation:** Review user's trading history and account activity.\n"

    return report
