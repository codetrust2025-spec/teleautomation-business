import React, { useState } from 'react'
import { Spinner } from '../Loader.jsx'

const API_BASE = typeof window !== 'undefined' && window.location.port === '3000'
  ? ''
  : (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.host}` : '')

export function ForgotPasswordPanel({ onBack }) {
  const [username, setUsername] = useState('')
  const [reference, setReference] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  async function onSubmit(ev) {
    ev.preventDefault()
    setError('')
    if (!username.trim() || !reference.trim()) {
      setError('Enter your login username and referrer name')
      return
    }
    if (newPassword.length < 4) {
      setError('New password must be at least 4 characters')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match')
      return
    }
    setBusy(true)
    try {
      const res = await fetch(`${API_BASE}/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: username.trim(),
          reference: reference.trim(),
          new_password: newPassword,
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError(data.detail || data.message || 'Could not reset password')
        return
      }
      setDone(true)
    } catch {
      setError('Network error — try again')
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <div className="auth-forgot">
        <p className="auth-success">Password reset. You can sign in with your new password.</p>
        <button type="button" className="btn btn--ghost auth-forgot-back" onClick={onBack}>
          Back to sign in
        </button>
      </div>
    )
  }

  return (
    <form className="auth-forgot" onSubmit={onSubmit}>
      <p className="auth-forgot-lead">
        Enter your login username and referrer name (exactly as on your candidate records) to set a new password.
      </p>
      <label className="auth-field">
        <span className="auth-label">Login username</span>
        <input
          className="input auth-input"
          type="text"
          autoComplete="username"
          value={username}
          onChange={ev => setUsername(ev.target.value)}
          disabled={busy}
          placeholder="e.g. thrilok"
        />
      </label>
      <label className="auth-field">
        <span className="auth-label">Referrer name</span>
        <input
          className="input auth-input"
          type="text"
          value={reference}
          onChange={ev => setReference(ev.target.value)}
          disabled={busy}
          placeholder="e.g. Thrilok"
        />
      </label>
      <label className="auth-field">
        <span className="auth-label">New password</span>
        <input
          className="input auth-input"
          type="password"
          autoComplete="new-password"
          value={newPassword}
          onChange={ev => setNewPassword(ev.target.value)}
          disabled={busy}
        />
      </label>
      <label className="auth-field">
        <span className="auth-label">Confirm password</span>
        <input
          className="input auth-input"
          type="password"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={ev => setConfirmPassword(ev.target.value)}
          disabled={busy}
        />
      </label>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      <button type="submit" className="btn btn--primary auth-submit" disabled={busy}>
        {busy ? <Spinner size={18} /> : 'Reset password'}
      </button>
      <button type="button" className="btn btn--ghost auth-forgot-back" onClick={onBack} disabled={busy}>
        Back to sign in
      </button>
    </form>
  )
}
