# Tasker

A small tickets app. Requests enter through tasker/api/router.py and travel down the layers:

1. migrations/*.sql: the schema, one script per change, applied in file name order by tasker/db.py
2. tasker/models: one dataclass per table
3. tasker/repository: SQL for one table
4. tasker/service: validation and workflows
5. tasker/api: request parsing and JSON shape; the contract is documented in openapi.json
6. tasker/view: text table and CSV rendering, used by tasker/cli.py

Existing migrations are never edited: a schema change is a new script.
