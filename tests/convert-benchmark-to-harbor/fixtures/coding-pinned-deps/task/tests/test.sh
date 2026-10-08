#!/bin/sh
# Run the source grader; write a reward only when it produced a result.
set -eu
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt
python3 /tests/grade.py /app/solution.py > /logs/verifier/grader-result.json
python3 /tests/write_reward.py --output /logs/verifier/reward.json --keys resolved \
  < /logs/verifier/grader-result.json
