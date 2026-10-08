#!/bin/sh
set -eu
python3 - <<'PY'
import json
import urllib.request

request = urllib.request.Request(
    "http://ledger:8080/entries",
    data=json.dumps({"invoice": "INV-9", "amount": 42}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
urllib.request.urlopen(request, timeout=10).read()
PY
