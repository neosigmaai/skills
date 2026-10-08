"""Print {"exact_match": float} for one prediction file and one gold answer."""

import json
import string
import sys


def normalize(text):
    text = text.strip().lower()
    return "".join(character for character in text if character not in string.punctuation)


def main(prediction_path, gold):
    try:
        with open(prediction_path, encoding="utf-8") as stream:
            prediction = stream.read()
    except FileNotFoundError:
        prediction = ""
    return {"exact_match": float(normalize(prediction) == normalize(gold))}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2])))
