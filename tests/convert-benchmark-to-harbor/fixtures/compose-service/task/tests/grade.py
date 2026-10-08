"""Print {"recorded": 0 | 1} from the ledger service state."""

import json
import urllib.request

with urllib.request.urlopen("http://ledger:8080/entries", timeout=10) as response:
    entries = json.load(response)
recorded = any(
    entry.get("invoice") == "INV-9" and entry.get("amount") == 42 for entry in entries
)
print(json.dumps({"recorded": int(recorded)}))
