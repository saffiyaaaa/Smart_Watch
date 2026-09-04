# Design Decisions

Every entry here follows the same shape: **what was chosen**, **what was
rejected**, and **why** — because the "why" is what lets a future change be
evaluated against the same reasoning rather than against nothing. If a
rejected alternative's reason no longer holds (a dependency gets fixed, a
requirement changes), that is a legitimate reason to revisit the entry, not
a reason the entry was wrong to write down.

---

### 1. Modular monolith, not microservices

**Chosen:** one API process, one worker process, one database, communicating
only through PostgreSQL.

**Rejected:** separate services per domain (auth service, market-data
service, notification service), coordinated over HTTP or a message bus.

**Why:** the API and the worker don't share a request path — the API never
calls a provider, the worker never serves HTTP — so there is no synchronous
coordination need a network boundary would solve. What they *do* share (the
domain layer, the schema) is exactly what a network boundary would make
harder to keep consistent. See [architecture.md](architecture.md) §1.

---

### 2. A single asyncio loop for the worker, not Celery/Redis

**Chosen:** `worker/scheduler.py` — one process, one `while True`, one
provider call per tracked symbol per cycle.

**Rejected:** Celery + Redis/RabbitMQ as a distributed task queue.

**Why:** the workload is "fetch N symbols every few minutes from one
process." A task queue earns its complexity when work must be distributed
across many workers, retried with visibility into a broker, or prioritized
against other queues — none of which describes this workload. Per this
project's own principle, a technology needs a concrete reason to exist; here
none of Celery's reasons to exist apply yet. If ingestion volume ever
requires horizontal worker scaling, that is the point at which this decision
should be revisited, not before.

---

### 3. Deterministic scoring, not ML

**Chosen:** a hand-specified formula (price % vs. previous close, volume
ratio vs. 20-day average, confidence multipliers for stale/conflicting data)
producing a 0–100 score and a severity band.

**Rejected:** a trained model, an LLM-based "is this significant" judgment.

**Why:** meaningfulness has to be reproducible, explainable, and testable —
three properties a deterministic function has for free and a model does not.
Every severity band and multiplier in `app/domain/changes/scoring.py` is
verified against hand-calculated worked examples
(`docs/product-spec.md` §4, `tests/unit/test_scoring.py`), including the
load-bearing invariant that stale or conflicting data can never reach `HIGH`
severity — a property enforced at **startup** (`config.py`'s
`_check_invariants`), not just tested after the fact. An ML model cannot be
hand-verified this way, and "why was I shown this" cannot be answered with
an evidence string when the judgment came from a model's weights instead of
a formula.

---

### 4. psycopg (v3), not psycopg2 or asyncpg

**Chosen:** `psycopg[binary]` with SQLAlchemy's synchronous engine.

**Rejected:** psycopg2 (SQLAlchemy's historical default), asyncpg (faster,
async-native).

**Why:** psycopg2 is in maintenance mode; psycopg3 is its actively developed
successor with a compatible enough surface that SQLAlchemy supports it as a
first-class dialect. asyncpg would require the ORM layer and every repository
function to be async, for a workload (a handful of queries per request, no
long-held connections) where the concurrency benefit does not offset the
complexity of threading `async`/`await` through every layer, including
domain code that has no other reason to know about an event loop. The
worker's I/O-bound cost (provider network calls) is already handled with
`asyncio` at the layer that actually benefits — see decision #10 — without
forcing the database layer to follow.

**A real cost of this choice, paid during deployment:** SQLAlchemy's default
dialect for a bare `postgresql://` URL is psycopg2, not psycopg3. Every
managed Postgres provider (Neon, Render Postgres, Heroku, Supabase) hands out
exactly that bare scheme. `config.py`'s `_normalize_database_url_driver`
validator exists specifically to absorb this mismatch — a correct connection
string must not fail at startup with `ModuleNotFoundError: No module named
'psycopg2'` just because the provider's copy-paste convention doesn't know
which driver this project installs.

---

### 5. bcrypt directly, not passlib

**Chosen:** the `bcrypt` library called directly (`app/infrastructure/security.py`).

**Rejected:** passlib, a multi-scheme password-hashing abstraction.

**Why:** this system uses exactly one hashing scheme. passlib's value is
letting an application support several schemes at once (for migrating off an
old one, say) — a problem this system doesn't have. passlib is also
effectively unmaintained and has warned about compatibility with modern
bcrypt releases. The direct dependency is simpler and more current.

---

### 6. SQLite is never used, in tests or otherwise

