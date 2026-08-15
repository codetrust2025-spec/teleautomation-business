import React, { useEffect, useRef, useState } from 'react'
import { useDialogA11y } from '../hooks/useDialogA11y.js'
import { Spinner } from '../Loader.jsx'

const API_BASE = typeof window !== 'undefined' && window.location.port === '3000'
  ? ''
  : (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.host}` : '')

export function ChangePasswordModal({ open, onClose, onSuccess }) {
  // Focus in, Tab trapped, focus restored to the trigger. Escape stays
  // below so this dialog's own guard keeps its exact behaviour.
  const dialogRef = useDialogA11y(open, onClose, { closeOnEscape: false })

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const currentRef = useRef(null)

  useEffect(() => {
    if (!open) {
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setBusy(false)
      setError('')
      setDone(false)
      return undefined
    }
    const t = setTimeout(() => currentRef.current?.focus(), 50)
    function onKey(ev) {
      if (ev.key === 'Escape' && !busy) onClose?.()
    }
    document.addEventListener('keydown', onKey)
    return () => {
      clearTimeout(t)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, busy, onClose])

  if (!open) return null

  async function onSubmit(ev) {
    ev.preventDefault()
    setError('')
    if (!currentPassword) {
      setError('Enter your current password')
      return
    }
    if (newPassword.length < 4) {
      setError('New password must be at least 4 characters')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('New passwords do not match')
      return
    }
    setBusy(true)
    try {
      const res = await fetch(`${API_BASE}/auth/change-password`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError(data.detail || data.message || 'Could not update password')
        return
      }
      setDone(true)
      onSuccess?.()
    } catch {
      setError('Network error — try again')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop confirm-backdrop" onClick={busy ? undefined : onClose} role="presentation">
      <div
        className="cand-modal cand-modal--narrow"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="change-pw-title"
        onClick={ev => ev.stopPropagation()}
      >
        <header className="cand-modal-header">
          <h3 className="cand-modal-title" id="change-pw-title">Change password</h3>
          <p className="cand-modal-sub">Update your dashboard login password.</p>
        </header>
        {done ? (
          <div className="cand-modal-body">
            <p className="auth-success">Password updated successfully.</p>
            <footer className="cand-modal-footer">
              <button type="button" className="cand-btn cand-btn--primary" onClick={onClose}>Done</button>
            </footer>
          </div>
        ) : (
          <form className="cand-modal-body" onSubmit={onSubmit}>
            <label className="cand-field">
              <span className="cand-field-label">Current password</span>
              <input
                ref={currentRef}
                className="cand-input"
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={ev => setCurrentPassword(ev.target.value)}
                disabled={busy}
              />
            </label>
            <label className="cand-field">
              <span className="cand-field-label">New password</span>
              <input
                className="cand-input"
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={ev => setNewPassword(ev.target.value)}
                disabled={busy}
              />
            </label>
            <label className="cand-field">
              <span className="cand-field-label">Confirm new password</span>
              <input
                className="cand-input"
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={ev => setConfirmPassword(ev.target.value)}
                disabled={busy}
              />
            </label>
            {error ? <p className="cand-error" role="alert">{error}</p> : null}
            <footer className="cand-modal-footer">
              <button type="button" className="cand-btn cand-btn--ghost" onClick={onClose} disabled={busy}>Cancel</button>
              <button type="submit" className="cand-btn cand-btn--primary" disabled={busy}>
                {busy ? 'Saving…' : 'Save password'}
              </button>
            </footer>
          </form>
        )}
      </div>
    </div>
  )
}
