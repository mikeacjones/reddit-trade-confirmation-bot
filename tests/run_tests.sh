#!/bin/bash

# Run fraud detection tests
echo "Running fraud detection tests..."
python -m pytest tests/test_fraud_detection.py -v --tb=short

echo ""
echo "Test run complete!"
