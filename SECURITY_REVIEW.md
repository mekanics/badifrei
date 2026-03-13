# Security Review — badi-predictor

**Reviewer:** Senior Security Engineer (automated review)  
**Date:** 2026-03-13  
**Branch:** `main`  
**Scope:** Full codebase — API, ML pipeline, Docker/compose, templates, dependencies

---

## Executive Summary

**Overall Risk Level: MEDIUM**

The badi-predictor project is a public read-only occupancy-prediction service with a reasonably defensive posture. There is no authentication surface to protect (intentional), SQL injection risk is well-managed through parameterized queries throughout, and Jinja2 autoescaping is active. The most pressing concerns are weak hardcoded default credentials in source code, all containers running as root, a missing Content-Security-Policy nonce (relying on `unsafe-inline`), and a Chart.js CDN inclusion without Subresource Integrity.

There are no critical findings. No credentials are committed to git history.

---

## Findings

### F-01 — HIGH | Hardcoded Default Credentials in Source Code

**Files:**  
- `collector/config.py` lines 22, 25  
- `ml/weather.py` line 71  
- `tests/conftest.py` line 10  
- `tests/integration/test_docker_compose.py` line 18

**Description:**  
The database password `badi:badi` is hardcoded as the fallback in two production modules. If `DATABASE_URL` is absent from the environment (misconfigured deploy, environment variable injection failure, or a local run without the `.env` loaded), the application silently connects with the weak default credentials instead of failing fast and alerting operators.

```python
# collector/config.py — line 22
database_url=os.getenv("DATABASE_URL", "postgresql://badi:badi@localhost:5432/badi"),

# ml/weather.py — line 71
url = os.getenv("DATABASE_URL", "postgresql://badi:badi@localhost:5432/badi")
```

The `.env` file (present locally, correctly gitignored) also uses `badi:badi`. If a developer promotes this to a staging or production environment without changing credentials, the database is protected only by network access controls.

**Recommendation:**  
1. Remove the hardcoded fallback entirely. Replace with:  
   ```python
   url = os.environ["DATABASE_URL"]  # raises KeyError if unset — fail fast
   ```  
2. Add a startup assertion in `lifespan()` that crashes the API immediately (not gracefully) if `DATABASE_URL` is unset.  
3. Change the `.env` default to a randomised value or document that `badi:badi` must never reach production.  
4. Use a strong, randomly-generated password (≥ 32 chars) for all environments.

---

### F-02 — HIGH | All Docker Containers Run as Root

**Files:**  
- `api/Dockerfile`  
- `collector/Dockerfile`  
- `ml/Dockerfile`

**Description:**  
None of the three Dockerfiles create or switch to a non-root user. The Python `slim` base image defaults to UID 0. A container escape or RCE vulnerability (e.g. in aiohttp, uvicorn, or a malicious model file) would give an attacker full root access inside the container and, depending on host configuration, potential breakout leverage.

**Recommendation:**  
Add to each Dockerfile before the `CMD`:
```dockerfile
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app
USER appuser
```
Ensure the volume mounts (`model_artifacts`, template/static mounts) are also owned by `appuser` or world-readable as needed.

---

### F-03 — MEDIUM | CORS Wildcard Origin

**File:** `api/main.py` lines ~140–146

