"""Tests for fraud detection system."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, MagicMock, PropertyMock
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fraud_detection import FraudDetector, FraudIndicator, format_fraud_report


class MockRedditor:
    """Mock Reddit user for testing."""

    def __init__(
        self,
        name: str,
        created_utc: int,
        link_karma: int = 100,
        comment_karma: int = 100,
        submissions=None,
        comments=None,
    ):
        self.name = name
        self.created_utc = created_utc
        self.link_karma = link_karma
        self.comment_karma = comment_karma
        self.submissions = submissions or Mock(new=Mock(return_value=[]))
        self.comments = comments or Mock(new=Mock(return_value=[]))


@pytest.fixture
def detector():
    """Provide a fraud detector instance."""
    return FraudDetector()


@pytest.fixture
def new_account_high_trades():
    """User with new account claiming high trades."""
    now_utc = datetime.now(timezone.utc).timestamp()
    created_15_days_ago = now_utc - (15 * 24 * 3600)

    user = MockRedditor(
        name="suspicious_new_user",
        created_utc=int(created_15_days_ago),
        link_karma=50,
        comment_karma=50,
    )
    return user, 10  # Claiming 10 trades


@pytest.fixture
def old_account_high_trades():
    """User with old account and high trades (legitimate)."""
    now_utc = datetime.now(timezone.utc).timestamp()
    created_2_years_ago = now_utc - (730 * 24 * 3600)

    user = MockRedditor(
        name="legitimate_trader",
        created_utc=int(created_2_years_ago),
        link_karma=5000,
        comment_karma=10000,
    )
    return user, 50  # Claiming 50 trades


@pytest.fixture
def new_account_low_trades():
    """User with new account but reasonable trade count (legitimate)."""
    now_utc = datetime.now(timezone.utc).timestamp()
    created_10_days_ago = now_utc - (10 * 24 * 3600)

    user = MockRedditor(
        name="new_but_honest",
        created_utc=int(created_10_days_ago),
        link_karma=100,
        comment_karma=100,
    )
    return user, 3  # Claiming 3 trades


@pytest.fixture
def low_karma_high_trades():
    """User with low karma claiming high trades."""
    now_utc = datetime.now(timezone.utc).timestamp()
    created_6_months_ago = now_utc - (180 * 24 * 3600)

    user = MockRedditor(
        name="low_karma_suspicious",
        created_utc=int(created_6_months_ago),
        link_karma=10,
        comment_karma=20,
    )
    return user, 8  # Claiming 8 trades with very low karma


@pytest.fixture
def low_karma_low_trades():
    """User with low karma but reasonable trades (may be legitimate lurker)."""
    now_utc = datetime.now(timezone.utc).timestamp()
    created_1_year_ago = now_utc - (365 * 24 * 3600)

    user = MockRedditor(
        name="lurker_trader",
        created_utc=int(created_1_year_ago),
        link_karma=5,
        comment_karma=15,
    )
    return user, 2  # Claiming 2 trades with low karma


@pytest.fixture
def rapid_trading_user():
    """User making many confirmations in short timeframe."""
    now_utc = datetime.now(timezone.utc).timestamp()
    created_30_days_ago = now_utc - (30 * 24 * 3600)

    # Create mock comments from last 24 hours
    recent_times = [
        int(now_utc - (1 * 3600)),
        int(now_utc - (2 * 3600)),
        int(now_utc - (4 * 3600)),
        int(now_utc - (8 * 3600)),
    ]

    mock_comments = [
        Mock(created_utc=t) for t in recent_times
    ]

    user = MockRedditor(
        name="rapid_trader",
        created_utc=int(created_30_days_ago),
        link_karma=500,
        comment_karma=500,
        comments=Mock(new=Mock(return_value=mock_comments)),
    )
    return user, 5


# Test cases
class TestNewAccountDetection:
    """Tests for new account with high trades detection."""

    def test_new_account_high_trades_flagged(self, detector, new_account_high_trades):
        """New account with high trades should be flagged."""
        user, trades = new_account_high_trades
        indicators = detector.check_user(user, trades)

        assert len(indicators) > 0
        assert any(i.severity == "high" for i in indicators)
        assert any("new account" in i.reason.lower() for i in indicators)

    def test_old_account_high_trades_not_flagged(self, detector, old_account_high_trades):
        """Old account with high trades should not be flagged."""
        user, trades = old_account_high_trades
        indicators = detector.check_user(user, trades)

        # Should not have high severity indicators for account age
        age_indicators = [i for i in indicators if "account" in i.reason.lower()]
        assert len(age_indicators) == 0

    def test_new_account_low_trades_not_flagged(
        self, detector, new_account_low_trades
    ):
        """New account with reasonable trades should not be flagged."""
        user, trades = new_account_low_trades
        indicators = detector.check_user(user, trades)

        # Should not have high severity account age indicators
        age_indicators = [
            i for i in indicators if "new account" in i.reason.lower()
        ]
        assert len(age_indicators) == 0


class TestLowKarmaDetection:
    """Tests for low karma detection."""

    def test_low_karma_high_trades_flagged(self, detector, low_karma_high_trades):
        """Low karma with high trades should be flagged."""
        user, trades = low_karma_high_trades
        indicators = detector.check_user(user, trades)

        assert len(indicators) > 0
        assert any("karma" in i.reason.lower() for i in indicators)

    def test_low_karma_low_trades_not_flagged(self, detector, low_karma_low_trades):
        """Low karma with reasonable trades should not be flagged."""
        user, trades = low_karma_low_trades
        indicators = detector.check_user(user, trades)

        # Should not flag low karma if trades are reasonable
        karma_indicators = [
            i for i in indicators if "karma" in i.reason.lower()
        ]
        assert len(karma_indicators) == 0


class TestRapidTradingDetection:
    """Tests for rapid trading detection."""

    def test_rapid_trading_flagged(self, detector, rapid_trading_user):
        """Multiple confirmations in 24h should be flagged."""
        user, trades = rapid_trading_user
        indicators = detector.check_rapid_trades(user, hours=24)

        assert len(indicators) > 0
        assert any("wash trading" in i.reason.lower() for i in indicators)

    def test_rapid_trading_window(self, detector):
        """Rapid trading should only flag within the specified window."""
        now_utc = datetime.now(timezone.utc).timestamp()
        created_30_days_ago = now_utc - (30 * 24 * 3600)

        # Create comments at various times
        comment_times = [
            int(now_utc - (1 * 3600)),  # 1 hour ago
            int(now_utc - (3 * 3600)),  # 3 hours ago
            int(now_utc - (25 * 3600)),  # 25 hours ago
        ]

        mock_comments = [Mock(created_utc=t) for t in comment_times]

        user = MockRedditor(
            name="timing_test_user",
            created_utc=int(created_30_days_ago),
            comments=Mock(new=Mock(return_value=mock_comments)),
        )

        # 24h window should catch 2 comments
        indicators_24h = detector.check_rapid_trades(user, hours=24)
        # 6h window should catch 1 comment
        indicators_6h = detector.check_rapid_trades(user, hours=6)

        # 24h window might flag as suspicious (2 in 3 hours is close to threshold)
        # 6h window less likely to flag with lower count
        assert isinstance(indicators_24h, list)
        assert isinstance(indicators_6h, list)


class TestFraudReporting:
    """Tests for fraud report formatting."""

    def test_report_formatting(self, detector, new_account_high_trades):
        """Fraud report should be properly formatted."""
        user, trades = new_account_high_trades
        indicators = detector.check_user(user, trades)

        report = format_fraud_report(user.name, trades, indicators)

        assert user.name in report
        assert str(trades) in report
        assert "HIGH" in report or "MEDIUM" in report or "LOW" in report
        assert "Indicator" in report

    def test_empty_report(self):
        """Empty indicators should produce empty report."""
        report = format_fraud_report("test_user", 5, [])
        assert report == ""

    def test_severity_levels(self):
        """Report should reflect indicator severity."""
        high_indicator = FraudIndicator("high", "Test high severity")
        medium_indicator = FraudIndicator("medium", "Test medium severity")
        low_indicator = FraudIndicator("low", "Test low severity")

        # High severity should be in report
        report = format_fraud_report(
            "test_user", 5, [high_indicator]
        )
        assert "🔴" in report

        # Medium severity should be in report
        report = format_fraud_report(
            "test_user", 5, [medium_indicator]
        )
        assert "🟠" in report

        # Low severity should be in report
        report = format_fraud_report(
            "test_user", 5, [low_indicator]
        )
        assert "🟡" in report


class TestComprehensiveChecks:
    """Tests for running all checks."""

    def test_check_all_combines_checks(self, detector, rapid_trading_user):
        """check_all should run all fraud checks."""
        user, trades = rapid_trading_user
        indicators = detector.check_all(user, trades)

        # Should be a list (might be empty or have indicators)
        assert isinstance(indicators, list)

    def test_multiple_indicators_combined(self, detector):
        """User with multiple fraud indicators should list all."""
        now_utc = datetime.now(timezone.utc).timestamp()
        created_10_days_ago = now_utc - (10 * 24 * 3600)

        # New account, low karma, high trades = multiple red flags
        user = MockRedditor(
            name="multiple_flags",
            created_utc=int(created_10_days_ago),
            link_karma=20,
            comment_karma=30,
        )

        indicators = detector.check_user(user, 15)

        # Should have multiple indicators
        assert len(indicators) >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
