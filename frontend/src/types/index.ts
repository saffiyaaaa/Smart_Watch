/** TypeScript interfaces that mirror the Phase 9 API schemas exactly.
 *
 * No derived fields, no computed properties -- the backend is the single
 * source of truth for severity, freshness, and scores. The frontend
 * renders what it receives.
 */

export interface User {
  id: string
  email: string
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
}

// items mirrors backend WatchlistItemResponse
export interface WatchlistItem {
  id: string
  symbol: string
  created_at: string
}

export interface Watchlist {
  id: string
  name: string
  created_at: string
  items: WatchlistItem[]
}

/** GET /watchlists/{id}/symbols/{symbol}/quote */
export interface QuoteResponse {
  symbol: string
  price: string        // Decimal serialised as string
  volume: number | null
  market_timestamp: string
  freshness: 'FRESH' | 'DELAYED' | 'STALE'
  ingest_freshness: 'FRESH' | 'DELAYED' | 'STALE'
}

/** One row from GET /watchlists/{id}/symbols/{symbol}/history */
export interface DailyBar {
  session_date: string
  close: string
  volume: number | null
}

/** One entry in the change feed */
export interface ChangeEvent {
  id: number
  symbol: string
  event_type: 'PRICE_MOVE' | 'VOLUME_SPIKE' | 'PRICE_AND_VOLUME'
  score: number
  severity: 'WATCH' | 'IMPORTANT' | 'HIGH'
  evidence: string[]
  detected_at: string
}

/** GET /watchlists/{id}/changes */
export interface ChangeFeedResponse {
  events: ChangeEvent[]
  first_visit: boolean
  last_seen_at: string | null
}

/** Response from POST /watchlists/{id}/seen */
export interface SeenStateResponse {
  watchlist_id: string
  last_seen_at: string
  last_seen_event_id: number | null
}

/** Standard error envelope from the API */
export interface ApiError {
  error: {
    code: string
    message: string
    details: Record<string, unknown>
  }
}
