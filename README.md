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

## Status

Built phase by phase against [docs/product-spec.md](docs/product-spec.md).

- [x] **Phase 0** — Product contract
- [x] **Phase 1** — Tooling and local infrastructure
- [x] **Phase 2** — Database schema and persistence
- [x] **Phase 3** — Auth and watchlist domain
- [x] **Phase 4** — Market provider adapter
- [x] **Phase 5** — Ingestion worker
- [x] **Phase 6** — Change detection
- [x] **Phase 7** — Attention scoring
- [ ] Phase 8 — Last-seen state and change feed
- [ ] Phase 9 — API completion
- [ ] Phase 10 — Frontend
- [ ] Phase 11 — Reliability and concurrency

## Quick start

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
```

Open <http://localhost:5173> — it shows the live status of both backend endpoints.

API documentation is generated at <http://localhost:8000/docs>.

### Why port 5433

A locally installed Postgres commonly already owns 5432. The container publishes on **5433** so both can run without conflict. Inside the Docker network it is still 5432.

### Why Python 3.13 and not 3.14

`yfinance` pulls in the pandas/numpy stack, whose wheel coverage is more reliable on 3.13. `pyproject.toml` pins `>=3.11,<3.14` to make this explicit rather than leaving it to be discovered during an install failure.

### Running the ingestion worker

The worker lives at the repo root (`worker/`), a sibling of `backend/`, so it must be run with `-m` from the repository root — not `python worker/scheduler.py` — so that both `app.*` (installed editable into `backend/.venv`) and `worker.*` (resolved via the working directory) are importable:

```bash
backend/.venv/bin/python -m worker.scheduler --once   # one ingestion cycle
backend/.venv/bin/python -m worker.scheduler          # loops forever on WORKER_INTERVAL_SECONDS
```

## Verification

```bash
cd backend && .venv/bin/python -m pytest -q      # unit + integration tests
cd backend && .venv/bin/python -m ruff check .   # lint
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
docs/               Spec, architecture, design decisions, reliability
```

The dependency rule: `api → domain ← infrastructure`. Domain logic imports nothing from the layers around it, which is what makes it testable without a database or a network.

## Documentation

- **[docs/product-spec.md](docs/product-spec.md)** — definitions, scoring model, failure matrix
- [docs/architecture.md](docs/architecture.md) — system and data flow
- [docs/design-decisions.md](docs/design-decisions.md) — why each choice, and what was rejected
- [docs/reliability.md](docs/reliability.md) — failure modes mapped to the tests that prove them
- [docs/api.md](docs/api.md) — endpoints and example payloads
