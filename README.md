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

venv/bin/python -m ruff format --check .
cd frontend && npm run build                     # typecheck + production build
cd frontend && npm run lint
```
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
