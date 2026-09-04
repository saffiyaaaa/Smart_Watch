# Product Contract — Smart Market Watchlist

**Status:** v1, Phase 0 output. Everything downstream (schema, scoring, tests) derives from this document. If a number appears in code and not here, that is a bug.

---

## 1. The problem being solved

A watchlist that shows prices answers "what is the price?". Nobody needs that; every broker app does it.

This system answers a harder question: **"I looked at this yesterday. What actually changed, what deserves my attention, and why?"**

That reframing forces three concepts apart, and keeping them apart is the core of the design:

| Concept | What it is | Who owns it |
|---|---|---|
| **Market fact** | An observation that a price was X at time T, per a named source | The market. Immutable. |
| **Derived event** | A judgement that a fact was *significant*, with a score and evidence | The system. Recomputable. |
| **Seen state** | A per-user cursor over derived events | The user. Independent per user. |

Collapsing any two of these is the main way this product fails. A "last seen" column on a stock row would mean two users can't have different views of the same market. Overwriting a price row would destroy the history that makes change detection possible.

---

## 2. Definitions

Every term the brief left ambiguous, given an exact meaning.

### Snapshot
One validated observation: `(source, symbol, price, volume, market_timestamp, fetched_at, ingest_freshness)`.

- **Immutable and append-only.** There is no code path that updates or deletes a snapshot. This is enforced by the repository having no such methods, not by convention.
- `market_timestamp` is when the *market* says the price held. `fetched_at` is when *we* asked. These are never substituted for one another — that substitution is exactly how stale data gets laundered into fresh data.
- Identity is `(source, symbol, market_timestamp)`. Two fetches returning the same observation are the same fact, not two facts.

### Trading session
US equities, `America/New_York`: Mon–Fri, 09:30–16:00. **v1 has no holiday calendar** — on a market holiday the system believes the market is open, so quotes read as DELAYED/STALE rather than fresh. This errs toward *under*-confidence, which is the safe direction, and is a documented v1 limitation rather than an oversight.

### Freshness
Computed **at read time**, against a reference point that accounts for closed markets:

```
reference = now                  if market currently open
          = last_session_close   otherwise
age = reference - market_timestamp
```

| Class | Condition |
|---|---|
| `FRESH` | `age <= 300s` (5 min) |
| `DELAYED` | `300s < age <= 900s` |
| `STALE` | `age > 900s` |

The closed-market reference is what stops the entire watchlist from screaming STALE all weekend over perfectly good Friday-close data.

`ingest_freshness` is also stored on the snapshot — that is the freshness *at the moment we ingested it*, an immutable fact about the observation. Display freshness is computed live. Both exist because they answer different questions: "was this ever fresh?" vs "is this fresh now?".

### Baselines

**Price baseline = previous session close**, read from `daily_bars`. This is the universal meaning of "% change today", it is stable across the whole session, and it does not drift as intraday snapshots accumulate.

> Rejected alternative: baseline = the immediately preceding snapshot. That looks appealing but is broken — a stock that climbs 1% per tick would trigger nothing while moving 6%, because each individual step is below threshold. Comparing to a fixed daily anchor catches the cumulative move.

**Volume baseline = trailing 20-session average volume**, from `daily_bars`, excluding the current session. Fewer than 5 sessions of history available → volume signal is unavailable, not zero (these are different, and treating "unknown" as "normal" would be a lie).

### Meaningful change
A `change_event` with `attention_score >= 20`. Signals below 20 are computed but **not** persisted as events and never surface. "Meaningful" is a threshold on a documented formula, not a vibe.

### Stale data
Data classified `STALE` per above. Policy: **store it, serve it, always label it, and cap its influence.** Never discard it (last-known-good beats nothing) and never present it as current.

### Conflicting data
Two snapshots sharing `(symbol, market_timestamp)` from *different* sources whose prices differ by more than **0.5%**.

Honest v1 note: with a single provider this condition cannot fire in production. It is implemented and unit-tested so that adding a second provider is a configuration change rather than a redesign, and so the confidence machinery is proven before it is needed.

### Last-seen state
Per `(user_id, watchlist_id)`: `last_seen_at` plus `last_seen_event_id`.

**Advances forward only.** Enforced in SQL via `GREATEST(existing, incoming)` on upsert, not in application code — application-level compare-then-write loses the race between two browser tabs.

---

## 3. The user-facing question, answered precisely

> "What changed since **I** last checked?"

Resolves to: **change events whose `detected_at` is after this user's `last_seen_at`, for symbols in this watchlist, scored ≥ 20, ranked by score then recency.**

