# Reliability: failure modes mapped to tests

Every row in docs/product-spec.md section 8's failure-mode matrix, mapped to
the test(s) that prove it. Where a row's coverage has a real gap, that gap is
stated rather than papered over — an honest map is the point of this
document existing at all.

All backend test paths are relative to `backend/tests/`. All frontend paths
are relative to `frontend/src/`.

| # | Failure | Proven by | Notes |
|---|---|---|---|
| 1 | Provider timeout | `unit/test_retry.py::TestSuccessPaths.test_provider_unavailable_retried_up_to_the_limit`, `TestBackoffSchedule.test_delay_grows_exponentially`, `test_jitter_is_added_on_top_of_the_base_delay`, `TestTimeouts.test_timeout_becomes_provider_timeout`, `test_timeout_is_retried`, `TestConcurrencyDoesNotBlockTheEventLoop.test_a_hanging_call_does_not_block_other_tasks`; `integration/test_ingestion.py::TestProviderTimeoutLeavesDataIntact.test_previous_valid_snapshot_survives_a_timeout`, `test_no_snapshot_is_written_on_timeout` | Bounded retry, backoff+jitter, no write on exhaustion, last valid snapshot survives, event loop never blocks. |
| 2 | Provider 5xx | `unit/test_retry.py::TestSuccessPaths` (5xx modeled as `ProviderUnavailable`, same retry path as timeout); `integration/test_error_handling.py::TestInvalidProviderDataIsRejected.test_provider_unavailable_stops_ingestion_without_crashing`; `integration/test_ingestion.py::TestProviderTimeoutLeavesDataIntact.test_provider_unavailable_behaves_the_same_way` | Same retry policy as row 1; no partial write. |
| 3 | Rate limited (429) | `unit/test_retry.py::TestRateLimiting.test_retry_after_is_honoured_instead_of_exponential_backoff`, `test_rate_limit_without_retry_after_uses_default_backoff`, `test_rate_limit_exhausts_retries_like_any_other_failure` | `Retry-After` honoured when present, default backoff otherwise. **Gap:** no test proves "no retry storm" at the batch level specifically for a 429 (batch isolation in `test_ingestion.py::TestBatchIsolation` uses a generic provider error, not 429) — low risk, since the underlying retry policy is identical regardless of which error triggered it. |
| 4 | Malformed response | `integration/test_error_handling.py::TestInvalidProviderDataIsRejected.test_negative_price_is_rejected_at_model_layer`, `test_future_timestamp_is_rejected_at_model_layer`, `test_naive_timestamp_is_rejected_at_model_layer`, `test_invalid_provider_data_exception_stops_ingestion` | Rejected at the `Quote`/`Bar` pydantic model boundary; ingestion writes nothing. **Gap:** no `caplog` assertion proves the validation-failure log line includes the raw payload — the safety-critical half (reject, write nothing) is proven; the observability half is implemented (`logger.exception` in `worker/ingestion.py`) but not asserted on. |
| 5 | Stale timestamp | `integration/test_ingestion.py::TestStaleDataIsMarkedNotHidden.test_stale_quote_is_persisted_and_labeled_stale`, `test_stale_data_is_never_silently_relabeled_fresh`; `unit/test_freshness.py::TestClassifyFreshness`; `unit/test_scoring.py::TestConfidenceMultiplier.test_stale_applies_its_multiplier`, `TestLoadBearingInvariant.test_stale_worst_case_stays_below_high`; `integration/test_change_detection_pipeline.py::TestStaleDataProducesADegradedEvent.test_stale_quote_that_would_be_high_is_capped_below_it` | Persisted labeled `STALE`; 0.6 confidence multiplier is enforced end to end, including the load-bearing invariant that stale data can never reach HIGH severity (also enforced at startup by `config.py`'s `_check_invariants`). |
| 6 | Conflicting sources | `unit/test_detection.py::TestDetectConflict`; `unit/test_scoring.py::TestConfidenceMultiplier.test_conflicting_applies_its_multiplier`, `TestLoadBearingInvariant.test_conflicting_worst_case_stays_below_high`; `unit/test_mock_providers.py::TestConflictingProvider`; `integration/test_stocks_api.py::TestGetQuote.test_conflicting_sources_at_the_same_instant_break_ties_by_fetched_at` | **Fixed during this review:** `get_latest`/`get_latest_for_symbols` ordered only by `market_timestamp`, with no tie-breaker for two sources reporting the identical instant — the documented "most recent `fetched_at` wins for display" precedence was not actually implemented (latent, since a single-provider deployment never produces the tie). Both queries now order by `market_timestamp DESC, fetched_at DESC`; the new test fails against the old ordering and passes against the fix. Conflict *detection* and its 0.5 multiplier were already correct; this was specifically about *display precedence* once a conflict exists. |
| 7 | Duplicate observation | `integration/test_constraints.py::TestSnapshotIdentity.test_duplicate_observation_rejected`; `integration/test_ingestion.py::TestDuplicateObservationsDoNotDuplicate`; `integration/test_concurrency.py::TestConcurrentSnapshotIngestion.test_simultaneous_identical_observations_create_exactly_one_snapshot` (added during this review — a genuine multi-threaded race on the exact same observation, not just a single-session constraint check); `integration/test_query_plans.py::test_ingest_conflict_check_uses_the_uniqueness_index` | `ON CONFLICT DO NOTHING` on `uq_market_snapshots_observation`, proven both sequentially and under real concurrency. |
| 8 | Out-of-order observation | `integration/test_ingestion.py::TestOutOfOrderObservations`; `unit/test_detection.py::TestIsNewObservation`; `integration/test_change_detection_pipeline.py::TestOutOfOrderNeverTriggersDetection.test_an_older_arrival_after_a_newer_one_does_not_create_an_event` | Stored as history, never overwrites latest, never triggers detection. |
| 9 | Concurrent add-symbol | `integration/test_concurrency.py::TestConcurrentAddSymbol` (12 real threads on real connections, with a barrier forcing genuine overlap) | Exactly one row; every caller gets success, not one winner and eleven `IntegrityError`s. |
| 10 | Concurrent mark-seen | `integration/test_change_feed_concurrency.py::TestConcurrentMarkSeen` | `GREATEST()` upsert converges regardless of arrival order. |
| 11 | Database unavailable | `integration/test_error_handling.py::TestDatabaseFailureProduces503`; `unit/test_health.py` | `/ready` returns 503 with `status: degraded`; `/health` never touches the database. |
| 12 | Frontend network failure | `frontend/components/ErrorBanner.tsx` + `pages/WatchlistDetailPage.tsx`, `pages/WatchlistsPage.tsx` | No automated frontend tests exist (no test files under `frontend/src/`) — this is implementation-verified, not test-verified. Verified during this review that the full-page error branch (keyed off TanStack Query's `isError`) cannot fire once a query has ever successfully returned data: v5's `status` only becomes `'error'` when `data` is `undefined`, so a background refetch failure leaves previously rendered data on screen exactly as required, and the full-page `ErrorBanner` is reachable only on a genuine first-load failure. |
| 13 | Empty watchlist | `frontend/pages/WatchlistDetailPage.tsx` (`items.length === 0` branch), `pages/WatchlistsPage.tsx` ("No watchlists yet" branch) | Implementation-verified; no automated frontend test. |
| 14 | No meaningful changes | `frontend/pages/WatchlistDetailPage.tsx` (`!hasEvents` branch, "Nothing significant changed since your last visit") | Implementation-verified; no automated frontend test. |
| 15 | Unknown/delisted symbol | Add-time format rejection: `integration/test_constraints.py::TestSymbolFormat`, `integration/test_watchlist_api.py::TestSymbolManagement.test_invalid_symbol_rejected_with_422`. Later-stops-resolving: `integration/test_error_handling.py::TestInvalidProviderDataIsRejected.test_symbol_not_found_stops_ingestion_without_crashing`; `unit/test_mock_providers.py`; `integration/test_yfinance_provider.py` (network-gated). Persists with no-data display: `integration/test_stocks_api.py::TestGetQuote.test_returns_404_when_no_snapshot_exists`; `frontend/pages/WatchlistDetailPage.tsx` ("No market data yet") | Add-time rejection is *format* validation only (`AAPL` shape, not "does this actually resolve") — the brief's "rejected at add time where possible" is honestly only partial: this system does not call the provider synchronously on add, by the same design choice documented in `app/api/routes/stocks.py` that keeps provider calls out of the request path. A symbol that is syntactically valid but does not exist is caught the first time the worker tries to ingest it, not at add time. |
| 16 | Insufficient volume history | `unit/test_detection.py::TestAverageVolume`, `TestComputeSignals.test_insufficient_volume_history_degrades_not_crashes`; `unit/test_scoring.py::TestConfidenceMultiplier.test_missing_volume_baseline_applies_its_multiplier`, `TestBuildEvidence.test_missing_volume_baseline_is_mentioned` | Returns `None` (unavailable), never `0`; 0.85 confidence multiplier applied. |

## Concurrency and idempotency (Phase 11)

Beyond the matrix rows above, the following are exercised with real threads on
real PostgreSQL connections (a single shared session serialises work by
definition, so these cannot be proven any other way — see
`integration/test_concurrency.py`'s module docstring):

- Concurrent symbol registration: `TestConcurrentRegistration` — one winner,
  every other caller gets a clean `409`, never a raw `IntegrityError`.
- Concurrent identical-observation ingestion (row 7, above): added during
  this review to close the gap between the documented claim in
  `insert_snapshot`'s docstring ("resolves correctly even across separate
  worker processes") and what was actually under test — previously only a
  single-session `db.flush()` constraint check existed.

## Worker concurrency and the quote cache (Phase 12)

Added during this review:

- `worker/ingestion.py`'s `ingest_all` now fetches up to
  `WORKER_SYMBOL_CONCURRENCY` symbols' quotes and daily history concurrently
  (bounded by an `asyncio.Semaphore`), instead of one symbol at a time.
  Network I/O overlaps; all `db` writes still go through the one `Session`
  passed in, serialised by a shared `asyncio.Lock` around each db-touching
  phase so genuine concurrency never means two coroutines inside the same
  `Session` at once. Proven in
  `integration/test_ingestion.py::TestIngestAllConcurrency`: the concurrency
  bound is respected (`max_concurrent <= worker_symbol_concurrency`), genuine
  overlap occurs (`max_concurrent > 1`), and a concurrent run is
  meaningfully faster than a sequential one for the same work.
- `app/infrastructure/cache/quote_cache.py` (`QuoteCache`) — a Redis-backed,
  short-TTL cache in front of `provider.get_quote`, off by default
  (`CACHE_ENABLED=false`). Nothing in the request path ever calls a
  provider (see `app/api/routes/stocks.py`), so this only matters if an
  ingestion cycle re-runs inside the TTL window. Fails open on every error
  path (unreachable Redis, corrupted value) — a cache must never become a
  new way for ingestion to fail. Proven with an injectable fake client in
  `unit/test_quote_cache.py` (logic: TTL pass-through, fail-open, disabled
  short-circuit) and against a real Redis instance in
  `integration/test_quote_cache_redis.py` (round trip, real expiry;
  `@redis_required`, skips when Redis is unreachable). The worker-level
  effect — a second `ingest_symbol` call within the TTL skips the provider
  entirely — is proven in
  `integration/test_ingestion.py::TestQuoteCacheAvoidsRepeatedProviderCalls`.

## Security review (Phase 13)

| Area | Finding | Status |
|---|---|---|
| Password storage | bcrypt, cost 12, explicit 72-byte truncation rejection (silent truncation would let two different long passwords collide), constant-time comparison via `bcrypt.checkpw`, and a dummy-hash check (`burn_password_time`) on the user-not-found path so login timing cannot be used to enumerate registered emails. | Reviewed, no change needed. See `app/infrastructure/security.py`. |
| JWT handling | Algorithm is explicitly whitelisted on decode (`algorithms=[settings.jwt_algorithm]`), not inferred from the token header — the standard mitigation for an algorithm-confusion attack. Token carries an explicit `type: "access"` claim so a future token type cannot be replayed as this one. User is re-loaded from the database on every request rather than trusted from the token body, so a deleted account cannot keep operating on a still-valid token. | Reviewed, no change needed. |
| Authorization | Every watchlist-scoped route resolves ownership through `watchlist_service.get_watchlist`, which collapses "belongs to someone else" and "does not exist" into the same 404 — deliberately, so an id cannot be confirmed as real by a 403 response. Exercised for every route in `integration/test_watchlist_api.py`, `test_stocks_api.py`, `test_change_feed_api.py`. | Reviewed, no change needed. |
| SQL construction | No string-interpolated SQL anywhere in `app/` or `worker/` (`grep` for `f"..SELECT`/`INSERT`/`UPDATE`/`DELETE` and `text(f"` returns nothing). Every raw `text()` use in the test suite and Alembic migrations takes bound parameters. | Reviewed, no change needed. |
| Secrets | `.env` is gitignored; `.env.example` documents every field with safe placeholder values and is the only one committed. | Reviewed, no change needed. |
| Rate limiting | **Added during this review.** `/auth/register` and `/auth/login` — the only endpoints reachable before any identity exists, and therefore the ones a credential-stuffing or registration-spam script would target — now carry an IP-keyed, in-memory fixed-window limit (`AUTH_RATE_LIMIT_MAX_REQUESTS` per `AUTH_RATE_LIMIT_WINDOW_SECONDS`, default 10/60s). Exceeding it returns `429` with a `Retry-After` header. In-memory and therefore per-process, matching this system's single-process worker (see `worker/scheduler.py`'s own docstring on why it isn't Celery/Redis) — nothing in this system's stated scope runs more than one API instance; the natural upgrade path if that changes is a Redis `INCR`+`EXPIRE` window, reusing the Redis already provisioned for the Phase 12 quote cache rather than adding a new dependency. See `app/infrastructure/rate_limit.py`; proven in `unit/test_rate_limit.py` (including a genuine multi-threaded race, since FastAPI runs sync route handlers in a worker thread pool) and `integration/test_auth_api.py::TestRateLimiting`. |
| Request size | **Added during this review.** Every payload this API accepts is a small JSON object; a `Content-Length` above `MAX_REQUEST_BODY_BYTES` (default 1 MB) is now rejected with `413` before the body is read, via `MaxBodySizeMiddleware`. This is a backstop, not the primary defense — a reverse proxy or load balancer in front of this API in a real deployment should reject an oversized request before it reaches this process at all. See `app/api/middleware.py`; proven in `integration/test_auth_api.py::TestRequestSizeLimit`. |
| Dependency audit | `pip-audit` flags one transitive vulnerability: `ecdsa` 0.19.2 (pulled in unconditionally by `python-jose`, regardless of the `[cryptography]` extra), a known, upstream-acknowledged timing side-channel in the pure-Python ECDSA implementation with no fixed version available (the maintainers consider constant-time guarantees out of scope for that implementation). **Not currently exploitable in this system**: `JWT_ALGORITHM` defaults to `HS256` (HMAC), which never touches the ECDSA code path. **Accepted risk, monitored**: do not set `JWT_ALGORITHM` to an ES* value until this is resolved upstream or `python-jose` is replaced; re-run `pip-audit` before any production deployment. |
| Sensitive data in logs | No log call anywhere in `app/` or `worker/` includes a password, token, or full request body (`grep` for `logger\.` calls near those terms returns nothing) — every log call names an id, a symbol, or a status, never a credential. | Reviewed, no change needed. |

## Deployment (Phase 14): three real failures, none of them in the test suite

The failure-mode matrix above is what the *test suite* proves. Deploying to
Vercel + Render + Neon surfaced three more failures that no unit or
integration test could have caught, because each one is specific to real
managed infrastructure a local Postgres container and a mock provider don't
reproduce. Recorded here because "the tests all pass" and "the system
survives contact with a real cloud deployment" are different claims, and
conflating them is exactly how a reliability document becomes dishonest.

| # | Failure | Root cause | Fix |
|---|---|---|---|
| 17 | `ModuleNotFoundError: No module named 'psycopg2'` on boot | Every managed Postgres provider hands out a bare `postgresql://` connection string; SQLAlchemy's default dialect for that scheme is psycopg2, which this project does not install (it installs psycopg v3). | `config.py`'s `_normalize_database_url_driver` rewrites `postgresql://`/`postgres://` to `postgresql+psycopg://` at settings load. See [design-decisions.md](design-decisions.md) #4. Regression-tested in `unit/test_config.py::TestDatabaseUrlDriverNormalization`. |
| 18 | `/health` succeeded, `/ready` reported `database: error`, indefinitely | The persistent connection pool set its statement timeout via a startup-packet `options` parameter, which Neon's pooled endpoint (PgBouncer) silently refused to forward — while Alembic's one-off migration connection (no such parameter) connected to the identical endpoint successfully. | Set the timeout via a `SET statement_timeout = ...` command on connect instead — an ordinary query, not a startup parameter. See [design-decisions.md](design-decisions.md) #13. Regression-tested in `integration/test_session.py`, including a locked-in assertion that `connect_args` never reintroduces an `options` parameter. |
| 19 | Every symbol except a handful showed "no market quote yet," indefinitely | The API's `/quote` endpoint only ever reads the database (decision #7, by design) — the worker is what populates it. Only the API web service was deployed; the worker was never deployed as its own service, so nothing was ever ingesting data for newly added symbols. | `worker/Dockerfile`, built from the repository root so it can include both `backend/app` and the sibling `worker/` package, deployed as a second, separate Render Background Worker service. See [design-decisions.md](design-decisions.md) #11. Verified locally before deploying: built the image, ran one ingestion cycle against real PostgreSQL with both `MockProvider` and the real `yfinance` provider, confirmed snapshots were persisted. |

None of these are gaps in the failure-mode matrix or the test suite — rows
1–16 above are about how the *running system* behaves once it is up; rows
17–19 are about whether the system as *deployed* is the system that was
*built*. The lesson generalizes: a docker-compose-based local environment
and a suite of tests against a local PostgreSQL container do not exercise a
managed provider's connection pooler, a split-service deployment topology,
or a copy-pasted connection string's exact scheme — each of those needed a
real deployment to surface, and each was fixed and verified against the real
infrastructure it failed on, not just against the local test suite.

## A test fixture that only failed on weekends

One more finding, caught by the test suite itself rather than by deploying:
`mock_provider.py`'s `StaleProvider` computed its quote's `market_timestamp`
as `now - 2 hours`, intended to reliably classify as `STALE` under the
15-minute threshold. It does — on a weekday. `classify_freshness` measures
staleness from `now` only while the market is open; while it's closed, the
reference point is the *most recent session close* (see
`app/domain/market/freshness.py`'s `freshness_reference`), specifically so a
perfectly good Friday-afternoon price doesn't read as degraded all weekend
(product-spec.md §2). On a weekend, that reference can sit up to ~65.5 hours
behind the real current time — comfortably more than the 2-hour offset the
fixture assumed — so `now - 2h` was still *after* Friday's close, producing
a negative age and classifying as `FRESH` instead of `STALE`.

This surfaced mid-session, three tests deep
(`test_ingestion.py::TestStaleDataIsMarkedNotHidden`,
`test_change_detection_pipeline.py::TestStaleDataProducesADegradedEvent`),
on the first weekend this codebase's test suite happened to run since the
fixture was written — the exact "passes in CI for months, fails the first
time someone runs it on a Saturday" shape of bug. Fixed by widening
`STALE_AGE` to four days, comfortably exceeding the largest gap
`freshness_reference` can introduce under any real calendar (including a
long weekend), rather than by freezing time in the test (which would have
hidden the fixture's actual assumption instead of correcting it). Verified
by reproducing the failure against the old value and confirming the fix
against the new one, not just by watching it pass once.

The broader point for how testing was done in this project: a fixture using
real wall-clock time (`datetime.now(UTC)`, as `StaleProvider` does) inherits
every calendar-dependent edge case the *system* has to handle — which is
appropriate when the fixture's job is to prove the system handles that edge
case, but means the fixture itself has to be as careful about the calendar
as the code it's testing. Fixtures that need a *specific* day (weekday
arithmetic, trading-session boundaries) use a frozen constant instead — see
`tests/fixtures/seed.py`'s `FIXED_NOW`, deliberately a Wednesday specifically
so "previous session" arithmetic never crosses a weekend by accident.
