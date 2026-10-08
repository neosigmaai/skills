"""Print the named metrics for one turn of the notes benchmark."""

import json
import sys

PATH = "/app/notes.md"


def main(turn):
    try:
        with open(PATH, encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except FileNotFoundError:
        lines = None
    exists = int(lines is not None)
    if turn == "create":
        return {"file_exists": exists, "header_ok": int(bool(lines) and lines[0] == "# Notes")}
    return {"file_exists": exists, "item_ok": int(bool(lines) and "- buy milk" in lines)}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1])))
