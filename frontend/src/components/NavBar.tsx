import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

export default function NavBar() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  return (
    <header
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 50,
        background: 'rgba(255, 255, 255, 0.95)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      <div
        style={{
          maxWidth: 1100,
          margin: '0 auto',
          padding: '0 24px',
          height: 60,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        {/* Logo */}
        <button
          onClick={() => navigate('/')}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: 0,
          }}
        >
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: 'var(--color-soft-pink)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--color-pink-primary)',
              fontSize: '1rem',
            }}
          >
            📊
          </div>
          <span
            style={{
              fontWeight: 700,
              fontSize: '1rem',
              color: 'var(--color-text-dark)',
              letterSpacing: '-0.01em',
            }}
          >
            Smart Watchlist
          </span>
        </button>

        {/* Right side */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {user && (
            <span
              style={{
                fontSize: '0.8rem',
                fontWeight: 500,
                color: 'var(--color-muted-purple)',
                background: 'var(--color-lavender-subtle)',
                padding: '4px 10px',
                borderRadius: 6,
                border: '1px solid var(--border-subtle)',
              }}
            >
              {user.email}
            </span>
          )}
          <button className="btn btn-ghost btn-sm" onClick={logout}>
            Sign out
          </button>
        </div>
      </div>
    </header>
  )
}
