# Version-parsing mini benchmark (fixture source)

Each task in `tasks/<id>/` has `prompt.md`, `requirements.txt`, a starter
`solution.py`, `grade.py` and `reference/solution.py`.

The harness installs `requirements.txt` with Python 3.12, copies the starter
file to `/app/solution.py`, lets the model edit it, then runs
`python grade.py /app/solution.py`. `grade.py` prints one JSON object. The
reported metric is `resolved` (1 when every case passes, else 0). A solution
that cannot be imported is unresolved. A grader crash is an infrastructure
error and is not scored.
