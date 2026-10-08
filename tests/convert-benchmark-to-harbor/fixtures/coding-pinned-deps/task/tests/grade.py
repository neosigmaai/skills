"""Grade one solution file and print {"resolved", "passed", "total"} as JSON."""

import importlib.util
import json
import sys

CASES = [("1.0", False), ("2.3.4", False), ("foo", True), ("release-candidate", True)]


def load(path):
    spec = importlib.util.spec_from_file_location("solution", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(path):
    try:
        solution = load(path)
    except Exception:
        return {"resolved": 0, "passed": 0, "total": len(CASES)}
    passed = 0
    for version, expected in CASES:
        try:
            passed += solution.is_legacy(version) is expected
        except Exception:
            pass
    return {"resolved": int(passed == len(CASES)), "passed": passed, "total": len(CASES)}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1])))
