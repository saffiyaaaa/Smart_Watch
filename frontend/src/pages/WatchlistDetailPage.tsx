import { useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import NavBar from '../components/NavBar'
import SeverityBadge from '../components/SeverityBadge'
import FreshnessBadge from '../components/FreshnessBadge'
import ScoreBar from '../components/ScoreBar'
import ErrorBanner from '../components/ErrorBanner'
import SkeletonRow from '../components/SkeletonRow'
import {
  addSymbol,
  extractErrorMessage,
  getChangeFeed,
  getQuote,
  getWatchlist,
  markSeen,
  removeSymbol,
} from '../services/api'
import type { ChangeEvent, WatchlistItem } from '../types'

/* ─── helpers ─────────────────────────────────────────────────────────────── */
function fmtPrice(p: string) {
  const n = parseFloat(p)
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60_000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

const POPULAR_SYMBOLS = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN']

/* ─── Event card ──────────────────────────────────────────────────────────── */
function EventCard({ event }: { event: ChangeEvent }) {
  const navigate = useNavigate()
  const { id: watchlistId } = useParams<{ id: string }>()

  return (
    <div
      style={{
        padding: '16px 20px',
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-subtle)',
        borderLeft: `4px solid ${
          event.severity === 'HIGH' ? 'var(--sev-high)' :
          event.severity === 'IMPORTANT' ? 'var(--sev-important)' :
          'var(--sev-watch)'
        }`,
        borderRadius: 10,
      }}
    >
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <button
            style={{
              background: 'var(--color-soft-pink)',
              border: '1px solid var(--color-pink-primary)',
              borderRadius: 6,
              padding: '2px 10px',
              color: 'var(--color-pink-primary)',
              fontWeight: 700,
              fontSize: '0.88rem',
              cursor: 'pointer',
              letterSpacing: '0.02em',
            }}
            onClick={() => navigate(`/watchlists/${watchlistId}/symbols/${event.symbol}`)}
            title={`View ${event.symbol} details & history`}
          >
            {event.symbol}
          </button>
          <SeverityBadge severity={event.severity} />
        </div>
        <span style={{ fontSize: '0.78rem', color: 'var(--color-muted-purple)' }}>
          {fmtRelativeTime(event.detected_at)}
        </span>
      </div>

      {/* Score bar */}
      <div style={{ marginBottom: 10 }}>
        <ScoreBar score={event.score} severity={event.severity} />
      </div>

      {/* Evidence — rendered verbatim from backend */}
      <ul style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 3 }}>
        {event.evidence.map((line, i) => (
          <li key={i} style={{ fontSize: '0.85rem', color: 'var(--color-text-dark)', lineHeight: 1.5 }}>
            {line}
          </li>
        ))}
      </ul>
    </div>
  )
}

/* ─── Symbol row (Section B) ──────────────────────────────────────────────── */
function SymbolRow({
  item,
  watchlistId,
  onRemove,
}: {
  item: WatchlistItem
  watchlistId: string
  onRemove: () => void
}) {
  const navigate = useNavigate()

  const { data: quote, isLoading, isError } = useQuery({
    queryKey: ['quote', watchlistId, item.symbol],
    queryFn: () => getQuote(watchlistId, item.symbol),
    retry: false,
  })

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '14px 18px',
        borderBottom: '1px solid var(--border-subtle)',
        background: 'var(--bg-surface)',
      }}
    >
      {/* Symbol + price */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flex: 1, minWidth: 0 }}>
        <span
          style={{
            fontWeight: 700,
            fontSize: '0.95rem',
            color: 'var(--color-text-dark)',
            minWidth: 60,
          }}
        >
          {item.symbol}
        </span>

        {isLoading && <SkeletonRow height={12} style={{ width: 90 }} />}

        {isError && (
          <span style={{ fontSize: '0.8rem', color: 'var(--color-muted-purple)', fontStyle: 'italic' }}>
            No market data yet
          </span>
        )}

        {quote && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
            <span style={{ fontWeight: 700, fontSize: '0.98rem', color: 'var(--color-text-dark)' }}>
              ${fmtPrice(quote.price)}
            </span>
            <FreshnessBadge freshness={quote.freshness} />
          </div>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
        <button
          className="btn btn-ghost btn-xs"
          onClick={() => navigate(`/watchlists/${watchlistId}/symbols/${item.symbol}`)}
          title={`View ${item.symbol} history`}
        >
          Details →
        </button>
        <button
          className="btn btn-danger btn-xs"
          onClick={onRemove}
          title={`Remove ${item.symbol}`}
          aria-label={`Remove ${item.symbol}`}
        >
          Remove
        </button>
      </div>
    </div>
  )
}

