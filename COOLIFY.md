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

**Field location:** Coolify → Service → *Advanced* → **Pre-deploy Command**

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