**Description:**  
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
)
```
Any website can make cross-origin `GET` requests to the API. Authenticated cookies are excluded (`allow_credentials` is not set, so cookies are not sent cross-origin), limiting the direct risk. However, for a predictable-domain service, this is wider than necessary and makes future API hardening more difficult.

**Recommendation:**  
Restrict to the known origin:
```python
allow_origins=["https://badifrei.ch"],
```
For development, override via an env var (`CORS_ORIGINS=*` locally).

---

### F-04 — MEDIUM | CSP Allows `unsafe-inline` Scripts

**File:** `api/main.py` — `SecurityHeadersMiddleware` (~lines 155–165)

**Description:**  
```python
"script-src 'self' cdn.jsdelivr.net 'unsafe-inline'; "
"style-src 'self' fonts.googleapis.com 'unsafe-inline'; "
```
`'unsafe-inline'` for `script-src` defeats the primary XSS protection purpose of CSP. Any injected inline script (e.g. via a Jinja2 context-data bug, a future template change, or a supply-chain attack on the CDN) would execute without restriction.

The root cause is Chart.js being loaded from CDN while inline `<script>` blocks are used in `pool.html`. This is an architectural tension (SSR data baked into inline JS).

**Recommendation:**  
Option A (nonce-based — preferred): Generate a per-request nonce, set it in the CSP header, and apply `nonce="{{ csp_nonce }}"` to all inline script tags. Removes `'unsafe-inline'`.  
Option B (move data to `data-*` attributes): Replace inline JS data (`SSR_PREDICTIONS`, `POOL_UID`) with `data-*` attributes on a DOM element, and read them from an external `.js` file. External files work with `'self'` in CSP.

Also tighten the CDN whitelist:
```
script-src 'self' cdn.jsdelivr.net
```
→ lock to the specific file path and add SRI (see F-05).

---

### F-05 — MEDIUM | Chart.js CDN Without Subresource Integrity (SRI)

**File:** `api/templates/pool.html` line ~(end of file)

**Description:**  
```html
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
```
No `integrity` attribute. If jsDelivr is compromised or serves a tampered file (supply-chain attack), the script executes without any browser-level verification. This is compounded by the CSP allowing all of `cdn.jsdelivr.net` rather than a specific resource.

**Recommendation:**  
Pin to a specific version and add SRI hash:
```html
<script
  src="https://cdn.jsdelivr.net/npm/chart.js@4.4.9/dist/chart.umd.min.js"
  integrity="sha384-<hash>"
  crossorigin="anonymous"></script>
```
Generate the hash with: `curl -s https://... | openssl dgst -sha384 -binary | openssl base64`  
Update CSP `script-src` to match the specific path (most browsers allow path-constrained CDN whitelisting as of 2024).

---

### F-06 — MEDIUM | Development `--reload` Flag in docker-compose.yml

**File:** `docker-compose.yml` lines ~54–58

**Description:**  
```yaml
command: ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```
`--reload` enables the file-watcher (inotify) and disables uvicorn's worker isolation. If `docker-compose.yml` is used on a staging server (common accident), the reload flag creates a larger attack surface (arbitrary file reads via hot-reload path traversal edge cases) and significantly increases CPU overhead.

**Recommendation:**  
Remove `--reload` from `docker-compose.yml`. Use a separate `docker-compose.override.yml` (gitignored) for local development with the reload flag. The Coolify config correctly omits it.

---

### F-07 — MEDIUM | Database Port Exposed to Host Network in Dev Compose

**File:** `docker-compose.yml` lines ~10–11

**Description:**  
```yaml
ports:
  - "5432:5432"
```
PostgreSQL is bound to `0.0.0.0:5432` on the host. If the developer machine is on a shared network (office, conference wifi, VPN), the database is reachable by other machines using the weak `badi:badi` credentials (F-01). The test database on port 5433 is similarly exposed.

**Recommendation:**  
Restrict to loopback: `"127.0.0.1:5432:5432"`. Or remove the port mapping entirely and use the Docker network name (`db:5432`) for all inter-service communication (already done correctly for service URLs).

---

### F-08 — LOW | `today_predictions_json | safe` Bypasses Jinja2 Autoescaping

**File:** `api/templates/pool.html` line 329

**Description:**  
```javascript
const SSR_PREDICTIONS = {{ today_predictions_json | safe }};
```
`| safe` tells Jinja2 to skip HTML escaping. The variable is `json.dumps(list_of_floats)` — currently safe — but this is a footgun: if the data pipeline is ever compromised and non-float values reach this path (e.g. via a database write), they would render as raw JavaScript content.

For comparison, `pool.uid` is correctly handled on line 327:
```javascript
const POOL_UID = {{ pool.uid | tojson }};
```
`| tojson` is the correct pattern because it produces a properly JSON-encoded string and escapes characters that could break out of a `<script>` context (e.g. `</script>`).

**Recommendation:**  
Replace:
```python
"today_predictions_json": json.dumps(today_predictions)
```
with:
```python
# In template context:
"today_predictions": today_predictions
```
And in the template:
```javascript
const SSR_PREDICTIONS = {{ today_predictions | tojson }};
```
`tojson` applies both JSON encoding and `</script>`-safe escaping. Remove `| safe`.

---

### F-09 — LOW | Dev Dependencies Installed in ML Production Container

**File:** `ml/Dockerfile` line 3

