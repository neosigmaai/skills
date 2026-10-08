#!/usr/bin/env python3
"""Write a Harbor reward file only when the grader produced finite numeric rewards.

Copy this file into a converted task's tests/ directory when the verifier image
already has Python 3. Read the source grader's JSON result on stdin:

    python3 /tests/write_reward.py --output /logs/verifier/reward.json \
        --keys accuracy,f1 < grader-result.json

reward.json receives an object of the selected names and values. reward.txt
receives one number and needs exactly one selected value. Booleans, strings,
NaN, infinity, missing names and empty input are rejected with exit status 2
and no reward file, so Harbor reports a missing reward instead of a score.
The file is written to a temporary name and then renamed into place.
"""

import argparse
import json
import math
import os
import sys
import tempfile

INVALID_REWARD_EXIT_CODE = 2


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--keys",
        help="Comma-separated source reward names to copy; default is every key.",
    )
    return parser.parse_args()


def _reject_constant(value):
    raise ValueError("non-finite number: " + value)


def rewards_from(document, keys):
    """Return the selected rewards, or raise ValueError when any is invalid."""
    if not isinstance(document, dict) or not document:
        raise ValueError("grader result must be a non-empty JSON object")
    names = keys if keys else sorted(document)
    rewards = {}
    for name in names:
        if name not in document:
            raise ValueError("grader result has no reward named " + repr(name))
        value = document[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("reward " + repr(name) + " is not a number")
        if not math.isfinite(value):
            raise ValueError("reward " + repr(name) + " is not finite")
        rewards[name] = value
    return rewards


def encode(rewards, output):
    """Return the reward file content Harbor expects for this file name."""
    if output.endswith(".txt"):
        if len(rewards) != 1:
            raise ValueError("reward.txt holds exactly one value; use reward.json")
        return json.dumps(next(iter(rewards.values()))) + "\n"
    if output.endswith(".json"):
        return json.dumps(rewards, sort_keys=True) + "\n"
    raise ValueError("output must be reward.txt or reward.json")


def write_atomic(path, content):
    """Make the reward file appear complete or not at all."""
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".reward-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def main():
    """Validate stdin and write the reward file, or exit 2 without writing."""
    arguments = _arguments()
    keys = [key for key in (arguments.keys or "").split(",") if key]
    try:
        document = json.loads(sys.stdin.read(), parse_constant=_reject_constant)
        content = encode(rewards_from(document, keys), arguments.output)
    except ValueError as error:
        sys.stderr.write("invalid grader result: " + str(error) + "\n")
        return INVALID_REWARD_EXIT_CODE
    write_atomic(arguments.output, content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
