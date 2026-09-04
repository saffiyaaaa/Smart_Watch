/**
 * The single HTTP client. Every request to the backend goes through here so
 * that auth headers, timeouts and error shaping exist in exactly one place.
 *
 * Phase 3 adds the JWT interceptor and the 401 redirect.
 */
import axios from 'axios'

// Vite proxies /api to the backend in development (see vite.config.ts).
const BASE_URL = import.meta.env.VITE_API_URL ?? '/api'

export const api = axios.create({
  baseURL: BASE_URL,
  // A bounded timeout on the client too: the backend promises never to hang on
  // a provider, and the browser should make the same promise to the user.
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

export interface HealthResponse {
  status: string
  service: string
}

export interface ReadyResponse {
  status: string
  checks: Record<string, string>
}

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await api.get<HealthResponse>('/health')
  return data
}

export async function getReady(): Promise<ReadyResponse> {
  const { data } = await api.get<ReadyResponse>('/ready')
  return data
}
