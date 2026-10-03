# Coolify Deployment Guide

## DB Migrations

This project uses a lightweight migration runner (`scripts/migrate.py`) to manage schema changes.

### How it works

- Migrations live in `docker/migrations/` as numbered SQL files (`001_init.sql`, `002_hourly_weather.sql`, …)
- On every deploy the `migrator` Docker Compose service runs `python scripts/migrate.py`
- Applied migrations are tracked in a `schema_migrations` table — already-applied files are skipped
- `collector`, `api`, and `retrain` services all declare `depends_on: migrator: condition: service_completed_successfully` so they only start after migrations succeed

### Coolify Pre-Deploy Command

In the Coolify service settings, set the **Pre-deploy Command** field to:

```
python scripts/migrate.py
```

This ensures migrations run against the live database **before** the new image goes live, matching the `migrator` service behaviour in local Compose.

**Field location:** Coolify → Service → _Advanced_ → **Pre-deploy Command**

### Adding a new migration

1. Create a new file in `docker/migrations/` with the next number prefix, e.g. `003_add_index.sql`
2. Write idempotent SQL (use `IF NOT EXISTS` where possible)
3. Commit and deploy — the runner will apply it automatically

### Manual run

```bash
DATABASE_URL=postgresql://user:pass@host/db python scripts/migrate.py
# or with a custom directory:
python scripts/migrate.py --migrations-dir path/to/migrations
```

## Hours delivery

The api re-reads `ml/data/opening_hours.generated.json` from `main` at runtime.
A data-only hours merge must not recreate containers.

### Environment

`HOURS_SYNC_URL` on the `api` service (set in `docker-compose.coolify.yml` with
a default pointing at `raw.githubusercontent.com/mekanics/badifrei/main/…`).
Local Compose leaves it unset, so the process serves the baked file only.

`/health` includes an `hours` block (`origin`, `content_sha256`, `last_result`).
The HTTP status stays 200 even when a fetch fails, so the Compose healthcheck
does not restart the api because GitHub is down.

### Watch Paths

Coolify → Configuration → General → Build → Watch Paths, in this order:

```
**
!ml/data/**
```

These filter Git-provider webhook deploys only. Manual and Deploy-Webhook
deploys still run. Set Watch Paths **after** production `/health.hours.last_result`
is `updated` or `unchanged`. Flipping them first would stop merged hours
reaching the site.

### Verification

1. After the runtime-fetch deploy: `curl https://badifrei.ch/health` →
   `hours.last_result` is `updated` or `unchanged` within 15 minutes.
2. Merge a data-only PR. Coolify Deployments must show no new run;
   `/health.hours.content_sha256` must change within ~20 minutes.

Rollback: revert the runtime-fetch PR (blanking `HOURS_SYNC_URL` in Coolify
does not disable the default). Clear Watch Paths to undo the path filter.

Verification date: _pending first production check._
