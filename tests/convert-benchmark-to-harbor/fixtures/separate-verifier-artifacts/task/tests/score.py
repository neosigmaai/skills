"""Print {"field_accuracy": float} for a report and the source data."""

import csv
import json
import sys


def expected(data_path):
    with open(data_path, encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    return {"rows": len(rows), "total": sum(int(row["amount"]) for row in rows)}


def main(report_path, data_path):
    try:
        with open(report_path, encoding="utf-8") as stream:
            report = json.load(stream)
    except (FileNotFoundError, ValueError):
        report = {}
    if not isinstance(report, dict):
        report = {}
    truth = expected(data_path)
    correct = sum(report.get(key) == value for key, value in truth.items())
    return {"field_accuracy": correct / len(truth)}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2])))