/* ─── Main page ───────────────────────────────────────────────────────────── */
export default function WatchlistDetailPage() {
  const { id: watchlistId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [symbolInput, setSymbolInput] = useState('')
  const [symbolError, setSymbolError] = useState<string | null>(null)
  const [markingDone, setMarkingDone] = useState(false)

  /* data */
  const { data: watchlist, isLoading: wlLoading, isError: wlError, error: wlErr, refetch: wlRefetch } =
    useQuery({
      queryKey: ['watchlist', watchlistId],
      queryFn: () => getWatchlist(watchlistId!),
      enabled: !!watchlistId,
    })

  const { data: feed, isLoading: feedLoading, isError: feedError, error: feedErr, refetch: feedRefetch } =
    useQuery({
      queryKey: ['feed', watchlistId],
      queryFn: () => getChangeFeed(watchlistId!),
      enabled: !!watchlistId,
    })

  /* mutations */
  const addMutation = useMutation({
    mutationFn: (sym: string) => addSymbol(watchlistId!, sym),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['watchlist', watchlistId] })
      setSymbolInput('')
      setSymbolError(null)
    },
    onError: (err) => setSymbolError(extractErrorMessage(err)),
  })

  const removeMutation = useMutation({
    mutationFn: (sym: string) => removeSymbol(watchlistId!, sym),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['watchlist', watchlistId] })
      qc.invalidateQueries({ queryKey: ['feed', watchlistId] })
    },
  })

  const markSeenMutation = useMutation({
    mutationFn: () =>
      markSeen(watchlistId!, {
        last_seen_event_id: feed?.events[0]?.id,
      }),
    onSuccess: () => {
      setMarkingDone(true)
      qc.invalidateQueries({ queryKey: ['feed', watchlistId] })
    },
  })

  /* handlers */
  function handleAddSymbol(e: FormEvent) {
    e.preventDefault()
    const sym = symbolInput.trim().toUpperCase()
    if (!sym) return
    addMutation.mutate(sym)
  }

  function handleChipClick(sym: string) {
    setSymbolInput(sym)
    addMutation.mutate(sym)
  }

  /* load / error states */
  if (wlLoading) {
    return (
      <div style={{ minHeight: '100vh', background: 'var(--bg-page)' }}>
        <NavBar />
        <main style={{ maxWidth: 880, margin: '0 auto', padding: '40px 24px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <SkeletonRow height={24} />
            <SkeletonRow height={16} lines={3} />
          </div>
        </main>
      </div>
    )
  }

  if (wlError) {
    return (
      <div style={{ minHeight: '100vh', background: 'var(--bg-page)' }}>
        <NavBar />
        <main style={{ maxWidth: 880, margin: '0 auto', padding: '40px 24px' }}>
          <ErrorBanner message={extractErrorMessage(wlErr)} onRetry={() => wlRefetch()} />
        </main>
      </div>
    )
  }

  const events = feed?.events ?? []
  const hasEvents = events.length > 0

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-page)' }}>
      <NavBar />

      <main style={{ maxWidth: 880, margin: '0 auto', padding: '40px 24px' }}>
        {/* Back + Title header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 28 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => navigate('/')}
              style={{ padding: '6px 12px' }}
            >
              ← Back
            </button>
            <div>
              <h1 style={{ margin: 0, fontSize: '1.6rem', fontWeight: 700, color: 'var(--color-text-dark)' }}>
                {watchlist?.name}
              </h1>
              <span style={{ fontSize: '0.82rem', color: 'var(--color-muted-purple)' }}>
                {watchlist?.items.length || 0} symbols in watchlist
              </span>
            </div>
          </div>
        </div>

        {/* ── Section A: Change feed (Primary Surface) ───────────────────────── */}
        <section style={{ marginBottom: 36 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span className="section-label">Changed since you last checked</span>
              {hasEvents && !markingDone && (
                <span
                  style={{
                    background: 'var(--sev-high-bg)',
                    color: 'var(--color-pink-primary)',
                    border: '1px solid var(--color-pink-primary)',
                    borderRadius: 99,
                    padding: '1px 8px',
                    fontSize: '0.72rem',
                    fontWeight: 700,
                  }}
                >
                  {events.length}
                </span>
              )}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              {feed?.last_seen_at && (
                <span style={{ fontSize: '0.78rem', color: 'var(--color-muted-purple)' }}>
                  Last checked {fmtRelativeTime(feed.last_seen_at)}
                </span>
              )}
              {hasEvents && !markingDone && (
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => markSeenMutation.mutate()}
                  disabled={markSeenMutation.isPending}
                >
                  {markSeenMutation.isPending ? '…' : '✓ Mark all as seen'}
                </button>
              )}
            </div>
          </div>

          {/* First-visit framing */}
          {feed?.first_visit && hasEvents && (
            <div
              style={{
                marginBottom: 14,
                padding: '12px 16px',
                background: 'var(--color-lavender-subtle)',
                borderRadius: 8,
                fontSize: '0.85rem',
                color: 'var(--color-text-dark)',
                border: '1px solid var(--border-subtle)',
              }}
            >
              👋 First time visiting — here are key events detected in the last 24 hours.
            </div>
          )}

          {/* Feed loading */}
          {feedLoading && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[1, 2].map((i) => (
                <div key={i} className="non-clickable-row" style={{ padding: 16 }}>
                  <SkeletonRow height={14} lines={3} />
                </div>
              ))}
            </div>
          )}

          {/* Feed error */}
          {feedError && (
            <ErrorBanner message={extractErrorMessage(feedErr)} onRetry={() => feedRefetch()} />
          )}

          {/* Events or Affirmative Silence */}
          {!feedLoading && !feedError && (
            <>
              {!hasEvents || markingDone ? (
                <div
                  style={{
                    padding: '32px 20px',
                    textAlign: 'center',
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 12,
                    color: 'var(--color-muted-purple)',
                  }}
                >
                  <div
                    style={{
                      width: 44,
                      height: 44,
                      borderRadius: '50%',
                      background: 'var(--fresh-fresh-bg)',
                      color: 'var(--fresh-fresh)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '1.4rem',
                      margin: '0 auto 12px',
                    }}
                  >
                    ✓
                  </div>
                  <p style={{ margin: 0, fontSize: '0.98rem', fontWeight: 600, color: 'var(--color-text-dark)' }}>
                    Nothing significant changed since your last visit.
                  </p>
                  <p style={{ margin: '6px 0 0', fontSize: '0.85rem' }}>
                    {markingDone ? "You're all caught up." : "All watched symbols are operating within normal parameters."}
                  </p>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {events.map((ev) => (
                    <EventCard key={ev.id} event={ev} />
                  ))}
                </div>
              )}
            </>
          )}
        </section>

        {/* ── Section B: All symbols ───────────────────────────────────────── */}
        <section>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
            <span className="section-label">
              Symbols ({watchlist?.items.length ?? 0})
            </span>
          </div>

          <div
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 12,
              overflow: 'hidden',
            }}
          >
            {/* Symbol list */}
            <div>
              {watchlist?.items.length === 0 ? (
                <div style={{ padding: '32px 20px', textAlign: 'center', color: 'var(--color-muted-purple)' }}>
                  <p style={{ margin: 0, fontSize: '0.9rem', fontWeight: 500 }}>
                    No symbols in this watchlist. Add a stock symbol below to get started.
                  </p>
                </div>
              ) : (
                watchlist?.items.map((item) => (
                  <SymbolRow
                    key={item.id}
                    item={item}
                    watchlistId={watchlistId!}
                    onRemove={() => removeMutation.mutate(item.symbol)}
                  />
                ))
              )}
            </div>

            {/* Add symbol form & quick popular chips */}
            <div
              style={{
                borderTop: '1px solid var(--border-subtle)',
                padding: '16px 20px',
                background: '#FAF8FC',
              }}
            >
              <form onSubmit={handleAddSymbol} style={{ display: 'flex', gap: 10, marginBottom: 10 }}>
                <input
                  id="add-symbol-input"
                  className="input"
                  style={{ flex: 1, textTransform: 'uppercase' }}
                  placeholder="Enter stock ticker (e.g. AAPL, MSFT, NVDA)"
                  value={symbolInput}
                  onChange={(e) => { setSymbolInput(e.target.value); setSymbolError(null) }}
                  maxLength={10}
                />
                <button
                  id="add-symbol-submit"
                  type="submit"
                  className="btn btn-primary"
                  disabled={addMutation.isPending}
                >
                  {addMutation.isPending ? 'Adding…' : '+ Add Symbol'}
                </button>
              </form>

              {/* Popular quick-add chips */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontSize: '0.78rem', color: 'var(--color-muted-purple)', fontWeight: 500 }}>
                  Quick Add:
                </span>
                {POPULAR_SYMBOLS.map((sym) => (
                  <button
                    key={sym}
                    type="button"
                    onClick={() => handleChipClick(sym)}
                    className="btn btn-ghost btn-xs"
                    style={{ fontSize: '0.75rem', padding: '2px 8px' }}
                  >
                    + {sym}
                  </button>
                ))}
              </div>

              {symbolError && (
                <p style={{ margin: '8px 0 0', fontSize: '0.82rem', color: 'var(--color-pink-primary)', fontWeight: 500 }}>
                  {symbolError}
                </p>
              )}
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}