**Chosen:** every integration test runs against real PostgreSQL
(`tests/conftest.py`'s `postgres_required` fixture family), never an
in-memory or file-based SQLite substitute.

**Rejected:** SQLite for fast, dependency-free test runs.

**Why:** almost everything worth verifying in this system is
PostgreSQL-specific — `ON CONFLICT DO NOTHING`, `DISTINCT ON`, `GREATEST()`'s
NULL handling, JSONB, regex `CHECK` constraints, and real `EXPLAIN` plans. A
SQLite suite would pass while proving nothing about the database this system
actually runs on. The cost is a slower, infrastructure-dependent test suite;
the alternative is a green test suite that lies.

---

### 7. The API never calls the market-data provider

**Chosen:** `GET /watchlists/{id}/symbols/{symbol}/quote` reads the last
snapshot the worker persisted to PostgreSQL.

**Rejected:** fetching a live quote from yfinance synchronously, in the
request path, on every read.

**Why:** yfinance is an unofficial, rate-limitable, occasionally slow
scraped API. Coupling API latency (and availability) to it would mean a
Yahoo Finance blip becomes a Smart Market Watchlist outage. Reading from the
database instead means the `freshness` field, not an HTTP 500, is what tells
the user their data is old — see [architecture.md](architecture.md) §6 and
failure-mode row 1 in [reliability.md](reliability.md).

---

### 8. `ON CONFLICT DO NOTHING`, not SELECT-then-INSERT

**Chosen:** every idempotent write (a duplicate market snapshot, a
re-added watchlist symbol, a duplicate registration) goes through a single
`INSERT ... ON CONFLICT` statement, pushing the race into PostgreSQL.

**Rejected:** check-then-write application logic ("does this row exist? if
not, insert it").

**Why:** SELECT-then-INSERT has a race window between the two statements —
two concurrent callers can both see "does not exist" and both insert, one of
them then failing on the unique constraint with a raw `IntegrityError`
surfaced as a confusing 500. `ON CONFLICT DO NOTHING` makes the database the
single arbiter, so every concurrent caller gets a clean, identical outcome.
This is proven under genuine multi-threaded contention, not just asserted,
in `tests/integration/test_concurrency.py` — including a regression test
added specifically because the failure mode above is easy to reintroduce by
accident and easy to miss in review (a SELECT-then-INSERT implementation
looks correct until it is actually raced).

---

### 9. `GREATEST()` upsert for the seen-state cursor

**Chosen:** advancing a user's "last seen" cursor is one `INSERT ... ON
CONFLICT DO UPDATE SET last_seen_at = GREATEST(excluded.last_seen_at,
user_seen_state.last_seen_at)` statement.

**Rejected:** read the current cursor, compare in application code, write if
newer.

**Why:** the same race as decision #8, with a subtler failure mode — two
browser tabs marking seen "at the same moment" can both read the old value,
and whichever write lands *second* wins, even if it carries the *earlier*
timestamp. That silently moves the cursor backward, which can resurrect
events the user already saw. `GREATEST()` makes the comparison and the write
one atomic database operation: the larger timestamp always wins regardless
of arrival order.

---

### 10. Bounded worker concurrency, not fully sequential or fully parallel

**Chosen:** `worker/ingestion.py`'s `ingest_all` fetches up to
`WORKER_SYMBOL_CONCURRENCY` symbols' quotes concurrently (an `asyncio.Semaphore`),
while every database write is serialized through a shared `asyncio.Lock`
around each db-touching phase.

**Rejected (too slow):** processing every symbol strictly one at a time —
the original Phase 5 implementation, deferred deliberately: "running symbols
concurrently is a Phase 12 performance question, not a Phase 5 correctness
one."

**Rejected (unsafe):** firing all symbols at once with no bound, or sharing
one `Session` across truly concurrent database writes with no
synchronization.

**Why:** the dominant per-symbol cost is two provider network calls (quote,
daily history), which genuinely overlap under `asyncio`. Database writes do
not benefit the same way — a single `Session` is not safe for two
coroutines to be inside simultaneously — so the lock exists specifically to
let network I/O overlap while keeping every actual database statement
serialized. The bound (rather than unlimited concurrency) exists because an
unbounded fan-out of provider calls is indistinguishable, from the
provider's perspective, from an abuse pattern. Verified in
`tests/integration/test_ingestion.py::TestIngestAllConcurrency`: the
concurrency bound is respected, genuine overlap occurs, and a concurrent run
is measurably faster than an equivalent sequential one.

---

### 11. Redis quote cache: provisioned, off by default, and why it needed its own Dockerfile-adjacent decision

**Chosen:** `app/infrastructure/cache/quote_cache.py`, a short-TTL cache in
front of `provider.get_quote`, gated by `CACHE_ENABLED` (default `false`)
and failing open on every error.

**Rejected:** no cache at all; an always-on cache; a cache with no
fail-open behavior.

**Why no cache at all wasn't chosen:** `worker_all`'s symbol discovery
already deduplicates identical symbols within one ingestion cycle
(`get_all_tracked_symbols` is `DISTINCT`), but nothing previously prevented
re-fetching the same symbol across *cycles* run closer together than the
configured interval — a real scenario for an ad-hoc `--once` invocation
during a demo, or a future second worker instance. Redis was already the
right tool for this because it was already provisioned for exactly this
purpose (see `docker-compose.yml`'s redis service comment) rather than being
a new dependency introduced to solve it.

**Why off by default and fail-open:** nothing in the request path ever
calls a provider (decision #7), so this cache is a worker-only optimization,
not a correctness requirement — a cache must never become a new way for
ingestion to fail. Every Redis error (unreachable, corrupted value) is
logged and treated as a cache miss, never raised.

**A related decision, made for the worker's separate deployment:** the
worker needs both the `app` package (installed from `backend/`) and the
sibling `worker/` package in one Docker image, but `backend/Dockerfile`'s
build context is `backend/` alone and cannot reach outside it. Rather than
restructure the repository (moving `worker/` inside `backend/` would break
the "worker and app are equal siblings, not app-owns-worker" relationship
the codebase's own docstrings describe), `worker/Dockerfile` builds from the
repository root instead, leaving the API's existing, working Dockerfile and
Render service untouched.

---

### 12. In-memory rate limiter, not Redis-backed

**Chosen:** `app/infrastructure/rate_limit.py`'s `InMemoryRateLimiter`, a
fixed-window counter per process, applied to `/auth/register` and
`/auth/login`.

**Rejected:** a Redis `INCR`+`EXPIRE` distributed limiter.

**Why:** the same reasoning as decision #2 — a distributed limiter solves a
problem (multiple API instances needing to share one budget) this
deployment does not have. Redis is already available in this system (for
the quote cache), so the upgrade path is cheap *when* it's needed: swap the
limiter's backing store, not its interface. Building that now would be
solving for a scale this system isn't running at.

---

### 13. `SET statement_timeout`, not a startup-packet `options` parameter

**Chosen:** the database engine issues `SET statement_timeout = ...` as a
real query on each new connection (`app/infrastructure/database/session.py`,
a SQLAlchemy `connect` event listener).

**Rejected:** `create_engine(..., connect_args={"options": "-c
statement_timeout=..."})` — SQLAlchemy's more common idiom for this, and
this project's own first implementation.

**Why:** the `options` startup parameter is part of the connection's
*startup packet*, and a connection pooler sitting in front of Postgres
(PgBouncer — which is what a managed provider's "pooled" endpoint actually
is, e.g. Neon's `-pooler` hostname) is not guaranteed to forward arbitrary
startup parameters. In production this was not a hypothetical: the app's
persistent connection pool could not connect to Neon's pooled endpoint at
all with the `options` parameter set, while Alembic's one-off migration
connection (no such parameter) connected to the identical endpoint
successfully — the exact split between `/health` succeeding and `/ready`
reporting a database error. A `SET` issued after the connection is
established is an ordinary query, not a startup parameter, and behaves
identically whether the endpoint is direct or pooled. See
[reliability.md](reliability.md)'s security-and-deployment section.

---

### 14. Migrate on every container boot, not as a manual deploy step

**Chosen:** the API's `Dockerfile` CMD runs `alembic upgrade head` before
starting uvicorn, on every boot.

**Rejected:** a manual `alembic upgrade head` run by a human (or a separate
CI step) before each deploy.

**Why:** `alembic upgrade head` is idempotent — a migration already applied
is a no-op — so running it unconditionally on boot is safe, and it is also
the difference between "a fresh managed Postgres instance gets its schema on
first deploy automatically" and "a fresh deploy 500s until someone remembers
to run a command." A manual step is a step that gets forgotten exactly once,
at the worst time.

---

### 15. Vercel + Render + Neon, not a single VPS or a single all-in-one PaaS

**Chosen:** frontend on Vercel (static build + CDN), API and worker as two
separate Render services (Docker-based), database on Neon (managed,
serverless Postgres).

**Rejected:** one VPS running everything (nginx, gunicorn, Postgres,
cron); a single Render/Fly/Railway app hosting frontend and backend
together.

**Why:** each piece is a different kind of workload with a different
scaling and failure profile — a static frontend wants a CDN, not a process
to keep alive; the API wants to scale independently of the worker (decision
#1); the database wants managed backups and connection pooling without this
project reimplementing either. Splitting them onto purpose-built platforms
means each piece runs on infrastructure designed for its actual shape,
rather than this project maintaining a general-purpose server to do all
three jobs adequately.
