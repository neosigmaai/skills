# Report summary mini benchmark (fixture source)

The model reads `/app/data.csv` and writes `/app/report.json` with keys `rows`
(number of data rows) and `total` (sum of the `amount` column). The harness
copies only `report.json` out of the agent container and grades it in a
separate grader container that has its own copy of `data.csv`. `score.py`
prints `{"field_accuracy": <fraction of the two fields that are correct>}`,
so the score is 0, 0.5 or 1. A missing or unreadable report scores 0.
