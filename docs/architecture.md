# Architecture

How the system is put together, and why it looks like this rather than the
more obvious alternatives. For *what* every threshold and definition means,
see [product-spec.md](product-spec.md); for the reasoning behind each
individual technical choice, see [design-decisions.md](design-decisions.md).
This document is the middle layer: the shape of the system as a whole.

---

## 1. Style: a modular monolith, not microservices

One deployable API, one deployable worker, one database. No message queue,
no service mesh, no per-domain service boundary enforced over the network.

This is a deliberate choice, not a starting point outgrown later. The
system's own stated principle — *every technology needs a concrete reason to
exist* — cuts against microservices here specifically: the API and the
worker already don't share a request path (the API never calls a market
provider; the worker never serves HTTP), so splitting them into
network-separated services would add serialization, service discovery, and
partial-failure modes between two things that don't actually need to
coordinate synchronously. The one thing they *do* share — the domain layer
(`app/domain/`) and the database models — is exactly what a network boundary
would make harder to keep consistent, not easier.

What *is* split, deliberately, is deployment: the API and the worker are two
separate processes (two separate Render services in production), because
they have different failure characteristics and different scaling needs — a
slow provider cycle in the worker must never affect API latency, and restart
policy for a stateless request server is not the right restart policy for a
long-running ingestion loop. That split is real; it just doesn't need a
network protocol between the two sides, because there isn't one — they only
ever meet in PostgreSQL.

## 2. Layering inside the API

```
app/api/           HTTP only. Parses requests, calls a service, shapes a
                    response. No SQL, no scoring logic, no provider calls.
app/services/       Orchestration. Wires domain logic to repositories inside
                    a transaction. This is where "get_watchlist raises 404
                    for both missing and not-yours" lives, once.
app/domain/          Pure functions and pure data. No database import, no
  market/            provider import, no config import. Freshness
  changes/           classification, change detection, and attention
  watchlist/         scoring are all here, and are the parts of the system
                    tested without a database at all.
app/infrastructure/  The only place external systems are touched: database
  database/          sessions and repositories, provider adapters, the
  providers/         quote cache. Everything here implements an interface
  cache/             the domain layer defines, never the reverse.
app/models/         SQLAlchemy tables — the persistence shape.
app/schemas/         Pydantic request/response contracts — the wire shape.
                    Deliberately separate from models: a table column
                    should be free to exist without being API surface.
```

**The dependency rule:** `api → services → domain ← infrastructure`. Domain
code imports nothing from `infrastructure` or `api`. This is what makes
`app/domain/changes/scoring.py` testable with plain numbers and no running
database (see `tests/unit/test_scoring.py`) — the same property that makes a
threshold change reviewable by reading one pure function instead of tracing
it through a route handler, a session, and a provider call.

## 3. Data flow: market ingestion

```
┌──────────┐   get_quote(symbol)   ┌──────────────┐
│ Provider │ ────────────────────► │ worker/       │
│ (yfinance│  bounded retry +      │ ingestion.py  │
│  / mock) │  timeout (Phase 4)    │               │
└──────────┘                       └───────┬───────┘
                                            │ validate (Quote/Bar pydantic
                                            │ models reject bad data before
                                            │ it becomes a row)
                                            ▼
                                   ┌──────────────────┐
                                   │ market_snapshots  │  append-only,
                                   │ (immutable facts) │  ON CONFLICT DO
                                   └────────┬──────────┘  NOTHING dedup
                                            │
                                            │ is_latest_snapshot? (an
                                            │ out-of-order arrival stops
                                            │ here, stored as history only)
                                            ▼
                                   ┌──────────────────┐
                                   │ change_detection_  │  pure signals +
                                   │ service            │  scoring, using
                                   └────────┬──────────┘  daily_bars as
                                            │             the baseline
                                            ▼
                                   ┌──────────────────┐
                                   │  change_events     │  one per
                                   │ (derived judgment) │  (symbol,
                                   └──────────────────┘  trading_day)
```

