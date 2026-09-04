import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import NavBar from '../components/NavBar'
import FreshnessBadge from '../components/FreshnessBadge'
import ErrorBanner from '../components/ErrorBanner'
import SkeletonRow from '../components/SkeletonRow'
import { extractErrorMessage, getHistory, getQuote } from '../services/api'

function fmtPrice(p: string) {
  return parseFloat(p).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtTimestamp(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function fmtDate(d: string): string {
  return new Date(d + 'T00:00:00').toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  })
}

function fmtVol(v: number | null): string {
  if (v === null) return '—'
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`
  return v.toLocaleString()
}

export default function SymbolDetailPage() {
  const { id: watchlistId, symbol } = useParams<{ id: string; symbol: string }>()
  const navigate = useNavigate()

  const {
    data: quote,
    isLoading: qLoading,
    isError: qError,
    error: qErr,
    refetch: qRefetch,
  } = useQuery({
    queryKey: ['quote', watchlistId, symbol],
    queryFn: () => getQuote(watchlistId!, symbol!),
    retry: false,
  })

  const {
    data: history,
    isLoading: hLoading,
    isError: hError,
    error: hErr,
    refetch: hRefetch,
  } = useQuery({
    queryKey: ['history', watchlistId, symbol],
    queryFn: () => getHistory(watchlistId!, symbol!, 30),
    retry: false,
  })

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-page)' }}>
      <NavBar />

      <main style={{ maxWidth: 880, margin: '0 auto', padding: '40px 24px' }}>
        {/* Back link + Symbol Title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 28 }}>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => navigate(`/watchlists/${watchlistId}`)}
          >
            ← Back
          </button>
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: '1.6rem',
                fontWeight: 700,
                color: 'var(--color-text-dark)',
              }}
            >
              {symbol}
            </h1>
            <span style={{ fontSize: '0.82rem', color: 'var(--color-muted-purple)' }}>
              Stock Details & Price History
            </span>
          </div>
        </div>

        {/* ── Current quote ──────────────────────────────────────────────── */}
        <section style={{ marginBottom: 32 }}>
          <span className="section-label" style={{ display: 'block', marginBottom: 12 }}>
            Current Market Quote
          </span>

          {qLoading && (
            <div style={{ padding: 20, background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', borderRadius: 12 }}>
              <SkeletonRow height={28} />
              <div style={{ marginTop: 12 }}>
                <SkeletonRow height={14} lines={2} />
              </div>
            </div>
          )}

          {qError && (
            <div style={{ padding: 20, background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', borderRadius: 12 }}>
              <ErrorBanner
                message={
                  (qErr as { response?: { status?: number } })?.response?.status === 404
                    ? `No market quote for ${symbol} yet. The ingestion worker has not recorded data for this symbol.`
                    : extractErrorMessage(qErr)
                }
                onRetry={() => qRefetch()}
              />
            </div>
          )}

          {quote && (
            <div
              style={{
                padding: '24px 24px',
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 12,
              }}
            >
              {/* Price + freshness badge */}
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 18 }}>
                <span
                  style={{
                    fontSize: '2.2rem',
                    fontWeight: 700,
                    letterSpacing: '-0.02em',
                    color: quote.freshness === 'STALE' ? 'var(--color-pink-primary)' : 'var(--color-text-dark)',
                  }}
                >
                  ${fmtPrice(quote.price)}
                </span>
                <FreshnessBadge freshness={quote.freshness} />
              </div>

              {/* Quote metrics grid */}
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
                  gap: 16,
                  padding: '16px 18px',
                  background: 'var(--color-lavender-subtle)',
                  borderRadius: 10,
                  border: '1px solid var(--border-subtle)',
                }}
              >
                {[
                  { label: 'Volume', value: fmtVol(quote.volume) },
                  { label: 'Market timestamp', value: fmtTimestamp(quote.market_timestamp) },
                  { label: 'Current freshness', value: quote.freshness },
                  { label: 'Ingest freshness', value: quote.ingest_freshness },
                ].map(({ label, value }) => (
                  <div key={label}>
                    <div style={{ fontSize: '0.75rem', color: 'var(--color-muted-purple)', marginBottom: 3, fontWeight: 500 }}>
                      {label}
                    </div>
                    <div style={{ fontSize: '0.92rem', color: 'var(--color-text-dark)', fontWeight: 600 }}>
                      {value}
                    </div>
                  </div>
                ))}
              </div>

              {/* Pastel pink Stale warning banner */}
              {quote.freshness === 'STALE' && (
                <div
                  style={{
                    marginTop: 18,
                    padding: '14px 18px',
                    background: 'var(--color-soft-pink)',
                    borderRadius: 10,
                    fontSize: '0.85rem',
                    color: 'var(--color-text-dark)',
                    border: '1px solid var(--color-pink-primary)',
                    lineHeight: 1.5,
                  }}
                >
                  <strong style={{ color: 'var(--color-pink-primary)', display: 'block', marginBottom: 4 }}>
                    ⚠️ This quote is stale.
                  </strong>
                  The displayed price is the last known value and may not reflect current market conditions. Confidence in alerts based on this data is reduced.
                </div>
              )}
            </div>
          )}
        </section>

        {/* ── Price history table ────────────────────────────────────────── */}
        <section>
          <span className="section-label" style={{ display: 'block', marginBottom: 12 }}>
            Price history — last 30 sessions
          </span>

          {hLoading && (
            <div style={{ padding: 20, background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', borderRadius: 12 }}>
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} style={{ padding: '10px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                  <SkeletonRow height={14} />
                </div>
              ))}
            </div>
          )}

          {hError && (
            <ErrorBanner message={extractErrorMessage(hErr)} onRetry={() => hRefetch()} />
          )}

          {history && (
            <div
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 12,
                overflow: 'hidden',
              }}
            >
              {history.length === 0 ? (
                <div style={{ padding: '32px 20px', textAlign: 'center', color: 'var(--color-muted-purple)' }}>
                  <p style={{ margin: 0, fontSize: '0.9rem' }}>
                    No historical sessions recorded yet. Run the backend ingestion worker to populate session history.
                  </p>
                </div>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.88rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border-subtle)', background: 'var(--color-lavender-subtle)' }}>
                      {['Session Date', 'Close Price', 'Volume'].map((h) => (
                        <th
                          key={h}
                          style={{
                            padding: '12px 18px',
                            textAlign: h === 'Session Date' ? 'left' : 'right',
                            fontSize: '0.75rem',
                            fontWeight: 700,
                            color: 'var(--color-muted-purple)',
                            letterSpacing: '0.04em',
                            textTransform: 'uppercase',
                          }}
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((bar) => (
                      <tr
                        key={bar.session_date}
                        style={{ borderBottom: '1px solid var(--border-subtle)' }}
                      >
                        <td style={{ padding: '12px 18px', color: 'var(--color-text-dark)', fontWeight: 500 }}>
                          {fmtDate(bar.session_date)}
                        </td>
                        <td
                          style={{
                            padding: '12px 18px',
                            textAlign: 'right',
                            fontWeight: 700,
                            color: 'var(--color-text-dark)',
                          }}
                        >
                          ${fmtPrice(bar.close)}
                        </td>
                        <td
                          style={{
                            padding: '12px 18px',
                            textAlign: 'right',
                            color: 'var(--color-muted-purple)',
                            fontWeight: 500,
                          }}
                        >
                          {fmtVol(bar.volume)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
