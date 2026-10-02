# Codex Implementation Plan: PostgreSQL Migration

## Goal

Migrate Listner's shared persistence from local SQLite to PostgreSQL so the Vercel control API and the persistent Telethon worker can safely share state.

Do **not** put PostgreSQL credentials, Telegram credentials, session strings, or other secrets in the repository.

## Why

The current Store is SQLite-backed via DATABASE_PATH. Vercel serverless instances do not provide a shared persistent SQLite filesystem, so the current Vercel API cannot safely share the worker's SQLite database.

Target architecture:

Vercel control API -> PostgreSQL <- persistent Telethon worker

SQLite may remain as an optional local-development backend only if that can be done cleanly without weakening the PostgreSQL production path.

## Required implementation

### 1. Database adapter

Update `listner/db.py`:

- Preserve the existing `Store` public methods used by the API and worker.
- Add PostgreSQL support using a synchronous driver such as `psycopg[binary]`.
- Prefer `DATABASE_URL` for PostgreSQL.
- Keep connection handling safe for short-lived Vercel requests and long-running worker loops.
- Initialize schema idempotently.
- Use PostgreSQL-compatible SQL/types instead of SQLite-only syntax.
- Do not use SQLite-specific `PRAGMA`, `INSERT OR IGNORE`, `AUTOINCREMENT`, or SQLite connection APIs in the PostgreSQL path.

Tables/semantics to preserve:

- watched_contacts
- known_groups
- call_monitors
- call_presence
- user_statuses
- alerts

### 2. Duplicate monitor protection — critical

Preserve and strengthen the database-level lease invariant for each active call:

- Unique key: `(group_id, call_id)`.
- `lease_owner` and `lease_until`.
- Acquisition must be atomic.
- A second worker must NOT acquire a valid existing lease.
- An expired lease may be reclaimed.
- The same owner may renew its lease.
- Renewal must fail after ownership is lost.
- Worker heartbeat/renewal must happen before each participant poll.
- Database remains the source of truth; in-memory task tracking is optional only.
- When a call ends, stale monitor state must not cause a future call to be mistaken for the same active call.

Use PostgreSQL `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE` or an equivalent atomic transaction.

### 3. Presence and alerts

Preserve current behavior:

- A watched user joining an active call creates exactly one joined transition/alert.
- Continued polling while present creates no duplicate join alerts.
- Leaving creates exactly one left transition/alert and includes duration.
- A user who is absent initially must not generate a false leave alert.
- Pending alerts survive worker/API restarts until delivered.
- Online/offline status transitions remain durable.

Avoid duplicate alert delivery as far as practical. If delivery claiming/locking is changed, make the behavior safe for multiple worker instances.

### 4. Configuration

Update `listner/config.py`:

- Add/use `DATABASE_URL`.
- Make PostgreSQL the production/default backend.
- Keep `DATABASE_PATH` only if an explicit local SQLite fallback is implemented.
- Do not log database URLs or credentials.
- Preserve existing polling and lease settings.

Update `.env.example` with placeholders only.

Recommended production variables:

- DATABASE_URL
- TELEGRAM_API_ID
- TELEGRAM_API_HASH
- TELEGRAM_SESSION
- BOT_TOKEN
- BOT_CHAT_ID
- CONTROL_SECRET
- POLL_SECONDS
- LEASE_SECONDS

### 5. API and worker

Update `api/index.py` and `listner/worker.py` only as needed so both instantiate the same PostgreSQL-backed Store.

The Vercel API must no longer assume that `/data/listner.sqlite3` is shared with the worker.

The worker must continue using persistent storage only for the Telethon session file; database state belongs in PostgreSQL.

### 6. Dependencies/deployment

Update `requirements.txt` to include the PostgreSQL driver.

Update `docker-compose.yml` and README instructions so the worker and bot-control use `DATABASE_URL`.

Do not add a PostgreSQL container unless it is useful for local development; production should use an external managed PostgreSQL database.

### 7. Tests

Add tests for the Store/lease semantics. At minimum verify:

1. schema initialization is idempotent;
2. watching the same user twice does not duplicate it;
3. two different owners cannot simultaneously acquire the same unexpired `(group_id, call_id)` lease;
4. an expired lease can be reclaimed;
5. the current owner can renew;
6. a non-owner cannot renew;
7. join is emitted once;
8. repeated presence emits no duplicate join;
9. leave emits once with duration;
10. pending alerts remain pending until marked delivered.

Prefer a PostgreSQL test database when practical. If CI cannot provide PostgreSQL, keep a focused SQL/logic test layer and document the integration-test requirement.

### 8. Documentation

Update README with:

- PostgreSQL as the production shared database.
- Example `DATABASE_URL` format without a real credential.
- Neon/Supabase/etc. may be used as the managed PostgreSQL provider; do not hard-code a provider.
- Vercel hosts the control API.
- The Telethon worker remains on an always-on/persistent runtime.
- Never commit `.env`, Telegram API hashes, bot tokens, Telethon session strings, or database credentials.

## Acceptance criteria

- `python -m compileall -q listner api` succeeds.
- Existing API endpoints continue to work.
- Worker starts with PostgreSQL configuration.
- Two worker processes scanning the same active call cannot create two valid monitor owners.
- A lease is reclaimable after expiry.
- Join/leave transitions and alert durability continue to work.
- No production code requires a shared SQLite file.
- No secrets are committed.
- README and environment examples match the new configuration.

## Files expected to change

Likely:

- `listner/db.py`
- `listner/config.py`
- `listner/worker.py` (only where required)
- `api/index.py` (only where required)
- `requirements.txt`
- `.env.example`
- `docker-compose.yml`
- `README.md`
- tests under `tests/`

Keep the PR focused on the PostgreSQL migration. Do not refactor unrelated Telegram behavior.

## Codex instructions

Implement this plan on the PR branch. Inspect the current repository before editing. Preserve existing behavior unless this plan explicitly changes it. Run the available tests/static checks before finishing, report any test limitations, and keep secrets out of commits.