Note carefully what this does *not* mean. Events are **user-agnostic** — the system detects "NVDA moved 6% vs its previous close" once, as a fact about the market, not once per user. The per-user dimension is purely *which events fall after that user's cursor*.

This is why `change_events` has no `user_id`, and why it must not acquire one. Ten thousand users watching NVDA produce one event and ten thousand cursors, not ten thousand events. It is also what lets the worker run without knowing users exist.

Consequence for wording: evidence reads **"vs previous close"**, not "since your last check" — because that is what was actually measured. Claiming otherwise would be a small lie that a user could catch.

### First-time user
No seen state → return events from the **last 24 hours**, capped at 20, with `first_visit: true` so the UI can frame it as "here's what's been happening" rather than "here's what changed while you were gone".

### Event deduplication
**At most one event per `(symbol, trading_day)`**, enforced by a unique constraint. Re-running the worker on the same day updates the existing event only if the new score is **higher**.

Without this, a worker on a 5-minute tick would generate 78 near-identical "NVDA is up" events per day and the feed would be worthless. Escalation refreshes `detected_at`, which deliberately re-surfaces the event to users who already saw it — if a 3% move became a 9% move, that is new information and they should see it again.

### Out-of-order observations
An observation older than the newest stored for that symbol is **stored** (history has value) but does **not** trigger change detection and never becomes "latest". "Latest" means `MAX(market_timestamp)`, never `MAX(id)` — arrival order is not truth.

---

## 4. Scoring model (v1)

Deterministic, explainable, and tunable by test rather than by argument. No ML, no LLM — neither would survive the question "why was this flagged?", and that question is the product.

### Components

```
price_points  = 0                                      if |pct| < 1.0
              = 55 × clamp((|pct| − 1.0) / 7.0, 0, 1)  otherwise

volume_points = 0                                      if ratio < 1.5 or unavailable
              = 45 × clamp((ratio − 1.5) / 3.5, 0, 1)  otherwise

raw = price_points + volume_points          # 0–100
```

So price saturates at an 8% move, volume at 5× average. Price is weighted slightly heavier (55 vs 45) because a large price move is self-evidently relevant, whereas volume without price movement is often mechanical (index rebalancing, options expiry).

### Confidence multipliers

| Condition | Multiplier |
|---|---|
| `FRESH` | 1.00 |
| `DELAYED` | 0.85 |
| `STALE` | 0.60 |
| Conflicting sources | 0.50 |
| Volume baseline unavailable | 0.85 |

Multipliers compose. `final = round(raw × Π multipliers)`, clamped 0–100.

### Severity

| Band | Severity |
|---|---|
| 0–19 | `NORMAL` (not persisted, not surfaced) |
| 20–49 | `WATCH` |
| 50–74 | `IMPORTANT` |
| 75–100 | `HIGH` |

**Load-bearing property:** the worst possible raw score of 100 becomes 60 when stale and 50 when conflicting — both below the 75 HIGH floor. **Untrustworthy data provably cannot produce a high-confidence alert.** This is asserted in tests, not hoped for.

Scoped precisely: the invariant covers `STALE` and `CONFLICTING`. `DELAYED` (×0.85) *can* still reach HIGH, at a worst case of 85 — and that is intentional. Five-to-fifteen-minute latency is normal for free market data; suppressing a genuine 8% move on a 10-minute-old quote would be a false negative, which is the more expensive error here. `DELAYED` shades confidence; it does not veto.

### Calibration sanity check

| Scenario | Score | Severity |
|---|---|---|
| +0.8%, normal volume | 0 | not surfaced |
| +3.5%, normal volume | 20 | WATCH |
| +2.0%, 3× volume | 27 | WATCH |
| +6.0%, 2× volume | 46 | WATCH |
| +8.0%, 3.6× volume | 82 | HIGH |
| +8.0%, 3.6× volume, **stale** | 49 | WATCH |

The last two rows are the whole point: identical market movement, different data quality, correctly different treatment.

### Evidence
Every persisted event carries human-readable strings with real numbers:

- `"Price +6.2% vs previous close ($180.40 → $191.58)"`
- `"Volume 3.1× the 20-day average"`
- `"Quote is 42 minutes old — confidence reduced to 60%"`

The frontend renders these **verbatim**. It computes no percentages and no severities. A user asking "why is this flagged?" gets an answer produced by the same code that made the decision, so the explanation cannot drift from the reasoning.

---

## 5. MVP user journey

