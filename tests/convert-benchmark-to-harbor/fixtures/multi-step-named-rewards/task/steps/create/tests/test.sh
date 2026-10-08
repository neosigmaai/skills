#!/bin/sh
set -eu
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt
python3 /tests/check.py create > /logs/verifier/grader-result.json
python3 /tests/write_reward.py --output /logs/verifier/reward.json \
  --keys file_exists,header_ok < /logs/verifier/grader-result.json
