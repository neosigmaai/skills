#!/bin/sh
set -eu
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt
python3 /tests/grade.py /app/price.txt 12.40 > /logs/verifier/grader-result.json
python3 /tests/write_reward.py --output /logs/verifier/reward.json --keys correct \
  < /logs/verifier/grader-result.json
