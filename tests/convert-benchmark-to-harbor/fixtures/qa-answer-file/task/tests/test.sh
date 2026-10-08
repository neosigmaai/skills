#!/bin/sh
# Run the source evaluator on the answer file; gold stays verifier-only.
set -eu
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt
gold=$(python3 -c 'import json; print(json.load(open("/tests/gold.json"))["answer"])')
python3 /tests/eval.py /app/answer.txt "$gold" > /logs/verifier/grader-result.json
python3 /tests/write_reward.py --output /logs/verifier/reward.json --keys exact_match \
  < /logs/verifier/grader-result.json
