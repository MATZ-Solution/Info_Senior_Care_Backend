# InfoSenior Care -- Backend API

Production-grade FastAPI backend for the InfoSenior Care app (Infomary AI
excluded -- handled separately). Built for **10,000+ concurrent users**
without falling over, using Supabase Postgres + Valkey caching.

Status: **local-only** right now (per current phase) -- deployment is a
separate later step. Everything below has been tested end-to-end against
a real Postgres + Valkey and the real 60,037-row facility dataset.

---

## Stack

| Layer | Choice |
|---|---|
| Framework | FastAPI (async) + Uvicorn/Gunicorn |
| Database | Supabase Postgres (pooler mode in production) |
| Cache / rate-limit | **Valkey** (Redis-protocol compatible -- same `redis-py` client) |
| Auth | Supabase Auth (email, Google, Apple) -- JWT verified here |
| Migrations | Alembic |
| ORM | SQLAlchemy 2.0 (async, `asyncpg`) |

---

## Project layout

```
backend/
├── app/
│   ├── main.py                  # FastAPI app, middleware, exception handlers
│   ├── core/                    # config, database, cache (Valkey), security (JWT)
│   ├── api/v1/endpoints/        # one file per resource (auth, facilities, ...)
│   ├── models/                  # SQLAlchemy models (normalized facility schema)
│   ├── schemas/                 # Pydantic request/response shapes
│   ├── services/                # dedup hashing, guest sessions, profile provisioning
│   └── dependencies.py          # auth DI: require_user / optional_user / require_user_or_guest
├── alembic/                     # migrations (initial schema incl. dedup indexes)
├── scripts/
│   └── import_facilities.py     # idempotent CSV importer (see below)
├── tests/                       # 20 tests, all passing -- see "Testing" below
├── requirements.txt
├── .env.example                 # copy to .env and fill in
└── docker-compose.yml           # Valkey for local dev (Postgres is local-native here)
```

---

## Running locally

1. **Postgres + Valkey** need to be reachable. This repo was developed and
   tested against a local Postgres 16 + Redis-protocol server; if you
   don't have Postgres locally, run `docker compose up -d valkey` for the
   cache and either install Postgres natively or use a local Supabase CLI
   instance.

2. **Environment**:
   ```bash
   cp .env.example .env
   ```
   Edit `.env`: `DATABASE_URL`, `MIGRATION_DATABASE_URL`, `VALKEY_URL`,
   and the `SUPABASE_*` values once you have a real Supabase project.

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt --break-system-packages
   ```

4. **Run migrations**:
   ```bash
   python3 -m alembic upgrade head
   ```

5. **Import facility data** (idempotent -- safe to re-run):
   ```bash
   python3 -m scripts.import_facilities /path/to/data_with_uuid.csv
   ```

6. **Run the API**:
   ```bash
   uvicorn app.main:app --reload
   ```
   Docs at `http://localhost:8000/docs` (auto-disabled when `ENVIRONMENT=production`).

---

## Testing

```bash
python3 -m pytest -v
```

**20/20 tests passing.** Covers:
- Health/readiness checks
- Dedup-hash logic (the core guarantee behind "no duplicates on re-import")
- Guest session token issue/verify/tamper-rejection
- Google sign-in, Apple sign-in, and confirming they produce **distinct**
  tracked profiles (`auth_provider` field)
- Full user journey: sync-profile -> onboarding -> search -> facility
  detail -> save/unsave (idempotent double-save) -> inquiry -> assessment
- Guest flow: can submit an inquiry, cannot access `/profile/me`
- 404/400 handling for missing/invalid facility ids

Tests run against the real local Postgres + Valkey (not mocks), using
JWTs signed with the same `SUPABASE_JWT_SECRET` your `.env` has -- this
validates the actual auth-verification code path, not a stand-in.

**Note on cache during testing/reimports:** the test suite flushes Valkey
before running (see `tests/conftest.py::flush_cache_before_tests`),
because a stale cached response referencing an old facility id (e.g. after
a full data reimport regenerates ids) can otherwise return inconsistent
results. Do the same in production ops after any bulk reimport: flush the
relevant cache keys (or the whole cache in a pinch) once the import
finishes.

---

## Facility data import -- design decisions

The source CSV (60,037 rows, 95 columns, CMS + state-directory data across
53 states/territories) has two real data-quality properties that shaped
the import design:

1. **Only ~19% of rows have `ccn`** (CMS Certification Number) -- the
   other 81% come from state directories with no such identifier.
2. **The same facility can legitimately appear under different
   `facility_type` values with different CCNs** (e.g. a single business
   licensed for both Home Health and Hospice at the same address).

This means identity/dedup matching can't rely on `ccn` alone, and can't
naively dedup on name+address+zip+state either (that would wrongly merge
distinct service lines). The solution:

- **Primary match key**: `ccn`, when present (partial unique index,
  `WHERE ccn IS NOT NULL`).
- **Fallback match key**: `dedup_hash` = `sha256(normalized name + address +
  zip + state + facility_type)` -- always computed, unique-indexed.
