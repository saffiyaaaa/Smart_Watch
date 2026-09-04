# API Reference — Smart Market Watchlist

**Base URL:** `http://localhost:8000` (development) · deployed example: `https://smart-watch-ovz6.onrender.com` (see [README.md](../README.md#deployment) for the full deployment topology)

**Authentication:** Bearer token. Obtain a token from `POST /auth/login` and include it as:
```
Authorization: Bearer <token>
```

---

## Error format

Every error response — regardless of cause — uses this envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "Watchlist not found",
    "details": {}
  }
}
```

| HTTP status | `code` | Meaning |
|---|---|---|
| 401 | `unauthorized` | Missing, invalid, or expired token |
| 404 | `not_found` | Resource does not exist **or** belongs to another user |
| 409 | `conflict` | Duplicate unique resource (e.g. watchlist name already exists) |
| 413 | `request_too_large` | Request body exceeds `MAX_REQUEST_BODY_BYTES` (default 1 MB) |
| 422 | `validation_error` | Request body failed schema validation |
| 429 | `rate_limited` | Too many requests to `/auth/register` or `/auth/login` — see [Rate limiting](#rate-limiting) |
| 503 | `service_unavailable` | Database unavailable (controlled — never exposes internals) |

> **Security note:** 404 is returned for both "not found" and "not yours". The caller cannot distinguish the two, which prevents probing other users' IDs.

---

## Auth

### `POST /auth/register`

Register a new account. Rate limited — see [Rate limiting](#rate-limiting).

**Request**
```json
{ "email": "alice@example.com", "password": "correct-horse-battery" }
```

Password must be at least 8 characters and at most 72 **bytes** when
UTF-8-encoded (bcrypt's own limit — an over-long password is rejected
rather than silently truncated, since silent truncation would let two
different long passwords collide on the same hash).

**Response** `201 Created`
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "email": "alice@example.com",
  "created_at": "2026-03-10T09:00:00Z"
}
```

