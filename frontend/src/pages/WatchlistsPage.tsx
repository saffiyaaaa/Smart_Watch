import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import NavBar from '../components/NavBar'
import ErrorBanner from '../components/ErrorBanner'
import SkeletonRow from '../components/SkeletonRow'
import {
  createWatchlist,
  deleteWatchlist,
  extractErrorMessage,
  listWatchlists,
} from '../services/api'
import type { Watchlist } from '../types'

function WatchlistRow({ wl, onDelete }: { wl: Watchlist; onDelete: (id: string) => void }) {
  const navigate = useNavigate()
  const symbolCount = wl.items.length

  return (
    <div
      className="clickable-row"
      onClick={() => navigate(`/watchlists/${wl.id}`)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && navigate(`/watchlists/${wl.id}`)}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <div
          style={{
            width: 42,
            height: 42,
            borderRadius: 10,
            background: 'var(--color-lavender-subtle)',
            color: 'var(--color-pink-primary)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '1.1rem',
          }}
        >
          📂
        </div>
        <div>
          <div style={{ fontWeight: 600, fontSize: '0.98rem', color: 'var(--color-text-dark)' }}>
            {wl.name}
          </div>
          <div style={{ fontSize: '0.82rem', color: 'var(--color-muted-purple)', marginTop: 2 }}>
            {symbolCount === 0
              ? '0 symbols'
              : `${symbolCount} symbol${symbolCount !== 1 ? 's' : ''} · ${wl.items.map((i) => i.symbol).join(', ')}`}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--color-pink-primary)' }}>
          View →
        </span>
        <button
          className="btn btn-danger btn-xs"
          onClick={(e) => {
            e.stopPropagation()
            onDelete(wl.id)
          }}
          title="Delete watchlist"
          aria-label={`Delete ${wl.name}`}
        >
          ✕
        </button>
      </div>
    </div>
  )
}

export default function WatchlistsPage() {
  const qc = useQueryClient()
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [newName, setNewName] = useState('')
  const [formError, setFormError] = useState<string | null>(null)

  const { data: watchlists, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['watchlists'],
    queryFn: listWatchlists,
  })

  const createMutation = useMutation({
    mutationFn: (name: string) => createWatchlist(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['watchlists'] })
      setNewName('')
      setFormError(null)
      setShowCreateForm(false)
    },
    onError: (err) => setFormError(extractErrorMessage(err)),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteWatchlist(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlists'] }),
  })

  function handleCreate(e: FormEvent) {
    e.preventDefault()
    const trimmed = newName.trim()
    if (!trimmed) { setFormError('Name cannot be blank.'); return }
    createMutation.mutate(trimmed)
  }

  const totalWatchlists = watchlists?.length || 0
  const totalSymbols = watchlists?.reduce((acc, w) => acc + w.items.length, 0) || 0

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-page)' }}>
      <NavBar />

      <main style={{ maxWidth: 880, margin: '0 auto', padding: '40px 24px' }}>
        {/* Page header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24, flexWrap: 'wrap', gap: 16 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: '1.75rem', fontWeight: 700, color: 'var(--color-text-dark)', letterSpacing: '-0.02em' }}>
              Your Watchlists
            </h1>
            <p style={{ margin: '6px 0 0', color: 'var(--color-muted-purple)', fontSize: '0.92rem' }}>
              Track what matters. See what's changed.
            </p>
          </div>

          <button
            id="create-watchlist-trigger"
            className="btn btn-primary"
            onClick={() => setShowCreateForm(!showCreateForm)}
          >
            {showCreateForm ? 'Cancel' : '+ Create Watchlist'}
          </button>
        </div>

        {/* Compact summary bar (horizontal layout with dividers) */}
        {watchlists && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 24,
              padding: '12px 18px',
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 10,
              marginBottom: 28,
              fontSize: '0.85rem',
              color: 'var(--color-muted-purple)',
              flexWrap: 'wrap',
            }}
          >
            <div>
              <strong style={{ color: 'var(--color-text-dark)', fontWeight: 700 }}>{totalWatchlists}</strong> Watchlist{totalWatchlists !== 1 ? 's' : ''}
            </div>
            <div style={{ width: 1, height: 16, background: 'var(--border-subtle)' }} />
            <div>
              <strong style={{ color: 'var(--color-text-dark)', fontWeight: 700 }}>{totalSymbols}</strong> Tracked Symbol{totalSymbols !== 1 ? 's' : ''}
            </div>
            <div style={{ width: 1, height: 16, background: 'var(--border-subtle)' }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--fresh-fresh)' }} />
              Live Change Monitoring
            </div>
          </div>
        )}

        {/* Create form dropdown panel */}
        {showCreateForm && (
          <form
            onSubmit={handleCreate}
            style={{
              padding: 20,
              background: 'var(--bg-surface)',
              border: '1px solid var(--color-pink-primary)',
              borderRadius: 12,
              marginBottom: 24,
            }}
          >
            <label
              htmlFor="create-watchlist-name"
              style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, color: 'var(--color-text-dark)', marginBottom: 8 }}
            >
              Watchlist Name
            </label>
            <div style={{ display: 'flex', gap: 10 }}>
              <input
                id="create-watchlist-name"
                className="input"
                style={{ flex: 1 }}
                placeholder="e.g. Tech Leaders, Portfolio Core"
                value={newName}
                onChange={(e) => { setNewName(e.target.value); setFormError(null) }}
                maxLength={100}
                autoFocus
              />
              <button
                id="create-watchlist-submit"
                type="submit"
                className="btn btn-primary"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? 'Saving…' : 'Save Watchlist'}
              </button>
            </div>
            {formError && (
              <p style={{ margin: '8px 0 0', fontSize: '0.82rem', color: 'var(--color-pink-primary)', fontWeight: 500 }}>
                {formError}
              </p>
            )}
          </form>
        )}

        {/* Content */}
        {isLoading && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {[1, 2, 3].map((i) => (
              <div key={i} className="non-clickable-row">
                <SkeletonRow height={16} lines={2} />
              </div>
            ))}
          </div>
        )}

        {isError && (
          <ErrorBanner
            message={extractErrorMessage(error)}
            onRetry={() => refetch()}
          />
        )}

        {!isLoading && !isError && watchlists && (
          <>
            {watchlists.length === 0 ? (
              <div
                style={{
                  textAlign: 'center',
                  padding: '60px 24px',
                  background: 'var(--bg-surface)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 12,
                  color: 'var(--color-muted-purple)',
                }}
              >
                <div style={{ fontSize: '2.5rem', marginBottom: 12 }}>📊</div>
                <p style={{ margin: 0, fontSize: '1.05rem', fontWeight: 600, color: 'var(--color-text-dark)' }}>
                  No watchlists yet
                </p>
                <p style={{ margin: '8px 0 16px', fontSize: '0.875rem' }}>
                  Create your first watchlist to track meaningful market movement.
                </p>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => setShowCreateForm(true)}
                >
                  + Create Watchlist
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div className="section-label" style={{ marginBottom: 4 }}>
                  Your Watchlist Collections ({watchlists.length})
                </div>
                {watchlists.map((wl) => (
                  <WatchlistRow
                    key={wl.id}
                    wl={wl}
                    onDelete={(id) => {
                      if (confirm(`Delete "${wl.name}"? This cannot be undone.`)) {
                        deleteMutation.mutate(id)
                      }
                    }}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}