- **Import is a staging-table UPDATE-then-INSERT**, not a plain
  `INSERT ... ON CONFLICT`, because a single statement can't target two
  different unique indexes, and real rows in this file DO cross-match
  (a CMS row with `ccn` and a state-directory row without one, for the
  physically same facility) -- these get merged (ccn is backfilled onto
  the existing record) rather than erroring or duplicating.
- Confirmed idempotent: running the same file twice inserts 0 new rows
  the second time; every row updates instead.
- 2 rows in the source file had a completely blank `name` -- these are
  skipped (logged as "rejected_invalid") rather than crashing the import.

Run report from the real dataset:
```
Total rows read      : 60037
Inserted (new)        : 59901
Updated (matched)     : 66      (cross-source duplicates merged)
Skipped (in-file dup) : 68      (exact identity collisions within one file)
Rejected (invalid)    : 2       (blank name)
```

**Schema is normalized**, not one 95-column table:
- `facilities` -- core ~25 columns every screen needs (search/map/cards).
- `nursing_home_details`, `home_health_details`, `facility_services` --
  1:1 detail tables, only joined by the single facility-detail endpoint,
  and only the ONE relevant table for that facility's type. This is what
  keeps the high-traffic `facilities/search` endpoint cheap: it never
  touches the wide, mostly-null detail tables.

---

## API surface (20 endpoints, Infomary/Voice excluded)

```
POST   /api/v1/auth/sync-profile        # call right after any Supabase login
POST   /api/v1/auth/guest               # anonymous guest session

POST   /api/v1/onboarding/complete
GET    /api/v1/onboarding/me

GET    /api/v1/facilities/search        # cached, column-slim, paginated
GET    /api/v1/facilities/suggest       # autocomplete
GET    /api/v1/facilities/recommended
GET    /api/v1/facilities/{id}          # only endpoint that joins detail tables

POST   /api/v1/inquiries
GET    /api/v1/inquiries/me

POST   /api/v1/assessment/submit
GET    /api/v1/assessment/me/latest

GET    /api/v1/saved
POST   /api/v1/saved/{facility_id}
DELETE /api/v1/saved/{facility_id}

GET    /api/v1/profile/me
PATCH  /api/v1/profile/me
PATCH  /api/v1/profile/loved-one

GET    /api/v1/resources
GET    /api/v1/resources/{id}

GET    /health          # liveness
GET    /health/ready     # readiness (checks DB + Valkey)
```

---

## Auth: Google + Apple tracked separately

Supabase Auth issues the same JWT shape regardless of provider (email,
Google, Apple) -- the provider is embedded in `app_metadata.provider`.
`POST /auth/sync-profile` reads that claim and stores it as
`profiles.auth_provider` (`email` | `google` | `apple` | `guest`), so
Google and Apple sign-ins are tracked as distinct, per your requirement.
Tested explicitly (see `test_google_and_apple_profiles_are_distinct_users`).

---

## Connecting to real Supabase Postgres (instead of local Postgres)

If you're pointing `DATABASE_URL` at Supabase's pooler (port `6543`,
transaction mode) rather than a local Postgres, note:

- `app/core/database.py` already sets `statement_cache_size: 0` for
  asyncpg -- this is required with pgbouncer transaction-mode pooling,
  otherwise you'll intermittently hit confusing
  `prepared statement "..." already exists` errors under load (pgbouncer
  can hand your session a different physical backend connection between
  statements, but asyncpg caches prepared statements per connection).
  This is already handled for you; no action needed.
- `DATABASE_URL` needs the `+asyncpg` driver prefix:
  `postgresql+asyncpg://...` -- Supabase's dashboard gives you a plain
  `postgresql://...` string, so add `+asyncpg` after copying it in.
- `MIGRATION_DATABASE_URL` should be the **direct** connection (port
  `5432`), not the pooler -- Alembic's DDL statements don't play well
  with transaction-mode pooling.

---

## Known data observation (worth a product decision, not a bug)

`overall_rating` for `Nursing Home` rows ranges **1-9** in the source data,
while `Home Health` rows range 1-5. This looks like two different rating
scales blended into one column (possibly CMS's 5-star scale plus a
separate 1-9 metric from a state source). Worth deciding how to display
this consistently before launch -- flagging rather than silently
normalizing it for you.

---

## Scaling notes for 10k concurrent users (not yet load-tested)

The architecture is designed for this (async everywhere, Supabase
pooler/pgbouncer, Valkey caching on read-heavy endpoints, column-slim
queries, horizontal-scale-ready stateless app instances, rate limiting).
None of this has been load-tested yet (e.g. with `locust` or `k6`) -- do
that before relying on the 10k number in production, and before
deployment (next phase).

## Budget/price filter -- not implemented

`facilities/search` does not filter by price. The current dataset (CMS +
state directories) has no pricing field; wiring a `budget_min`/`budget_max`
param to an unrelated column (e.g. bed count) would silently return wrong
results, so it was left out rather than faked. Add a real pricing column
+ source when that data exists.
