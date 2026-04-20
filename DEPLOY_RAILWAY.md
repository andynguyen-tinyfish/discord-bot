# Deploy to Railway

## 1) Required environment variables
Set these in Railway service Variables:

- `DISCORD_BOT_TOKEN` (required)
- `GEMINI_API_KEY` (required)
- `DATABASE_PATH` (required)
- `ADMIN_PASSWORD` or `ADMIN_AUTH_SECRET` (required by app startup)

Recommended for Railway with persistent volume:

- `DATABASE_PATH=/data/app.db`

Optional:

- `ADMIN_SESSION_SECRET` (recommended if you run admin service)

## 2) Do you need a Railway Volume?
Yes, if you want SQLite data to persist across deploy/restarts.

Mount a Volume at `/data` and set:

- `DATABASE_PATH=/data/app.db`

Notes:

- Ingested knowledge chunks are stored in SQLite, so DB persistence is the key requirement.
- Local source docs (if you ingest from file paths) must exist inside the running container/volume path.

## 3) Start command
Default worker start command (bot + scheduler):

```bash
python -m app.main --mode serve
```

Admin dashboard start command (optional separate service):

```bash
python -m app.main --mode admin --host 0.0.0.0 --port $PORT
```

## 4) Healthcheck path
- Admin service healthcheck path: `/health`
- Worker-only bot service has no HTTP listener, so no HTTP healthcheck is required there.

## 5) Recommended Railway topology
Smallest safe option:

1. **One service for bot worker only** (`--mode serve`)

If you also need dashboard in Railway:

1. Bot worker service: `python -m app.main --mode serve`
2. Admin web service: `python -m app.main --mode admin --host 0.0.0.0 --port $PORT`

Both services should point to the same persistent `DATABASE_PATH` if they need shared state.

## 6) GitHub -> Railway deploy steps
1. Push this repo to GitHub.
2. In Railway, click **New Project** -> **Deploy from GitHub repo**.
3. Select this repository.
4. For bot worker service:
   - Use start command `python -m app.main --mode serve` (or keep `railway.toml` default).
   - Add Variables listed above.
   - Add Volume and mount at `/data`.
   - Set `DATABASE_PATH=/data/app.db`.
5. (Optional) Create a second Railway service from the same repo for admin:
   - Start command: `python -m app.main --mode admin --host 0.0.0.0 --port $PORT`
   - Same Variables/Volume/`DATABASE_PATH`.
   - Configure healthcheck path to `/health`.
6. Deploy.
7. Verify:
   - Worker logs show Discord connected and scheduler started.
   - Admin service `/health` returns `ok` (if deployed).
   - DB file exists in mounted volume path.
