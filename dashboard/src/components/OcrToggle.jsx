import React, { useCallback, useEffect, useState } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useConfirm } from '../context/ConfirmContext.jsx'

/**
 * Project-wide OCR ON/OFF, shown inside AI Mail Review.
 *
 * This is the whole OCR product surface. It replaces the standalone Settings
 * page without becoming a second one: same `/ai/ocr-policy` routes, same
 * admin-only write, same spelled-out confirmation. Turning OCR off stops
 * Tesseract everywhere — invite extraction, screenshot parsing, payment and
 * proof reading — leaving Ollama to do all of it alone, which is why the
 * consequence is confirmed rather than implied.
 *
 * Deliberately no history list and no status block: the change record stays on
 * `/ai/ocr-policy/audit` for compliance, not on screen.
 */
export function OcrToggle() {
  const auth = useAuth()
  const { confirm } = useConfirm()
  const isAdmin = String(auth?.role || '').trim().toLowerCase() === 'admin'

  const [policy, setPolicy] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/ai/ocr-policy`, { credentials: 'include' })
      const type = String(res.headers?.get?.('content-type') || '')
      if (!res.ok || !type.includes('application/json')) throw new Error('unavailable')
      setPolicy(await res.json())
      setError('')
    } catch {
      setPolicy(null)
      setError('OCR setting unavailable')
    }
  }, [])

  useEffect(() => { load() }, [load])

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
      setError('')
    } catch (err) {
      setError(err.message || 'Could not change the OCR setting.')
    } finally {
      setSaving(false)
    }
  }, [policy, saving, isAdmin, confirm])

  if (!policy) {
    return <span className="sot-ocr-toggle sot-ocr-toggle--muted">{error || 'OCR …'}</span>
  }

  return (
    <span className="sot-ocr-toggle">
      <span className="sot-ocr-toggle__label">OCR</span>
      <button
        type="button"
        className={`sot-ocr-toggle__switch${policy.enabled ? ' sot-ocr-toggle__switch--on' : ''}`}
        onClick={toggle}
        disabled={saving || !isAdmin}
        aria-pressed={Boolean(policy.enabled)}
        title={isAdmin ? 'Project-wide OCR switch' : 'Only an admin can change the OCR policy'}
      >
        {saving ? '…' : policy.enabled ? 'ON' : 'OFF'}
      </button>
      {error && <span className="sot-ocr-toggle__error" role="alert">{error}</span>}
    </span>
  )
}

export default OcrToggle
