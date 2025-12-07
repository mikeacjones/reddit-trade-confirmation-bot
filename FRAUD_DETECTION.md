# Fraud Detection System

The fraud detection system monitors trade confirmations for suspicious patterns that may indicate fraudulent activity. It notifies moderators via modmail when potential fraud is detected.

## Features

- **Disabled by default** - Fraud detection is opt-in via environment variable
- **No automatic actions** - Only sends notifications to moderators, doesn't remove trades or ban users
- **Detailed reports** - Includes reasons for suspicion and user details
- **Multiple detection methods** - Checks various fraud patterns
- **Severity levels** - Categorizes alerts as LOW, MEDIUM, or HIGH severity

## Configuration

Enable fraud detection by setting the environment variable in your `.env` file:

```
FRAUD_DETECTION_ENABLED=true
```

Default is `false` (disabled).

## Detection Patterns

The fraud detection system checks for the following patterns:

### 1. New Account with High Trade Claims (HIGH severity)
- Account age < 30 days
- Claiming > 5 trades
- **Indicates**: Possible account takeover or new scammer account

### 2. Low Karma with High Trade Claims (MEDIUM severity)
- Total karma < 100
- Claiming > 5 trades
- **Indicates**: Unusual activity or potential scam account

### 3. Long Activity Gaps (LOW severity)
- Account created > 6 months ago
- No activity for > 6 months before trading
- **Indicates**: Dormant account suddenly activated

### 4. Rapid Repeated Trades (LOW severity)
- 3+ confirmations in 24 hours
- **Indicates**: Possible wash trading or manipulation

## Moderator Alerts

When fraud is detected, moderators receive a modmail with:

```
🚨 **Fraud Detection Alert** 🚨

**User:** u/username
**Claimed Trades:** 15
**Alert Level:** 🔴 HIGH

**Indicators:**

[HIGH] New account (15 days old) claiming 15 trades
[MEDIUM] Low karma (50) claiming 15 trades

**Recommendation:** Review user's trading history and account activity.
```

## Testing

The fraud detection system includes comprehensive tests with mock data. Run tests with:

```bash
# Run all fraud detection tests
python -m pytest tests/test_fraud_detection.py -v

# Run specific test class
python -m pytest tests/test_fraud_detection.py::TestNewAccountDetection -v

# Run with coverage
python -m pytest tests/test_fraud_detection.py --cov=src.fraud_detection
```

### Test Scenarios

The test suite includes fixtures for:

- **New account with high trades** - Should be flagged HIGH
- **Old account with high trades** - Should NOT be flagged
- **New account with low trades** - Should NOT be flagged
- **Low karma with high trades** - Should be flagged MEDIUM
- **Low karma with low trades** - Should NOT be flagged
- **Rapid trading in 24h** - Should be flagged LOW
- **Multiple fraud indicators** - All should be reported

## Implementation Details

### Fraud Detection API

```python
from fraud_detection import FraudDetector, format_fraud_report

# Create detector
detector = FraudDetector()

# Check user for all fraud patterns
indicators = detector.check_all(redditor, claimed_trades)

# Format report for moderators
report = format_fraud_report(username, trade_count, indicators)
```

### Integration in Bot

The bot automatically checks users when they complete trades:

1. Trade confirmation is processed
2. If `FRAUD_DETECTION_ENABLED=true`:
   - Both users involved are checked
   - Indicators are collected
   - If found, modmail alert is sent to moderators
3. Trade proceeds normally regardless (no blocking)

### Customization

To adjust detection thresholds, modify constants in `src/fraud_detection.py`:

```python
NEW_ACCOUNT_DAYS = 30  # Account age threshold
NEW_ACCOUNT_MAX_TRADES = 5  # Max trades for new accounts
LOW_KARMA_THRESHOLD = 100  # Karma threshold
LOW_KARMA_MAX_TRADES = 5  # Max trades for low karma
RAPID_TRADE_HOURS = 24  # Window for rapid trades
RAPID_TRADE_THRESHOLD = 3  # Number of trades to flag
ACTIVITY_GAP_MONTHS = 6  # Long gap threshold
```

## Notes

- Fraud detection respects Reddit API rate limits
- Failed fraud checks don't block normal bot operation
- All alerts are logged for audit trails
- Users are NOT notified about fraud alerts
- Moderators must take manual action based on alerts
