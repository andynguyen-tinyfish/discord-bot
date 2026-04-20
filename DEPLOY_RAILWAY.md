# Deploy to Railway (Single Service)

## Required environment variables
Set these in Railway service Variables:

- `DISCORD_BOT_TOKEN` (required)
- `GEMINI_API_KEY` (required)
- `DATABASE_PATH` (required)
- `ADMIN_PASSWORD` or `ADMIN_AUTH_SECRET` (required)

Optional:

- `ADMIN_SESSION_SECRET` (recommended)

## Volume / persistence
Yes, use one Railway Volume so bot + admin share the same SQLite file.

Recommended:

- mount path: `/data`
- `DATABASE_PATH=/data/app.db`

Notes:

- Summaries, runtime settings, job logs, and ingested knowledge chunks are stored in this SQLite database.
- If you ingest from local files, those files must exist inside the deployed service/container path.

## Start command (single service)
Use the new combined mode:

```bash
python -m app.main --mode all --host 0.0.0.0 --port $PORT
```

This runs:

- Discord bot worker + scheduler in background thread
- Admin dashboard web server in foreground

## Healthcheck
Set Railway HTTP healthcheck path to:

- `/health`

## GitHub -> Railway deploy steps
1. Push this repo to GitHub.
2. In Railway: **New Project** -> **Deploy from GitHub repo**.
3. Select this repository.
4. Add one Volume mounted at `/data`.
5. Set variables listed above, including `DATABASE_PATH=/data/app.db`.
6. Set start command to:
   - `python -m app.main --mode all --host 0.0.0.0 --port $PORT`
7. Set healthcheck path to `/health`.
8. Deploy.
9. Verify:
   - logs show bot connected and scheduler running
   - `/health` returns `ok`
   - dashboard opens at `/admin`
