import React, { useCallback, useEffect, useState } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useConfirm } from '../context/ConfirmContext.jsx'

/**
 * Admin control for the project-wide OCR switch.
 *
 * Turning OCR off stops Tesseract everywhere — invite extraction, screenshot
 * parsing, payment and proof reading — leaving Ollama to do all of it alone.
 * That is a wide blast radius for one toggle, so the consequence is spelled out
 * and confirmed rather than implied.
 *
 * Operations owns this capability. In the monolith it lived inside Marketing's
 * AI-settings overlay, which is why the split lost it; it is rebuilt here
 * against the Operations routes rather than carried over with that overlay.
 */
export function OcrPolicyPanel() {
  const auth = useAuth()
  const confirm = useConfirm()
  const isAdmin = String(auth?.role || '').trim().toLowerCase() === 'admin'

  const [policy, setPolicy] = useState(null)
  const [audit, setAudit] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API}/ai/ocr-policy`, { credentials: 'include' })
      const type = String(res.headers?.get?.('content-type') || '')
      if (!res.ok || !type.includes('application/json')) {
        throw new Error(res.status === 403
          ? 'You do not have permission to view the OCR policy.'
          : 'Could not read the OCR setting.')
      }
      setPolicy(await res.json())
    } catch (err) {
      setError(err.message || 'Could not read the OCR setting.')
      setPolicy(null)
    } finally {
      setLoading(false)
    }
  }, [])

  // The audit trail is admin-only; a non-admin viewer simply sees no history
  // rather than an error they cannot act on.
  const loadAudit = useCallback(async () => {
    if (!isAdmin) return
    try {
      const res = await fetch(`${API}/ai/ocr-policy/audit?limit=20`, { credentials: 'include' })
      if (!res.ok) return
      const data = await res.json()
      setAudit(Array.isArray(data.entries) ? data.entries : [])
    } catch {
      /* history is supplementary; its absence must not block the control */
    }
  }, [isAdmin])

  useEffect(() => { load(); loadAudit() }, [load, loadAudit])

  const toggle = useCallback(async () => {
    if (!policy || saving || !isAdmin) return
    const turningOff = Boolean(policy.enabled)
    const ok = await confirm({
      title: turningOff ? 'Turn OCR off for the whole project?' : 'Turn OCR on for the whole project?',
      message: turningOff
        ? 'Tesseract stops running everywhere. Ollama handles every read alone, so some may fail and need manual entry.'
        : 'Tesseract runs alongside Ollama again and cross-checks what the AI reads.',
      details: turningOff
        ? ['Invite extraction', 'Screenshot parsing', 'Payment and proof reading']
        : undefined,
      confirmLabel: turningOff ? 'Turn OCR off' : 'Turn OCR on',
      cancelLabel: 'Cancel',
      variant: turningOff ? 'warn' : 'default',
    })
    if (!ok) return

    setSaving(true)
    setError('')
    setNotice('')
    try {
      const res = await fetch(`${API}/ai/ocr-policy`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !turningOff }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(res.status === 403
          ? 'Only an admin can change the OCR policy.'
          : (data.detail || data.message || 'Could not change the OCR setting.'))
      }
      setPolicy(data)
      setNotice(`OCR is now ${data.enabled ? 'ON' : 'OFF'} for the whole project.`)
      loadAudit()
    } catch (err) {
      setError(err.message || 'Could not change the OCR setting.')
    } finally {
      setSaving(false)
    }
  }, [policy, saving, isAdmin, confirm, loadAudit])

  return (
    <section className="panel ocr-policy" aria-labelledby="ocr-policy-title">
      <header className="panel-head">
        <h2 id="ocr-policy-title">OCR policy</h2>
        <p className="panel-sub">
          Controls Tesseract across the whole project. Ollama AI is unaffected.
        </p>
      </header>

      {loading && <p className="ocr-policy__state" role="status">Loading the OCR setting…</p>}

      {!loading && error && (
        <div className="ocr-policy__state ocr-policy__state--error" role="alert">
          {error}
          <button type="button" className="ocr-policy__retry" onClick={load}>Retry</button>
        </div>
      )}

      {!loading && !error && policy && (
        <>
          <div className="ocr-policy__row">
            <div>
              <p className="ocr-policy__value">
                OCR is <strong>{policy.enabled ? 'ON' : 'OFF'}</strong>
              </p>
              <p className="ocr-policy__meta">
                Processing mode: {policy.mode || 'unknown'} · set by {policy.source === 'admin' ? 'an admin' : 'the environment default'}
                {policy.source === 'environment' && typeof policy.env_default === 'boolean'
                  ? ` (${policy.env_default ? 'on' : 'off'})`
                  : ''}
              </p>
              {policy.updated_at && (
                <p className="ocr-policy__meta">
                  Last changed {policy.updated_at}{policy.updated_by ? ` by ${policy.updated_by}` : ''}
                </p>
              )}
            </div>
            <button
              type="button"
              className={`ocr-policy__toggle${policy.enabled ? ' ocr-policy__toggle--on' : ''}`}
              onClick={toggle}
              disabled={saving || !isAdmin}
              aria-pressed={Boolean(policy.enabled)}
              title={isAdmin ? undefined : 'Only an admin can change the OCR policy'}
            >
              {saving ? 'Saving…' : policy.enabled ? 'Turn OCR off' : 'Turn OCR on'}
            </button>
          </div>

          {!isAdmin && (
            <p className="ocr-policy__meta">Only an admin can change this setting.</p>
          )}

          {notice && <p className="ocr-policy__state ocr-policy__state--ok" role="status">{notice}</p>}

          {isAdmin && audit.length > 0 && (
            <div className="ocr-policy__audit">
              <h3 className="ocr-policy__audit-title">Recent changes</h3>
              <ul className="ocr-policy__audit-list">
                {audit.map((entry, i) => (
                  <li key={`${entry.at || entry.updated_at || i}-${i}`}>
                    <span>{entry.enabled ? 'Turned ON' : 'Turned OFF'}</span>
                    {(entry.actor || entry.updated_by) && <span> by {entry.actor || entry.updated_by}</span>}
                    {(entry.at || entry.updated_at) && <span> · {entry.at || entry.updated_at}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  )
}

export default OcrPolicyPanel
