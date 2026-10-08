#!/bin/sh
# Grade the collected report in the separate verifier container.
set -eu
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt
python3 /tests/score.py /app/report.json /tests/data.csv \
  > /logs/verifier/grader-result.json
python3 /tests/write_reward.py --output /logs/verifier/reward.json \
  --keys field_accuracy < /logs/verifier/grader-result.json
