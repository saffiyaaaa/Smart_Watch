/** Thin horizontal bar showing score/100, colored by severity. */
import type { ChangeEvent } from '../types'

type Severity = ChangeEvent['severity']

const COLOR: Record<Severity, string> = {
  WATCH:     'var(--sev-watch)',
  IMPORTANT: 'var(--sev-important)',
  HIGH:      'var(--sev-high)',
}

export default function ScoreBar({ score, severity }: { score: number; severity: Severity }) {
  return (
    <div className="score-bar-track" title={`Attention score: ${score}/100`}>
      <div
        className="score-bar-fill"
        style={{
          width: `${score}%`,
          background: COLOR[severity],
          opacity: 0.85,
        }}
      />
    </div>
  )
}
