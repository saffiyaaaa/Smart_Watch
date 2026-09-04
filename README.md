# Smart Market Watchlist

A watchlist that answers **"what meaningfully changed since I last checked, and why?"** rather than just showing prices.

## The problem interpretation

Displaying stock prices is solved; every broker app does it. The interesting question is what a user should *pay attention to* after being away — and answering it well forces three concepts apart:

| Concept | What it is | Owned by |
|---|---|---|
| **Market fact** | A price was X at time T, per a named source | The market. Immutable, append-only. |
| **Derived event** | A judgement that a fact was significant, with a score and evidence | The system. Recomputable. |
| **Seen state** | A per-user cursor over derived events | The user. Independent per user. |

Collapsing any two of these is the main way this product fails. A `last_seen` column on a stock row would mean two users could not have different views of the same market. Overwriting a price row would destroy the history that makes change detection possible in the first place.

The full contract — every threshold, formula and failure behaviour — is in **[docs/product-spec.md](docs/product-spec.md)**. If a number appears in code but not there, it is a bug.

## Tech stack

| Layer | Technology | Why |
|---|---|---|
| Frontend | React + TypeScript + Vite | Fast dev loop, typed component boundaries |
| Frontend data | TanStack Query | Server-state caching, loading/error states, keeps stale data on screen through a failed refetch |
| Styling | Tailwind CSS | Fast, consistent UI without a separate design-system dependency |
| Backend | Python + FastAPI | Typed request/response contracts via Pydantic, async-capable where it matters (the worker), sync where it doesn't (the DB layer) |
| Validation | Pydantic | Request/response schemas *and* the provider boundary (`Quote`/`Bar` models reject bad data before it becomes a row) |
| ORM | SQLAlchemy (2.0, sync) | See [design-decisions.md](docs/design-decisions.md) #4 for why sync, and psycopg not psycopg2 |
| DB driver | psycopg (v3) | Actively maintained successor to psycopg2 |
| Migrations | Alembic | Versioned schema, run automatically on every API boot (idempotent) |
| Database | PostgreSQL | Real constraints (`UNIQUE`, `CHECK`) doing correctness work, not just storage |
| Worker | Plain `asyncio` loop, no task queue | See [design-decisions.md](docs/design-decisions.md) #2 for why not Celery/Redis |
| Cache | Redis (optional, off by default) | Short-TTL quote cache for the worker; see [design-decisions.md](docs/design-decisions.md) #11 |
| Auth | JWT (python-jose) + bcrypt | See [docs/reliability.md](docs/reliability.md)'s security review |
| Testing | pytest, real PostgreSQL (never SQLite) | See [Testing](#testing) below |
| Local infra | Docker Compose | Postgres + Redis, reproducible from one command |
| Deployment | Vercel (frontend) + Render (API + worker, Docker) + Neon (managed Postgres) | See [Deployment](#deployment) below |

## Status

Built phase by phase against [docs/product-spec.md](docs/product-spec.md). All 15 phases complete and deployed.

- [x] **Phase 0** — Product contract
- [x] **Phase 1** — Tooling and local infrastructure
- [x] **Phase 2** — Database schema and persistence
- [x] **Phase 3** — Auth and watchlist domain
- [x] **Phase 4** — Market provider adapter
- [x] **Phase 5** — Ingestion worker
- [x] **Phase 6** — Change detection
- [x] **Phase 7** — Attention scoring
- [x] **Phase 8** — Last-seen state and change feed
- [x] **Phase 9** — API completion and integration tests
- [x] **Phase 10** — Frontend
- [x] **Phase 11** — Reliability and concurrency
- [x] **Phase 12** — Performance and scalability (worker concurrency, quote cache)
- [x] **Phase 13** — Security review (rate limiting, request-size limits, dependency audit)
- [x] **Phase 14** — Deployed: [see below](#deployment)

## Architecture

A modular monolith, not microservices — one API process, one worker process,
one database, communicating only through PostgreSQL. The full reasoning is
in [docs/architecture.md](docs/architecture.md); the short version:

```
api/           HTTP only. No SQL, no scoring.
services/       Orchestration + transactions.
domain/         Pure business logic. No database or provider imports.
  market/       Snapshot and freshness rules
  changes/      Change detection and attention scoring
  watchlist/    Watchlist rules
infrastructure/ Database, provider adapters, cache. The only place
                external systems are touched.
```

The dependency rule: `api → services → domain ← infrastructure`. Domain
logic imports nothing from the layers around it, which is what makes it
testable without a database or a network — see `backend/tests/unit/`.

**The single most important request-path decision:** the API never calls
the market-data provider. `GET .../quote` reads the last snapshot the
worker persisted; a yfinance outage degrades data *freshness*, never API
*availability*. See [architecture.md](docs/architecture.md) §6.

## How to run this project

There are three ways to run it, in increasing order of how much you want to
touch: full local dev (edit code, hot reload), Docker-only (build and run
the actual production images locally), and the live deployed instance.

### Option A — Full local dev

Requires Docker, Python 3.11–3.13, and Node 20+.

```bash
# 1. Infrastructure (Postgres on host port 5433, Redis on 6380)
docker compose up -d

# 2. Configuration
cp .env.example .env
# then set a real JWT_SECRET:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 3. Backend
cd backend
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload      # http://localhost:8000

# 4. Frontend (separate terminal)
cd frontend
npm install
npm run dev                                   # http://localhost:5173

# 5. Worker (separate terminal, from the repository root — not backend/)
backend/.venv/bin/python -m worker.scheduler --once   # one ingestion cycle
backend/.venv/bin/python -m worker.scheduler          # loops forever
```

Open <http://localhost:5173> — it shows the live status of both backend endpoints. API documentation is generated at <http://localhost:8000/docs>.

Nothing has quote data until the worker runs at least once for a tracked
symbol — the API deliberately never fetches on demand (see
[architecture.md](docs/architecture.md) §6). Add a symbol, then run the
worker with `--once` to see it populate.

**Why port 5433:** a locally installed Postgres commonly already owns 5432. The container publishes on **5433** so both can run without conflict. Inside the Docker network it is still 5432.

**Why Python 3.13 and not 3.14:** `yfinance` pulls in the pandas/numpy stack, whose wheel coverage is more reliable on 3.13. `pyproject.toml` pins `>=3.11,<3.14` to make this explicit rather than leaving it to be discovered during an install failure.

**Why the worker runs with `-m` from the repository root, not `python worker/scheduler.py`:** `worker/` and `app/` (inside `backend/`) are sibling packages once `backend` is pip-installed editable. `-m` from the repo root puts the repo root on `sys.path`, which is what makes `from worker.ingestion import ingest_all` resolve — see `worker/scheduler.py`'s own docstring.

### Option B — Docker only (build and run the real images)

This is exactly what runs in production, just on your machine. Useful for
verifying a change survives containerization without needing a cloud deploy.

```bash
docker compose up -d   # Postgres + Redis

# API (from backend/) — runs alembic upgrade head, then serves
docker build -t smw-api -f backend/Dockerfile backend
docker run --rm -p 8000:8000 \
  -e DATABASE_URL='postgresql+psycopg://smw:smw_dev_password@host.docker.internal:5433/smart_market_watchlist' \
  -e JWT_SECRET='dev-secret' \
  -e MARKET_PROVIDER=mock \
  smw-api

# Worker (from the repo ROOT — different build context, see below)
docker build -t smw-worker -f worker/Dockerfile .
docker run --rm \
  -e DATABASE_URL='postgresql+psycopg://smw:smw_dev_password@host.docker.internal:5433/smart_market_watchlist' \
  -e JWT_SECRET='dev-secret' \
  -e MARKET_PROVIDER=mock \
  smw-worker python -m worker.scheduler --once
```

**Why the worker's build context is the repo root and the API's is `backend/`:**
the worker needs both `backend/app` and the sibling `worker/` package in one
image; `backend/`'s build context alone cannot reach outside it. See
[design-decisions.md](docs/design-decisions.md) #11.

### Option C — The live deployment

See [Deployment](#deployment) below for the running instance and its topology.

## Testing

```bash
cd backend && .venv/bin/python -m pytest -q      # 548 tests: unit + integration
cd backend && .venv/bin/python -m ruff check .   # lint
cd backend && .venv/bin/python -m ruff format --check .
cd frontend && npm run build                     # typecheck + production build
cd frontend && npm run lint
```

**Why every integration test runs against real PostgreSQL, never SQLite:**
almost everything worth testing here is PostgreSQL-specific — `ON CONFLICT
DO NOTHING`, `DISTINCT ON`, `GREATEST()`'s NULL handling, JSONB, regex
`CHECK` constraints, real `EXPLAIN` plans. A SQLite suite would pass while
proving nothing about the database this system actually runs on.

**The test pyramid, and what each layer is actually for:**

| Layer | What it proves | Example |
|---|---|---|
| Unit (`tests/unit/`) | Pure domain logic — scoring, freshness, detection, retry backoff — with no database, no network. | `test_scoring.py` verifies the load-bearing invariant that stale/conflicting data can never reach `HIGH` severity, by calculation, not by running the system. |
| Integration (`tests/integration/`) | Real HTTP → real service → real PostgreSQL, including authorization, constraints, and transaction rollback. | `test_watchlist_api.py`'s cross-user 404 tests. |
| Concurrency (`test_concurrency.py`, `test_change_feed_concurrency.py`) | Genuine races, using real threads on separate connections — a single shared session serializes work by definition and cannot exercise a race at all. | 12 threads adding the same symbol simultaneously; exactly one row, every caller succeeds. |
| Query plans (`test_query_plans.py`) | An index that exists is not an index that gets used — asserts on real `EXPLAIN` output against a loaded table, not just that a migration ran. | The ingest hot path's `ON CONFLICT` arbiter index, not a `Seq Scan`. |
| Provider contract | A real adapter (gated, `pytest -m network`) plus deterministic fakes for every failure mode in the matrix. | `test_yfinance_provider.py`; `mock_provider.py`'s `StaleProvider`, `TimeoutProvider`, etc. |

**Edge cases and failure modes** are tracked as a single source of truth —
[docs/reliability.md](docs/reliability.md) maps all 16 rows of the
product-spec's failure-mode matrix to the exact test that proves each one,
including honest notes where coverage has a real gap rather than claiming
completeness it doesn't have. It also records three failures found only by
deploying to real infrastructure (a managed Postgres connection pooler, a
split-service deployment) that no local test could have caught — see that
document's "Deployment" section.

## Deployment

```
Vercel (frontend)  ──HTTPS/CORS──►  Render Web Service (FastAPI API)  ──psycopg──►  Neon PostgreSQL
                                                                                            ▲
                                    Render Background Worker  ───────psycopg───────────────┘
                                            │
                                            ▼
                                       yfinance
```

- **Frontend** — Vercel, static Vite build. `VITE_API_URL` points at the API.
- **API** — Render Web Service, `backend/Dockerfile`, Root Directory `backend`. Runs `alembic upgrade head` on every boot (idempotent), then serves.
- **Worker** — Render Background Worker, `worker/Dockerfile`, Root Directory `.` (repo root — different from the API; see [design-decisions.md](docs/design-decisions.md) #11). Runs `python -m worker.scheduler`, looping forever on `WORKER_INTERVAL_SECONDS`.
- **Database** — Neon, managed serverless PostgreSQL.

**Why two separate Render services instead of one:** the API and worker have
different failure and scaling characteristics — a slow ingestion cycle must
never affect API latency, and a stateless request server's restart policy is
not the right one for a long-running loop. See
[architecture.md](docs/architecture.md) §1 and
[design-decisions.md](docs/design-decisions.md) #15.

**Required environment variables** (both services, see `.env.example` for the complete list with defaults):

```
DATABASE_URL=postgresql://...        # any managed provider's bare scheme works — see below
JWT_SECRET=<32+ random bytes>
ENVIRONMENT=production
MARKET_PROVIDER=yfinance
CORS_ORIGINS=https://your-frontend.vercel.app   # API service only
```

**Two deployment-specific behaviors worth knowing about, both discovered by
deploying to real managed infrastructure and both now fixed in code** (full
writeups in [docs/reliability.md](docs/reliability.md)'s Deployment section):

1. `DATABASE_URL` accepts a bare `postgresql://` string exactly as every
   managed provider (Neon, Render Postgres, Heroku, Supabase) hands it out —
   the app normalizes the driver internally, so you never need to add
   `+psycopg` yourself.
2. The database connection pool sets its statement timeout via a `SET`
   command rather than a startup parameter, specifically so it works through
   a pooled endpoint (PgBouncer) — a managed provider's "pooled" connection
   string, not just its direct one.

## Layout

```
backend/app/
  api/routes/       HTTP layer only. No SQL, no scoring.
  domain/           Pure business logic. No database or provider imports.
    market/         Snapshot and freshness rules
    changes/        Change detection and attention scoring
    watchlist/      Watchlist rules
  infrastructure/   Database, provider adapters, cache. The only place
                    external systems are touched.
  models/           SQLAlchemy tables
  schemas/          Pydantic request/response contracts
  config.py         Every tunable number in the system
worker/             Ingestion and scheduling, deployed separately from the API
frontend/src/       React watchlist UI
docs/               Spec, architecture, design decisions, reliability, API reference
```

The dependency rule: `api → domain ← infrastructure`. Domain logic imports nothing from the layers around it, which is what makes it testable without a database or a network.

## Documentation

- **[docs/product-spec.md](docs/product-spec.md)** — definitions, scoring model, failure matrix. The contract everything else derives from.
- **[docs/architecture.md](docs/architecture.md)** — system and data flow, layering, deployment topology, and *why* the shape is what it is.
- **[docs/design-decisions.md](docs/design-decisions.md)** — 15 numbered decisions, each with what was chosen, what was rejected, and why — including the three found during deployment.
- **[docs/reliability.md](docs/reliability.md)** — every failure-mode-matrix row mapped to the test that proves it, a security review, and real deployment failures no test suite caught.
- **[docs/api.md](docs/api.md)** — endpoints, example payloads, rate limits, error codes.
