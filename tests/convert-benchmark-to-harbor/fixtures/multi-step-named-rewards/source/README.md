# Two-turn notes mini benchmark (fixture source)

Turn 1 ("create"): create `/app/notes.md` whose first line is `# Notes`.
Turn 2 ("extend"): append a line `- buy milk`.

After each turn `check.py <turn>` prints named metrics:
- create: `{"file_exists": 0|1, "header_ok": 0|1}`
- extend: `{"file_exists": 0|1, "item_ok": 0|1}`

If turn 1 does not reach `file_exists = 1`, the episode stops. The episode
result is the last turn's metrics (missing turns score nothing).