**Errors**
- `409 conflict` — email already registered
- `429 rate_limited` — too many requests from this client; see [Rate limiting](#rate-limiting)

---

### `POST /auth/login`

Exchange credentials for a JWT access token. Rate limited — see
[Rate limiting](#rate-limiting), and shares the same budget as `/auth/register`
(both are reachable before any identity exists, so both draw from one
IP-keyed limit).

**Request**
```json
{ "email": "alice@example.com", "password": "correct-horse-battery" }
```

**Response** `200 OK`
```json
{
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

`expires_in` is in seconds. Tokens expire after `jwt_expire_minutes` (default 24 h).

**Errors**
- `401 unauthorized` — wrong email or password (both produce the same error — callers cannot tell which was wrong; the server also spends the same amount of time either way, so response latency cannot be used to enumerate registered emails)
- `429 rate_limited` — too many requests from this client; see [Rate limiting](#rate-limiting)

---

### `GET /auth/me`

Fetch the authenticated user's profile. Useful to verify a token is still valid.

**Response** `200 OK`
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "email": "alice@example.com",
  "created_at": "2026-03-10T09:00:00Z"
}
```

---

## Watchlists

### `GET /watchlists`

List all watchlists owned by the authenticated user.

**Response** `200 OK`
```json
[
  {
    "id": "wl-uuid-here",
    "name": "Tech",
    "created_at": "2026-03-10T09:00:00Z",
    "items": [
      { "id": "item-uuid-here", "symbol": "AAPL", "created_at": "2026-03-10T09:01:00Z" },
      { "id": "item-uuid-here-2", "symbol": "MSFT", "created_at": "2026-03-10T09:01:05Z" }
    ]
  }
]
```

Empty list `[]` when no watchlists exist yet. `items` is `[]` for a watchlist
with no symbols added.

---

### `POST /watchlists`

Create a new watchlist.

**Request**
```json
{ "name": "Tech" }
```

Name rules: 1–90 characters, non-blank after trimming.

**Response** `201 Created`
```json
{
  "id": "wl-uuid-here",
  "name": "Tech",
  "created_at": "2026-03-10T09:00:00Z",
  "items": []
}
```

**Errors**
- `409 conflict` — a watchlist with this name already exists for this user

---

### `GET /watchlists/{watchlist_id}`

Get a single watchlist.

**Response** `200 OK` — same shape as the list item above.

**Errors**
- `404 not_found` — does not exist or belongs to another user

---

### `DELETE /watchlists/{watchlist_id}`

Delete a watchlist. Cascades to all symbols and the user's seen state for this watchlist.

**Response** `204 No Content`

---

### `POST /watchlists/{watchlist_id}/symbols`

Add a symbol to a watchlist. **Idempotent** — adding a symbol that is already present returns `201` (not an error).

**Request**
```json
{ "symbol": "aapl" }
```

Symbol is normalized to uppercase. Valid symbols: 1–10 chars, starts with a letter, letters/digits/`.`/`-` only.

**Response** `201 Created`
```json
{ "id": "item-uuid-here", "symbol": "AAPL", "created_at": "2026-03-10T09:01:00Z" }
```

Re-adding a symbol already on the watchlist also returns `201` with the
*existing* item (not an error) — see the note on idempotency above.

**Errors**
- `404 not_found` — watchlist does not exist or belongs to another user
- `422 validation_error` — symbol fails format validation

---

### `DELETE /watchlists/{watchlist_id}/symbols/{symbol}`

Remove a symbol from a watchlist.

**Response** `204 No Content`

**Errors**
- `404 not_found` — watchlist not found, or symbol is not in this watchlist

---

## Symbols (market data)

### `GET /watchlists/{watchlist_id}/symbols/{symbol}/quote`

Latest stored price observation for one symbol, with a live-computed freshness label.

The data comes from the worker's most recent ingestion — **not** a live provider call. API latency is therefore independent of provider availability. The `freshness` field tells the client exactly how current the data is.

**Response** `200 OK`
```json
{
  "symbol": "AAPL",
  "price": "191.580000",
  "volume": 52341200,
  "market_timestamp": "2026-03-11T15:30:00Z",
  "freshness": "FRESH",
  "ingest_freshness": "FRESH"
}
```

| Field | Meaning |
|---|---|
| `price` | Last traded price (NUMERIC, exact decimal) |
| `volume` | Shares traded; `null` when the provider did not report it |
| `market_timestamp` | When **the market** says this price held (never substituted with fetch time) |
| `freshness` | Computed **right now** against the correct reference point (last session close while market is closed) — `FRESH` / `DELAYED` / `STALE` |
| `ingest_freshness` | Freshness **when the worker wrote this row** — immutable |

**Freshness thresholds** (from `config.py`, overridable via `.env`):

| Class | Condition |
|---|---|
| `FRESH` | age ≤ 300 s (5 min) |
| `DELAYED` | 300 s < age ≤ 900 s |
| `STALE` | age > 900 s |

While the market is closed, age is measured from the last session close, not from `now`.

**Errors**
- `404 not_found` — no snapshot has been ingested for this symbol yet, or watchlist not found

---

### `GET /watchlists/{watchlist_id}/symbols/{symbol}/history`

Completed trading sessions (daily bars) for one symbol, most recent first.

**Query parameters**

| Param | Default | Range | Description |
|---|---|---|---|
| `limit` | `30` | 1–90 | Number of sessions to return |

**Response** `200 OK`
```json
[
  { "session_date": "2026-03-11", "close": "191.580000", "volume": 52341200 },
  { "session_date": "2026-03-10", "close": "185.240000", "volume": 48100000 },
  { "session_date": "2026-03-07", "close": "183.90000",  "volume": 41200000 }
]
```

Empty list `[]` when no bars have been ingested yet. `volume` may be `null` if the provider did not report it.

These are the same bars used for change-detection baselines (previous close and 20-day average volume), so what the user sees here directly matches the inputs that drove any events in their feed.

---

## Change feed

### `GET /watchlists/{watchlist_id}/changes`

What meaningfully changed since this user last checked this watchlist.

**Response** `200 OK`
```json
{
  "events": [
    {
      "id": 42,
      "symbol": "NVDA",
      "event_type": "PRICE_AND_VOLUME",
      "score": 82,
      "severity": "HIGH",
      "evidence": [
        "Price +8.2% vs previous close ($580.40 → $628.19)",
        "Volume 3.6× the 20-day average"
      ],
      "detected_at": "2026-03-11T14:05:00Z"
    }
  ],
  "first_visit": false,
  "last_seen_at": "2026-03-10T09:00:00Z"
}
```

| Field | Meaning |
|---|---|
| `events` | Ordered by `score DESC, detected_at DESC` — the highest-attention item leads |
| `first_visit` | `true` when the user has no prior seen state for this watchlist. The UI should frame this as "here's what's been happening" rather than "here's what changed while you were away" |
| `last_seen_at` | The user's current cursor. `null` on first visit |

**First-visit behavior:** events from the last 24 hours (configurable via `first_visit_lookback_hours`), capped at 20 (configurable via `first_visit_max_events`).

**Returning-user behavior:** events with `detected_at > last_seen_at`, capped at 100 (configurable via `change_feed_max_events`).

**Severity bands:**

| Band | Score range | Meaning |
|---|---|---|
| `WATCH` | 20–49 | Worth a look |
| `IMPORTANT` | 50–74 | Deserves attention |
| `HIGH` | 75–100 | Significant move — data quality verified |

**Event types:**

| Type | Driven by |
|---|---|
| `PRICE_MOVE` | Price signal only |
| `VOLUME_SPIKE` | Volume signal only |
| `PRICE_AND_VOLUME` | Both signals |

**Important invariant:** `HIGH` severity is provably unreachable for stale or conflicting data (multipliers cap the score below 75). Evidence strings explain exactly what drove the score using real numbers from the actual observation.

---

## Seen state

### `POST /watchlists/{watchlist_id}/seen`

Advance the user's cursor so events already rendered do not reappear.

**Idempotent and concurrency-safe:** calling this multiple times, or from two browser tabs simultaneously, always converges to the correct state (implemented via `GREATEST()` upsert in PostgreSQL — no read-then-write race).

**Request**
```json
{
  "seen_at": "2026-03-11T14:10:00Z",
  "last_seen_event_id": 42
}
```

Both fields are optional:
- Omit `seen_at` → server uses current time (mark everything seen right now)
- Omit `last_seen_event_id` → only the timestamp cursor advances
- Supply both → precise: "I have rendered through this specific event at this time"

`seen_at` is **clamped to at most now** — a client cannot push the cursor into the future and permanently suppress events that have not happened yet.

**Response** `200 OK`
```json
{
  "watchlist_id": "wl-uuid-here",
  "last_seen_at": "2026-03-11T14:10:00Z",
  "last_seen_event_id": 42
}
```

---

## Ops

### `GET /health`

Liveness check. Returns `200` if the process is running, regardless of dependency state.

```json
{ "status": "ok", "service": "smart-market-watchlist" }
```

> This endpoint must never touch a database or external service. An orchestrator (Kubernetes, ECS) uses it to decide whether to kill and restart the process, not whether to route traffic.

---

### `GET /ready`

Readiness check. Returns `200` when all dependencies are reachable; `503` when degraded.

**`200 OK` — all dependencies healthy:**
```json
{ "status": "ready", "checks": { "database": "ok" } }
```

**`503 Service Unavailable` — database unreachable:**
```json
{ "status": "degraded", "checks": { "database": "error" } }
```

An orchestrator uses this to decide whether to route traffic to the instance. A degraded instance should be excluded from the load-balancer pool until `/ready` recovers.

> The market data provider is **not** checked here. It runs asynchronously in the worker, not in the API request path — a momentarily unavailable provider does not make the API unready.

---

## Pagination

v1 has no cursor-based pagination. All list endpoints are bounded by configured caps (`change_feed_max_events`, `first_visit_max_events`) or query-parameter limits (`history?limit=30`). Unbounded results are not possible.

---

## Rate limiting

`/auth/register` and `/auth/login` — the only endpoints reachable before any
identity exists, and therefore the pair a credential-stuffing or
registration-spam script would target — carry an IP-keyed, fixed-window
rate limit: **10 requests per 60 seconds** by default
(`AUTH_RATE_LIMIT_MAX_REQUESTS` / `AUTH_RATE_LIMIT_WINDOW_SECONDS`), shared
across both endpoints per client.

Exceeding it returns:

```json
{ "error": { "code": "rate_limited", "message": "Too many requests", "details": { "retry_after_seconds": 42 } } }
```
`429 Too Many Requests`, with a `Retry-After` header (seconds) a client
should honor before retrying.

Every other endpoint requires authentication and has no additional rate
limit in v1 — the auth boundary is the abuse surface that matters most before
an account exists.

The limiter is in-memory and per-process (see
[design-decisions.md](../docs/design-decisions.md) #12) — it resets on a
deploy/restart, and does not share state across multiple API instances. This
matches the current single-instance deployment; a distributed limiter
(Redis-backed) is the natural upgrade if that changes.

---

## Request size limit

Every payload this API accepts is a small JSON object. A request whose
`Content-Length` exceeds `MAX_REQUEST_BODY_BYTES` (default **1,000,000
bytes**) is rejected with `413` before the body is read:

```json
{ "error": { "code": "request_too_large", "message": "Request body exceeds the 1000000-byte limit", "details": {} } }
```

This is a backstop, not the primary defense — a reverse proxy or load
balancer in front of this API in production should reject an oversized
request before it reaches this process at all.

---

## Versioning

v1 has no URL versioning prefix. A breaking change will be versioned at that time. The OpenAPI schema is available at `/docs` (Swagger UI) and `/openapi.json`.
