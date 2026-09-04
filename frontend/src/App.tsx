/**
 * Application router — Phase 10.
 *
 * Route structure:
 *   /login         → LoginPage
 *   /register      → RegisterPage
 *   /              → WatchlistsPage       (requires auth)
 *   /watchlists/:id             → WatchlistDetailPage (requires auth)
 *   /watchlists/:id/symbols/:symbol → SymbolDetailPage (requires auth)
 *
 * RequireAuth wraps protected routes: it redirects to /login when the user
 * has no valid token, and shows nothing (null) during the initial token
 * validation to avoid a flash of the login page for returning users.
 */
import { Navigate, Route, Routes } from 'react-router-dom'
import { RequireAuth } from './hooks/useAuth'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import WatchlistDetailPage from './pages/WatchlistDetailPage'
import WatchlistsPage from './pages/WatchlistsPage'
import SymbolDetailPage from './pages/SymbolDetailPage'

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      {/* Protected */}
      <Route
        path="/"
        element={
          <RequireAuth>
            <WatchlistsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/watchlists/:id"
        element={
          <RequireAuth>
            <WatchlistDetailPage />
          </RequireAuth>
        }
      />
      <Route
        path="/watchlists/:id/symbols/:symbol"
        element={
          <RequireAuth>
            <SymbolDetailPage />
          </RequireAuth>
        }
      />

      {/* Catch-all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
