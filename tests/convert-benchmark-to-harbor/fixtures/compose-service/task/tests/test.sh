#!/bin/sh
set -eu
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt
python3 /tests/grade.py > /logs/verifier/grader-result.json
python3 /tests/write_reward.py --output /logs/verifier/reward.json --keys recorded \
  < /logs/verifier/grader-result.json
