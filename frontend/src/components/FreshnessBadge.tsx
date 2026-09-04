/**
 * Renders the backend-supplied freshness string as a colored badge.
 * Never calls classify_freshness — that lives server-side.
 */
import type { QuoteResponse } from '../types'

type Freshness = QuoteResponse['freshness']

const MAP: Record<Freshness, { cls: string; label: string }> = {
  FRESH:   { cls: 'badge badge-fresh',   label: 'Fresh' },
  DELAYED: { cls: 'badge badge-delayed', label: 'Delayed' },
  STALE:   { cls: 'badge badge-stale',   label: 'Stale' },
}

interface Props {
  freshness: Freshness
  /** Show the raw enum string instead of a human label. Default false. */
  raw?: boolean
}

export default function FreshnessBadge({ freshness, raw = false }: Props) {
  const { cls, label } = MAP[freshness]
  return <span className={cls}>{raw ? freshness : label}</span>
}
