import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { extractErrorMessage } from '../services/api'
import EyeIcon from '../components/EyeIcon'

export default function RegisterPage() {
  const { register } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password !== confirm) {
      setError('Passwords do not match.')
      return
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    setIsLoading(true)
    try {
      await register(email, password)
    } catch (err) {
      setError(extractErrorMessage(err))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
        background: 'var(--bg-page)',
      }}
    >
      <div style={{ width: '100%', maxWidth: 420 }}>
        {/* Header */}
        <div style={{ textAlign: 'center', marginBottom: 28 }}>
          <div
            style={{
              width: 52,
              height: 52,
              borderRadius: 14,
              background: 'var(--color-soft-pink)',
              color: 'var(--color-pink-primary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '1.6rem',
              margin: '0 auto 14px',
              border: '1px solid var(--color-pink-primary)',
            }}
          >
            📊
          </div>
          <h1 style={{ margin: 0, fontSize: '1.65rem', fontWeight: 700, color: 'var(--color-text-dark)', letterSpacing: '-0.02em' }}>
            Create an account
          </h1>
          <p style={{ margin: '6px 0 0', color: 'var(--color-muted-purple)', fontSize: '0.92rem' }}>
            Track what actually matters in the market
          </p>
        </div>

        {/* Card */}
        <div
          style={{
            padding: 32,
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 14,
          }}
        >
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div>
              <label
                htmlFor="reg-email"
                style={{ display: 'block', marginBottom: 6, fontSize: '0.84rem', fontWeight: 600, color: 'var(--color-text-dark)' }}
              >
                Email Address
              </label>
              <input
                id="reg-email"
                type="email"
                className="input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                autoComplete="email"
                required
              />
            </div>

            <div>
              <label
                htmlFor="reg-password"
                style={{ display: 'block', marginBottom: 6, fontSize: '0.84rem', fontWeight: 600, color: 'var(--color-text-dark)' }}
              >
                Password
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  id="reg-password"
                  type={showPassword ? 'text' : 'password'}
                  className="input"
                  style={{ paddingRight: 42 }}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="at least 8 characters"
                  autoComplete="new-password"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  title={showPassword ? 'Hide password' : 'Show password'}
                  style={{
                    position: 'absolute',
                    right: 10,
                    top: '50%',
                    transform: 'translateY(-50%)',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    padding: 4,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    opacity: 0.8,
                  }}
                >
                  <EyeIcon visible={showPassword} size={18} />
                </button>
              </div>
            </div>

            <div>
              <label
                htmlFor="reg-confirm"
                style={{ display: 'block', marginBottom: 6, fontSize: '0.84rem', fontWeight: 600, color: 'var(--color-text-dark)' }}
              >
                Confirm Password
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  id="reg-confirm"
                  type={showConfirm ? 'text' : 'password'}
                  className={`input ${error?.includes('match') ? 'input-error' : ''}`}
                  style={{ paddingRight: 42 }}
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  placeholder="••••••••"
                  autoComplete="new-password"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowConfirm(!showConfirm)}
                  aria-label={showConfirm ? 'Hide password' : 'Show password'}
                  title={showConfirm ? 'Hide password' : 'Show password'}
                  style={{
                    position: 'absolute',
                    right: 10,
                    top: '50%',
                    transform: 'translateY(-50%)',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    padding: 4,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    opacity: 0.8,
                  }}
                >
                  <EyeIcon visible={showConfirm} size={18} />
                </button>
              </div>
            </div>

            {error && (
              <p
                role="alert"
                style={{
                  margin: 0,
                  padding: '10px 14px',
                  background: 'var(--color-soft-pink)',
                  border: '1px solid var(--color-pink-primary)',
                  borderRadius: 8,
                  fontSize: '0.84rem',
                  color: 'var(--color-pink-primary)',
                  fontWeight: 500,
                }}
              >
                {error}
              </p>
            )}

            <button
              id="register-submit"
              type="submit"
              className="btn btn-primary"
              disabled={isLoading}
              style={{ marginTop: 6, width: '100%', padding: '12px 0' }}
            >
              {isLoading ? 'Creating account…' : 'Create account'}
            </button>
          </form>
        </div>

        <p style={{ textAlign: 'center', marginTop: 20, fontSize: '0.88rem', color: 'var(--color-muted-purple)' }}>
          Already have an account?{' '}
          <Link to="/login" style={{ color: 'var(--color-pink-primary)', fontWeight: 600, textDecoration: 'none' }}>
            Sign in
          </Link>
        </p>
      </div>
    </div>
  )
}
