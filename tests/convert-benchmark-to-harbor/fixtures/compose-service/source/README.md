# Ledger service mini benchmark (fixture source)

`docker-compose.yaml` starts the agent container `main` and a `ledger` HTTP
service on port 8080 (healthcheck: `GET /health`). The model must record a
payment of 42 for invoice INV-9 by sending
`POST http://ledger:8080/entries` with JSON `{"invoice": "INV-9", "amount": 42}`.
`grade.py` reads `GET http://ledger:8080/entries` after the run and prints
`{"recorded": 0 | 1}`. The task has no external network access.
