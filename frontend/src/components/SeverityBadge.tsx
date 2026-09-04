/**
 * Renders the backend-supplied severity string as a styled badge.
 * Never classifies severity itself — the string comes from the API.
 */
import type { ChangeEvent } from '../types'

type Severity = ChangeEvent['severity']

const MAP: Record<Severity, { cls: string; dot: string }> = {
  WATCH:     { cls: 'badge badge-watch',     dot: '●' },
  IMPORTANT: { cls: 'badge badge-important', dot: '◆' },
  HIGH:      { cls: 'badge badge-high',      dot: '▲' },
}

export default function SeverityBadge({ severity }: { severity: Severity }) {
  const { cls, dot } = MAP[severity]
  return (
    <span className={cls}>
      <span style={{ fontSize: '0.6rem' }}>{dot}</span>
      {severity}
    </span>
  )
}
