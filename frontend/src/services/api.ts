/**
 * Full API layer. Every request goes through here so auth headers,
 * timeouts, and error shaping live in exactly one place.
 *
 * Token lifecycle:
 *   - Stored in localStorage under KEY_TOKEN
 *   - Attached to every request by the request interceptor
 *   - Cleared on 401 — the auth context reacts and redirects to /login
 */
import axios, { AxiosError } from 'axios'
import type {
  ChangeFeedResponse,
  DailyBar,
  QuoteResponse,
  SeenStateResponse,
  TokenResponse,
  User,
  Watchlist,
  WatchlistItem,
} from '../types'

const BASE_URL = import.meta.env.VITE_API_URL ?? '/api'
export const KEY_TOKEN = 'smw_token'

export const api = axios.create({
  baseURL: BASE_URL,
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
})

/* ─── Auth interceptors ──────────────────────────────────────────────────── */

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(KEY_TOKEN)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err: AxiosError) => {
    if (err.response?.status === 401) {
      localStorage.removeItem(KEY_TOKEN)
      // Signal auth context by dispatching a storage event that the auth hook
      // listens for. Avoids a direct import cycle between api.ts and useAuth.
      window.dispatchEvent(new Event('smw:logout'))
    }
    return Promise.reject(err)
  },
)

/** Extract a human-readable message from an axios error */
export function extractErrorMessage(err: unknown): string {
  if (err instanceof AxiosError) {
    const data = err.response?.data as { error?: { message?: string } } | undefined
    if (data?.error?.message) return data.error.message
    if (err.message === 'Network Error') return 'Cannot reach the server. Check your connection.'
    if (err.code === 'ECONNABORTED') return 'Request timed out.'
  }
  return 'Something went wrong.'
}

/* ─── Health / readiness ─────────────────────────────────────────────────── */

export interface HealthResponse { status: string; service: string }
export interface ReadyResponse { status: string; checks: Record<string, string> }

export const getHealth = async (): Promise<HealthResponse> =>
  (await api.get<HealthResponse>('/health')).data

export const getReady = async (): Promise<ReadyResponse> =>
  (await api.get<ReadyResponse>('/ready')).data

/* ─── Auth ───────────────────────────────────────────────────────────────── */

export const authRegister = async (email: string, password: string): Promise<User> =>
  (await api.post<User>('/auth/register', { email, password })).data

export const authLogin = async (email: string, password: string): Promise<TokenResponse> =>
  (await api.post<TokenResponse>('/auth/login', { email, password })).data

export const authMe = async (): Promise<User> =>
  (await api.get<User>('/auth/me')).data

/* ─── Watchlists ─────────────────────────────────────────────────────────── */

export const listWatchlists = async (): Promise<Watchlist[]> =>
  (await api.get<Watchlist[]>('/watchlists')).data

export const getWatchlist = async (id: string): Promise<Watchlist> =>
  (await api.get<Watchlist>(`/watchlists/${id}`)).data

export const createWatchlist = async (name: string): Promise<Watchlist> =>
  (await api.post<Watchlist>('/watchlists', { name })).data

export const deleteWatchlist = async (id: string): Promise<void> =>
  void (await api.delete(`/watchlists/${id}`))

/* ─── Symbols ────────────────────────────────────────────────────────────── */

export const addSymbol = async (watchlistId: string, symbol: string): Promise<WatchlistItem> =>
  (await api.post<WatchlistItem>(`/watchlists/${watchlistId}/symbols`, { symbol })).data

export const removeSymbol = async (watchlistId: string, symbol: string): Promise<void> =>
  void (await api.delete(`/watchlists/${watchlistId}/symbols/${symbol}`))

/* ─── Market data ────────────────────────────────────────────────────────── */

export const getQuote = async (watchlistId: string, symbol: string): Promise<QuoteResponse> =>
  (await api.get<QuoteResponse>(`/watchlists/${watchlistId}/symbols/${symbol}/quote`)).data

export const getHistory = async (
  watchlistId: string,
  symbol: string,
  limit = 30,
): Promise<DailyBar[]> =>
  (await api.get<DailyBar[]>(`/watchlists/${watchlistId}/symbols/${symbol}/history`, {
    params: { limit },
  })).data

/* ─── Change feed ────────────────────────────────────────────────────────── */

export const getChangeFeed = async (watchlistId: string): Promise<ChangeFeedResponse> =>
  (await api.get<ChangeFeedResponse>(`/watchlists/${watchlistId}/changes`)).data

export const markSeen = async (
  watchlistId: string,
  opts?: { seen_at?: string; last_seen_event_id?: number },
): Promise<SeenStateResponse> =>
  (await api.post<SeenStateResponse>(`/watchlists/${watchlistId}/seen`, opts ?? {})).data
