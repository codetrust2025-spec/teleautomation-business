import React, { useState } from 'react'
import { Spinner } from '../Loader.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { ForgotPasswordPanel } from './ForgotPasswordPanel.jsx'

const BRAND = {
  name: 'TeleAutomation',
  tagline: 'Telegram CRM · AI inbox · multi-account',
}

export function LoginScreen() {
  const { login, error: authError } = useAuth()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState('')
  const [forgot, setForgot] = useState(false)

  async function onSubmit(e) {
    e.preventDefault()
    if (!password.trim()) {
      setLocalError('Enter your password')
      return
    }
    setBusy(true)
    setLocalError('')
    const result = await login(username.trim(), password)
    setBusy(false)
    if (!result?.ok) {
      setLocalError(result?.error || 'Login failed')
    }
  }

  const error = localError || authError

  if (forgot) {
    return (
      <div className="auth-screen">
        <div className="auth-card">
          <div className="auth-brand">
            <h1>Reset password</h1>
            <p>Handler self-service — no WhatsApp needed</p>
          </div>
          <ForgotPasswordPanel onBack={() => setForgot(false)} />
        </div>
      </div>
    )
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand">
          <h1>{BRAND.name}</h1>
          <p>{BRAND.tagline}</p>
        </div>
        <form className="auth-form" onSubmit={onSubmit}>
          <label className="auth-field">
            <span className="auth-label">Username</span>
            <input
              className="input auth-input"
              type="text"
              autoComplete="username"
              value={username}
              onChange={ev => setUsername(ev.target.value)}
              disabled={busy}
            />
          </label>
          <label className="auth-field">
            <span className="auth-label">Password</span>
            <div className="auth-password-wrap">
              <input
                className="input auth-input auth-input--password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                placeholder="Dashboard password"
                value={password}
                onChange={ev => setPassword(ev.target.value)}
                disabled={busy}
                autoFocus
              />
              <button
                type="button"
                className="auth-password-toggle"
                onClick={() => setShowPassword(v => !v)}
                disabled={busy}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                title={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? (
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M3 3l18 18M10.58 10.58A2 2 0 0012 14a2 2 0 001.41-3.41M9.88 4.24A10.94 10.94 0 0112 5c5 0 9.27 3.11 11 7.5a11.8 11.8 0 01-4.12 4.9M6.11 6.11A11.75 11.75 0 002 12.5C3.73 16.89 8 20 13 20a10.9 10.9 0 004.12-.76" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : (
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M2 12.5C3.73 8.11 8 5 13 5s9.27 3.11 11 7.5c-1.73 4.39-6 7.5-11 7.5S3.73 16.89 2 12.5z" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                    <circle cx="13" cy="12.5" r="3" stroke="currentColor" strokeWidth="1.75" />
                  </svg>
                )}
              </button>
            </div>
          </label>
          {error ? <p className="auth-error" role="alert">{error}</p> : null}
          <button type="submit" className="btn btn--primary auth-submit" disabled={busy}>
            {busy ? <Spinner size={18} /> : 'Sign in'}
          </button>
          <button
            type="button"
            className="btn btn--ghost auth-forgot-link"
            disabled={busy}
            onClick={() => setForgot(true)}
          >
            Forgot password?
          </button>
        </form>
        <p className="auth-footnote">
          Operator access only. Contact your admin if you need credentials.
        </p>
      </div>
    </div>
  )
}
