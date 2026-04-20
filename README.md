# Discord QA Reminder Bot

Daily workflow:
- Night job: collect yesterday messages from all source channels -> filter -> summarize -> save to SQLite.
- Morning job: load yesterday summary -> post to reminder channel -> mark posted.
- Mention Q&A (serve mode): when bot is mentioned, it answers using project-specific ingested knowledge.

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create `setting.env` (or `.env`) from `.env.example` and fill values.
4. Ensure the bot has Discord permissions:
   - View Channel
   - Read Message History
   - Send Messages

## Configuration Model

Secrets stay in environment variables:
- `DISCORD_BOT_TOKEN`
- `GEMINI_API_KEY`
- `DATABASE_PATH`
- `ADMIN_PASSWORD` (or `ADMIN_AUTH_SECRET`)

Notes:
- `GEMINI_API_KEY` is used for nightly summarization.
- `OPENAI_API_KEY` is optional legacy and no longer used for summarization.

Operational settings are stored in SQLite (`runtime_settings` table) and can be edited from the admin dashboard:
- source/reminder channel IDs
- per-project channel routing JSON
- per-project knowledge source JSON (channels/files)
- timezone
- nightly/morning schedule
- dry-run and dry-run channel
- allowed user/role filters

### Per-project routing

Use `Project Routing JSON` in dashboard settings for flexible channel mapping.

Example:
```json
[
  {
    "key": "alpha",
    "name": "Alpha",
    "source_channel_ids": [111111111111111111],
    "post_channel_id": 222222222222222222,
    "mention_role_id": 888888888888888888,
    "knowledge_channel_ids": [211111111111111111, 211111111111111112],
    "knowledge_file_paths": []
  },
  {
    "key": "beta",
    "name": "Beta",
    "source_channel_ids": [333333333333333333, 444444444444444444],
    "post_channel_id": 555555555555555555,
    "knowledge_channel_ids": [355555555555555551],
    "knowledge_file_paths": ["/Users/me/docs/beta_guideline.md", "/Users/me/docs/beta_reference.pdf"]
  },
  {
    "key": "temp-gamma",
    "name": "Temp Gamma",
    "source_channel_ids": [666666666666666666],
    "fallback_post_channel_id": 777777777777777777,
    "knowledge_channel_ids": [],
    "knowledge_file_paths": []
  }
]
```

Notes:
- `source_channel_ids` can contain multiple channels per project.
- `post_channel_id` is the dedicated post target for that project.
- `fallback_post_channel_id` is optional per-project fallback.
- `mention_role_id` is optional and prepends a role ping before that project message.
- `knowledge_channel_ids` are project-specific guideline/reference channels for ingestion.
- `knowledge_file_paths` are local docs (`.md`, `.txt`, `.pdf`) for ingestion.
- `Designated Role ID` in dashboard is a global fallback ping if a project does not define `mention_role_id`.
- `Shared Knowledge Channel IDs` and `Shared Knowledge File Paths` in dashboard are common sources ingested for every project.
- Global fallback order for posting is:
  `post_channel_id` -> `fallback_post_channel_id` -> `Shared Post Channel ID` -> legacy `Reminder Channel ID`.
- If `Project Routing JSON` is empty, bot keeps legacy single-route behavior using `Source Channel IDs` + `Reminder Channel ID`.

Optional env vars such as `SOURCE_CHANNEL_IDS`, schedule values, and whitelist values are used only as first-run bootstrap defaults for `runtime_settings`.

## Run Locally

Continuous scheduler mode:
```bash
python -m app.main --mode serve
```

One-off jobs:
```bash
python -m app.main --mode nightly
python -m app.main --mode morning
python -m app.main --mode ingest-knowledge
```

Knowledge ingestion (single project):
```bash
python -m app.main --mode ingest-knowledge --project-key alpha
```

Custom date rerun (configured local timezone date):
```bash
python -m app.main --mode nightly --date 2026-04-14
python -m app.main --mode morning --date 2026-04-14
```

Admin dashboard:
```bash
python -m app.main --mode admin --host 127.0.0.1 --port 8080
```
Then open `http://127.0.0.1:8080/admin` and sign in with `ADMIN_PASSWORD`.

## Testing

1. Start admin mode and save valid runtime settings in the dashboard.
2. Run nightly once (`--mode nightly`) and confirm a `summaries` row is created.
3. Run morning once (`--mode morning`) and confirm message post behavior:
   - normal mode posts to reminder channel and marks row `posted=1`
   - dry-run mode posts to dry-run channel or prints preview to stdout
4. Run morning again for same date and confirm duplicate post is skipped.
5. In dashboard, trigger manual nightly/morning runs and confirm they start in background.
6. If project routing is configured, confirm one summary row per `(date, project)` and per-project morning posts.
7. Run knowledge ingestion and check `job_logs` entries with `job=knowledge`.
8. Mention the bot in a project channel and confirm reply uses project knowledge context.

## Common Issues

- Missing admin auth env:
  set `ADMIN_PASSWORD` (or `ADMIN_AUTH_SECRET`).
- Dashboard login fails:
  verify the password exactly matches `ADMIN_PASSWORD`.
- No messages collected:
  check source channel IDs in dashboard and Discord permissions.
- Morning post skipped:
  summary may not exist for target date, or it is already marked posted.
- `--date` rejected:
  use exact format `YYYY-MM-DD` and pair it with `--mode nightly` or `--mode morning`.
- Invalid timezone error:
  use a valid IANA timezone string (for example `Asia/Bangkok`).