**Description:**  
```dockerfile
RUN pip install uv && uv pip install --system -e ".[dev]"
```
The `[dev]` extras install pytest, ruff, black, and pytest-cov into the production retrain container. These packages are unnecessary at runtime, increase the image size by ~50–100 MB, and expand the attack surface (e.g. pytest's fixture machinery, coverage instrumentation).

**Recommendation:**  
```dockerfile
RUN pip install uv && uv pip install --system -e "."
```

---

### F-10 — LOW | Unpinned Docker Image Tags

**Files:** `docker-compose.yml`, `docker-compose.coolify.yml`

**Description:**  
```yaml
image: timescale/timescaledb:latest-pg16
```
`latest-pg16` is not a floating `latest` tag, but it still resolves to different digests over time. An image update could silently introduce a regression or (in a supply-chain scenario) a malicious change without any diff in the compose file.

**Recommendation:**  
Pin to a digest:
```yaml
image: timescale/timescaledb:2.17.2-pg16@sha256:<digest>
```
Or use a specific version tag and validate with digest in CI: `docker pull timescale/timescaledb:2.17.2-pg16@sha256:...`

---

### F-11 — LOW | `pool_uid` URL Path Parameter Has No Length/Character Constraint

**File:** `api/main.py` lines ~214, ~243

**Description:**  
```python
@app.get("/bad/{pool_uid}", ...)
async def pool_detail(request: Request, pool_uid: str):
```
FastAPI passes `pool_uid` as a raw string with no max-length or character-set constraint. The application safely validates it against the pool list (returning 404 for unknowns) and all DB queries are parameterized — so there is no SQL injection risk. However, arbitrarily long strings (e.g. 100 KB) are accepted and compared against every pool in memory before returning 404.

**Recommendation:**  
Add a regex pattern constraint to limit the path parameter:
```python
from fastapi import Path
async def pool_detail(request: Request, pool_uid: str = Path(..., pattern=r'^[a-z0-9_-]{1,64}$')):
```

---

### F-12 — LOW | No Input Length Limit on `date` and `dt_str` Query Parameters

**File:** `api/main.py` lines ~247, ~278

**Description:**  
The `date` parameter in `/predict/range` is parsed with `dateutil.parser.parse`, which is deliberately fuzzy and accepts many undocumented formats (natural language dates, locale-specific strings). While the result is only used to build a safe `datetime` object, very long or pathological inputs could cause excessive parsing work.

**Recommendation:**  
For `/predict/range?date=`: enforce ISO format validation before dateutil:
```python
if len(date) > 20:
    raise HTTPException(status_code=422, detail="Invalid date format")
```
Or constrain with a Query annotation: `date: str = Query(..., max_length=20, pattern=r'^\d{4}-\d{2}-\d{2}$')`.

For `/predict?dt_str=`: the current code already switches to `datetime.fromisoformat()` after the dateutil import at the top of the file — verify `dt_str` uses that path, not the fuzzy parser.

---

### F-13 — INFO | No Authentication or Authorization

**File:** `api/main.py` — all endpoints

**Description:**  
All API endpoints are fully public with no API key, bearer token, or IP allowlist. This is likely intentional for a public prediction service. However, combined with the absent application-layer rate limiting in the dev compose, there is no throttle on automated scraping of the `/predict/range` endpoint (which triggers weather API calls and DB queries per request).

Rate limiting exists only via Traefik in the Coolify production config (60 req/min, burst 30).

**Recommendation:**  
For the current use case (public read-only), this is acceptable. Document the decision explicitly. If any write/admin endpoint is added in the future, add authentication before merging.

---

### F-14 — INFO | `WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR` Env Var Can Destroy Production Data

**File:** `ml/weather.py` lines ~176–183

**Description:**  
```python
if os.getenv("WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR", "").lower() == "true":
    await conn.execute(_TRUNCATE_SQL)  # TRUNCATE TABLE hourly_weather
```
This is documented as test-only. If accidentally set in production (e.g. via copy-paste from a test script), it silently truncates the weather cache table.

**Recommendation:**  
Add a guard: also check for an `APP_ENV=test` variable, or rename to `_TEST_WEATHER_DB_TRUNCATE=true` (the `_TEST_` prefix signals test-only by convention). Add a log line at `ERROR` level (not `INFO`) when the truncation executes.

---

## Positive Findings

These things are done correctly and should be preserved:

- ✅ **All asyncpg queries use parameterized placeholders** (`$1`, `$2`, etc.) throughout `api/main.py`, `ml/data_loader.py`, `api/predictor.py` — zero SQL injection surface found.
- ✅ **`_parse_interval()` in `ml/data_loader.py`** validates the bucket-interval string with a strict regex and converts to `timedelta` before passing to asyncpg — no string interpolation into SQL.
- ✅ **OpenAPI/Swagger UI disabled in production** (`docs_url=None`, `redoc_url=None`, `openapi_url=None`).
- ✅ **`SecurityHeadersMiddleware`** correctly sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`, `Permissions-Policy`, and a per-page CSP.
- ✅ **Error details not leaked to clients** — `api/main.py` logs exceptions server-side and returns null/empty responses to clients (no stack traces in HTTP responses).
- ✅ **`.env` correctly gitignored** — confirmed git history does not contain `.env` credentials.
- ✅ **`.dockerignore` excludes `.env`** — the `.env` file is excluded from all Docker build contexts, so credentials cannot be baked into images.
- ✅ **Jinja2 autoescaping active** — `FastAPI.Jinja2Templates` enables HTML autoescaping for `.html` files by default; `pool.uid`, `pool.name`, etc. are all safely escaped in templates.
- ✅ **`pool.uid | tojson`** in `pool.html` line 327 correctly JSON-encodes and `</script>`-escapes the pool UID for inline JavaScript context.
- ✅ **`pool_uid` validated against known pool list** before any DB query — unknown UIDs return 404 before any database interaction.
- ✅ **Weather API calls use only hardcoded URLs** — `FORECAST_URL` and `ARCHIVE_URL` in `ml/weather.py` are constants; city coordinates come from a hardcoded dict — no SSRF risk from user input.
- ✅ **Memory limits on all containers** in `docker-compose.coolify.yml`.
- ✅ **Traefik rate limiting configured** in Coolify deployment (60 req/min average, burst 30).
- ✅ **DB connection pool properly closed** on shutdown in `lifespan()`.
- ✅ **psycopg2 lag queries use `%s` placeholders** (parameterized) in `api/predictor.py` — no injection risk.
- ✅ **`uv.lock` present** in `ml/Dockerfile` — deterministic dependency resolution.

---

## Priority Fix List

These are the five most impactful changes, in order:

### 1. Remove hardcoded `badi:badi` credentials from source code (F-01)
**Why first:** Credential exposure in source code ships in every Docker image, git clone, and CI log. Even with `.env` gitignored, the fallback means a misconfigured production deploy would connect to the database with a trivially guessable password. One hour of work, zero regressions.  
**Files:** `collector/config.py` (lines 22, 25), `ml/weather.py` (line 71). Remove the fallback string; fail hard if `DATABASE_URL` is unset.

### 2. Add non-root users to all three Dockerfiles (F-02)
**Why second:** Defense-in-depth for any future RCE vulnerability. Containers running as root are a well-known risk that industry best practice mandates eliminating. Four lines of Dockerfile per service.  
**Files:** `api/Dockerfile`, `collector/Dockerfile`, `ml/Dockerfile`.

### 3. Add Subresource Integrity to the Chart.js CDN script tag (F-05)
**Why third:** Supply-chain attacks on npm CDNs are a real, documented threat (typosquatting, CDN compromise). Adding `integrity="sha384-..."` to one `<script>` tag is a five-minute fix with strong security payoff.  
**File:** `api/templates/pool.html`.

### 4. Replace `today_predictions_json | safe` with `today_predictions | tojson` (F-08)
**Why fourth:** Eliminates a latent XSS footgun with a one-line template change and a minor Python-side refactor. Low effort, removes a dangerous code pattern before it causes an incident.  
**Files:** `api/main.py` (template context), `api/templates/pool.html` (line 329).

### 5. Restrict database port binding and remove `--reload` in docker-compose.yml (F-06, F-07)
**Why fifth:** Both are single-line fixes that prevent common developer-environment mistakes from becoming production incidents. Changing `"5432:5432"` to `"127.0.0.1:5432:5432"` and removing `--reload` from the API service command takes under five minutes.  
**File:** `docker-compose.yml`.

---

*End of report. No critical findings. Addressing the five priority items above would reduce the residual risk level from MEDIUM to LOW.*
