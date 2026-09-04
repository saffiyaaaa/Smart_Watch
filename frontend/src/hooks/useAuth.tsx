/**
 * Auth context. Wraps the JWT lifecycle so every component can call
 * useAuth() rather than reading localStorage directly.
 *
 * Token storage: localStorage['smw_token']
 * Logout signals: the api.ts interceptor fires 'smw:logout' on 401, which
 *   this hook catches so a background query failure logs the user out
 *   automatically without an import cycle.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useNavigate } from 'react-router-dom'
import { KEY_TOKEN, authLogin, authMe, authRegister, extractErrorMessage } from '../services/api'
import type { User } from '../types'

interface AuthContextValue {
  user: User | null
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  // On mount, validate any existing token
  useEffect(() => {
    const token = localStorage.getItem(KEY_TOKEN)
    if (!token) { setIsLoading(false); return }
    authMe()
      .then(setUser)
      .catch(() => { localStorage.removeItem(KEY_TOKEN) })
      .finally(() => setIsLoading(false))
  }, [])

  // Listen for the 401 signal emitted by the api.ts interceptor
  useEffect(() => {
    const handle = () => {
      setUser(null)
      navigate('/login', { replace: true })
    }
    window.addEventListener('smw:logout', handle)
    return () => window.removeEventListener('smw:logout', handle)
  }, [navigate])

  const login = useCallback(async (email: string, password: string) => {
    const { access_token } = await authLogin(email, password)
    localStorage.setItem(KEY_TOKEN, access_token)
    const me = await authMe()
    setUser(me)
    navigate('/', { replace: true })
  }, [navigate])

  const register = useCallback(async (email: string, password: string) => {
    await authRegister(email, password)
    // Auto-login after registration
    await login(email, password)
  }, [login])

  const logout = useCallback(() => {
    localStorage.removeItem(KEY_TOKEN)
    setUser(null)
    navigate('/login', { replace: true })
  }, [navigate])

  const value = useMemo<AuthContextValue>(
    () => ({ user, isLoading, login, register, logout }),
    [user, isLoading, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

/** Wraps a route, redirecting to /login if not authenticated. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (!isLoading && !user) navigate('/login', { replace: true })
  }, [user, isLoading, navigate])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="skeleton" style={{ width: 120, height: 8 }} />
      </div>
    )
  }
  if (!user) return null
  return <>{children}</>
}

export { extractErrorMessage }
