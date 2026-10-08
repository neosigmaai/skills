"""Print {"correct": 0 | 1} for the price file and the expected price."""

import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        answer = stream.read().strip()
except FileNotFoundError:
    answer = ""
print(json.dumps({"correct": int(answer == sys.argv[2])}))