Every arrow above is exercised independently in the worker's own
transactions (see `worker/ingestion.py`'s module docstring): a scoring bug
must not roll back a snapshot that already committed, and a bars-refresh
failure must not invalidate the quote that same cycle already captured.

## 4. Data flow: the user-return flow

```
User opens a watchlist
        │
        ▼
GET /watchlists/{id}          reads market_snapshots directly (no provider
                              call — see design-decisions.md #7) — this is
                              what keeps API latency independent of yfinance
        │
        ▼
GET /watchlists/{id}/changes  reads change_events WHERE detected_at >
                              user_seen_state.last_seen_at (or the last 24h,
                              framed as "first visit", if no cursor exists
                              yet) — ordered by score, not by time, so the
                              highest-attention item leads
        │
        ▼
User reads the feed, sees evidence strings, clicks "mark seen"
        │
        ▼
POST /watchlists/{id}/seen    GREATEST() upsert — see design-decisions.md
                              #9 — converges correctly even if two tabs
                              race, and can never move the cursor backward
```

`market_snapshots` (system fact), `change_events` (system judgment), and
`user_seen_state` (individual perspective) are three separate tables for
exactly the reason section 1 of product-spec.md gives: collapsing any two of
them is the way this kind of product usually breaks.

## 5. Deployment topology

```
┌─────────────┐        ┌───────────────────┐        ┌────────────────┐
│   Vercel     │  HTTPS │  Render Web Service│  psycopg│  Neon           │
│  (frontend,  │───────►│  (FastAPI API,      │───────►│  PostgreSQL     │
│   static +   │  CORS  │   Dockerfile:       │  v3    │  (managed,      │
│   Vite build)│ allow- │   backend/Dockerfile)│        │   serverless)   │
└─────────────┘  listed └───────────────────┘        └────────▲───────┘
                                                                │
                                                        psycopg │ v3
                                                                │
                                                       ┌────────┴────────┐
                                                       │ Render Background│
                                                       │ Worker            │
                                                       │ (worker/Dockerfile)│
                                                       └───────────────────┘
                                                                │
                                                                ▼
                                                       ┌────────────────┐
                                                       │ yfinance        │
                                                       │ (external, no  │
                                                       │  official API) │
                                                       └────────────────┘
```

Two separate Render services share one Dockerfile lineage but different
build contexts (see design-decisions.md #11): the API's `backend/Dockerfile`
builds from `backend/` alone; the worker's `worker/Dockerfile` builds from
the repository root, because it needs both `backend/app` and the sibling
`worker/` package in one image. Both run `alembic upgrade head` (API, on
every boot) or nothing (worker doesn't touch schema) against the same Neon
database — there is exactly one schema, owned by Alembic, never diverging
between what the API and the worker each expect.

The frontend never talks to Neon or to yfinance directly — every path from
the browser goes through the API, which is the only thing that knows how to
turn a database row into freshness-labeled, evidence-bearing JSON.

## 6. Why the API never calls the provider

This is the single architectural decision most of the rest of the system's
latency and reliability properties fall out of, so it is worth stating
plainly here rather than only in a route docstring: **`GET
/watchlists/{id}/symbols/{symbol}/quote` reads the last snapshot the worker
persisted. It never calls yfinance.** That means:

- API p99 latency cannot depend on an external, unofficial, rate-limitable
  service's response time.
- A yfinance outage degrades data *freshness* (the `freshness` field moves
  from `FRESH` toward `STALE`), never API *availability*.
- The API can scale horizontally (see below) without multiplying provider
  load — ten API instances still cost the provider exactly the ingestion
  worker's call volume, not ten times it.

## 7. Statelessness and horizontal scaling

The API keeps no in-process state that matters across requests, with one
narrow, explicit exception: the auth rate limiter (`app.state.auth_rate_limiter`,
see design-decisions.md #12) is per-instance by design, matching this
system's single-worker-process scope. Everything else — auth (JWT, verified
against the database on every request, not trusted from a session), business
data (PostgreSQL), and the optional quote cache (Redis, shared) — is
externalized. Running two API instances behind a load balancer is a
configuration change, not a code change; nothing here was retrofitted for
that, which is exactly why `test_query_plans.py` and the concurrency test
suite exist at the level they do — those are the tests that would actually
catch a hidden single-instance assumption.
