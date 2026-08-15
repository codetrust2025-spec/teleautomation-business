import React from 'react'
import { Spinner } from '../Loader.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { LoginScreen } from './LoginScreen.jsx'

/** Shows login when dashboard auth is enabled; otherwise renders the main app. */
export function AuthGate({ children }) {
  const { loading, enabled, authenticated } = useAuth()

  if (loading) {
    return (
      <div className="auth-screen">
        <Spinner size={32} />
        <p className="auth-loading-label">Loading…</p>
      </div>
    )
  }

  if (enabled && !authenticated) {
    return <LoginScreen />
  }

  return children
}