1. Register → log in
2. Create a watchlist
3. Add symbols (idempotent — adding `aapl` twice yields one `AAPL`)
4. See latest known state, each row labeled with its freshness
5. Leave
6. Worker ingests snapshots, detects changes, writes events
7. Return → **"Changed since you last checked"** is the top surface, prices are secondary
8. Read the evidence behind each flagged item
9. Mark seen → cursor advances → those events do not reappear

---

## 6. v1 signals

**In:** price % vs previous close · volume vs 20-day average · data-quality confidence.

**Out:** news sentiment, corporate actions, technical indicators, cross-symbol correlation, sector-relative moves.

Each excluded signal is excluded because it adds a data dependency and a failure mode without proving the core thesis. The architecture accepts new signals as additional `Signal` producers feeding the same scorer, so adding one later is additive rather than structural.

---

## 7. Non-goals

Trading or order placement · portfolio/P&L tracking · real-time streaming (WebSocket/SSE) · ML or LLM scoring · multi-provider reconciliation beyond conflict flagging · mobile apps · social features · options, crypto, forex.

These are non-goals because each would add complexity that the core question does not require. If one becomes necessary, it needs a written justification first — per the brief's principle that every technology must earn its existence.

---

## 8. Failure-mode matrix

Each row gets a passing test by Phase 11. `reliability.md` will map every row to its test.

| # | Failure | Expected behavior |
|---|---|---|
| 1 | Provider timeout | Bounded retry (3, exponential backoff + jitter). On exhaustion: no write, keep last valid snapshot, surface freshness state. Never hangs the request path. |
| 2 | Provider 5xx | Retry per policy. Never persist a partial or invalid observation. |
| 3 | Rate limited (429) | Back off, honor `Retry-After` if present, skip this cycle. No retry storm. |
| 4 | Malformed response | Reject the observation, log a validation failure with the raw payload, write nothing. |
| 5 | Stale timestamp | Persist with `ingest_freshness=STALE`, serve labeled, cap severity via the 0.6 multiplier. |
| 6 | Conflicting sources | Flag conflict, apply the 0.5 multiplier. Documented precedence: most recent `fetched_at` wins for display. |
| 7 | Duplicate observation | `ON CONFLICT (source, symbol, market_timestamp) DO NOTHING`. Silently idempotent. |
| 8 | Out-of-order observation | Stored as history; does not trigger detection; never becomes "latest". |
| 9 | Concurrent add-symbol | `UNIQUE (watchlist_id, symbol)` + `ON CONFLICT DO NOTHING`. Exactly one row, both callers get success. |
| 10 | Concurrent mark-seen | `GREATEST()` upsert. Monotonic, converges, replay-safe. |
| 11 | Database unavailable | Controlled `503` with the standard error envelope. Never a false success. `/ready` reports degraded. |
| 12 | Frontend network failure | Retryable error surface; previously rendered data stays on screen rather than blanking. |
| 13 | Empty watchlist | Explicit empty state with an add-symbol action. |
| 14 | No meaningful changes | Explicit **"Nothing significant changed since your last visit"** — distinct from the empty-watchlist state and from an error. Silence must be affirmative, not blank. |
| 15 | Unknown/delisted symbol | Rejected at add time where possible; if it later stops resolving, the item persists and shows "no data available". |
| 16 | Insufficient volume history | Volume signal marked *unavailable*, not zero. 0.85 confidence multiplier applied. |

---

## 9. Data-model additions beyond the brief

Two deliberate departures from §5 of the original brief, both forced by requirements stated elsewhere in it:

1. **`daily_bars` table** (`symbol, session_date, close, volume, source`, unique on `(source, symbol, session_date)`). The brief's §6 requires a 20-day average volume baseline and a previous close, and neither can be derived from intraday snapshots on day one — there is no history yet. This table is where that history lives.

2. **`trading_day` column on `change_events`**, backing `UNIQUE (symbol, trading_day)`. This is what makes deduplication a database guarantee instead of application logic, and it is what makes "refreshing does not duplicate events" structurally true.

Both are additive. Neither changes the meaning of any entity the brief defined.

---

## 10. Phase 0 gate

- [x] Every ambiguous term has a number or formula, not a description
- [x] 16 failure cases enumerated with expected behavior (brief required ≥10)
- [x] End-to-end flow traceable from provider call to rendered UI with no guessing
- [x] Scoring model is deterministic and hand-checkable — the calibration table can be verified with a calculator
- [x] The degraded-data-cannot-reach-HIGH property is stated as a testable invariant
- [x] Departures from the brief are named and justified rather than silent
