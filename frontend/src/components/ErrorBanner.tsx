/** API/provider failure banner. Always shows something actionable — never a blank screen. */
interface Props {
  message: string
  onRetry?: () => void
}

export default function ErrorBanner({ message, onRetry }: Props) {
  return (
    <div
      role="alert"
      style={{
        background: 'var(--color-soft-pink)',
        border: '1px solid var(--color-pink-primary)',
        borderRadius: 10,
        padding: '14px 18px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: '1.1rem' }}>⚠️</span>
        <span style={{ color: 'var(--color-text-dark)', fontSize: '0.875rem', fontWeight: 500 }}>{message}</span>
      </div>
      {onRetry && (
        <button className="btn btn-primary btn-sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}
