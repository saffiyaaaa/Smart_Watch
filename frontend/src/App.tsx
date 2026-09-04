/**
 * Phase 1 shell. Its only job is to prove the frontend can reach the backend
 * and that loading / error / success are all handled from the very first
 * component -- the failure states are not an afterthought bolted on later.
 *
 * Phase 10 replaces this with the real watchlist experience.
 */
import { useQuery } from '@tanstack/react-query'
import { getHealth, getReady } from './services/api'

function StatusRow({
  label,
  isLoading,
  isError,
  error,
  value,
}: {
  label: string
  isLoading: boolean
  isError: boolean
  error: Error | null
  value?: string
}) {
  let tone = 'bg-slate-100 text-slate-600'
  let text = 'checking…'

  if (isError) {
    tone = 'bg-red-100 text-red-800'
    text = error?.message ?? 'unreachable'
  } else if (!isLoading && value) {
    tone = 'bg-green-100 text-green-800'
    text = value
  }

  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-200 py-3 last:border-0">
      <span className="font-mono text-sm text-slate-700">{label}</span>
      <span className={`rounded-full px-3 py-1 text-xs font-medium ${tone}`}>{text}</span>
    </div>
  )
}

export default function App() {
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth })
  const ready = useQuery({ queryKey: ['ready'], queryFn: getReady })

  const connected = health.isSuccess && ready.isSuccess

  return (
    <main className="mx-auto max-w-xl px-6 py-16">
      <h1 className="text-2xl font-semibold text-slate-900">Smart Market Watchlist</h1>
      <p className="mt-2 text-sm text-slate-500">
        Phase 1 — infrastructure check. The watchlist arrives in Phase 10.
      </p>

      <section className="mt-8 rounded-lg border border-slate-200 p-5">
        <h2 className="mb-2 text-sm font-semibold tracking-wide text-slate-500 uppercase">
          Backend connection
        </h2>
        <StatusRow
          label="GET /health"
          isLoading={health.isLoading}
          isError={health.isError}
          error={health.error}
          value={health.data?.status}
        />
        <StatusRow
          label="GET /ready"
          isLoading={ready.isLoading}
          isError={ready.isError}
          error={ready.error}
          value={ready.data?.status}
        />
      </section>

      {!connected && !health.isLoading && (
        <p className="mt-4 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
          Backend unreachable. Start it with{' '}
          <code className="font-mono">uvicorn app.main:app --reload</code> from{' '}
          <code className="font-mono">backend/</code>.
        </p>
      )}
    </main>
  )
}
