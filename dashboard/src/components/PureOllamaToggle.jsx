import React, { useCallback, useEffect, useState } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useConfirm } from '../context/ConfirmContext.jsx'

/**
 * Pure Ollama AI Mail Detection, ON/OFF, shown inside AI Mail Review.
 *
 * ON — every inbound mail reaches Ollama once the duplicate and direction
 * checks have run, and the model decides relevance. OFF — the existing
 * keyword and routing rules decide first, exactly as they do today.
 *
 * Deliberately built against the same shape as the OCR switch: same
 * admin-only write, same spelled-out confirmation, same lack of an on-screen
 * history. The change record stays on `/ai/pure-ollama-policy/audit`.
 *
 * Turning it OFF is the one worth confirming carefully: it puts the keyword
 * rules back in front of the model, and those are what dropped a real
 * interview reminder before anything read it.
 */
export function PureOllamaToggle() {
  const auth = useAuth()
  const { confirm } = useConfirm()
  const isAdmin = String(auth?.role || '').trim().toLowerCase() === 'admin'

  const [policy, setPolicy] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/ai/pure-ollama-policy`, { credentials: 'include' })
      const type = String(res.headers?.get?.('content-type') || '')
      if (!res.ok || !type.includes('application/json')) throw new Error('unavailable')
      setPolicy(await res.json())
      setError('')
    } catch {
      setPolicy(null)
      setError('Pure Ollama setting unavailable')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const toggle = useCallback(async () => {
    if (!policy || saving || !isAdmin) return
    const turningOff = Boolean(policy.enabled)
    const ok = await confirm({
      title: turningOff
        ? 'Turn Pure Ollama AI Mail Detection off?'
        : 'Turn Pure Ollama AI Mail Detection on?',
      message: turningOff
        ? 'The keyword and routing rules decide relevance again, before Ollama reads anything. Mail those rules do not recognise is dropped without reaching the model.'
        : 'Every inbound mail goes to Ollama after the duplicate and direction checks. The model decides relevance instead of the keyword rules.',
      details: turningOff
        ? ['Prefilter and routing rules run first', 'Unrecognised mail is ignored silently']
        : ['Ollama classifies every inbound mail', 'Booking safety checks are unchanged'],
      confirmLabel: turningOff ? 'Turn detection off' : 'Turn detection on',
      cancelLabel: 'Cancel',
      variant: turningOff ? 'warn' : 'default',
    })
    if (!ok) return

    setSaving(true)
    try {
      const res = await fetch(`${API}/ai/pure-ollama-policy`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !turningOff }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(res.status === 403
          ? 'Only an admin can change Pure Ollama AI Mail Detection.'
          : (data.detail || data.message || 'Could not change the detection setting.'))
      }
      setPolicy(data)
      setError('')
    } catch (err) {
      setError(err.message || 'Could not change the detection setting.')
    } finally {
      setSaving(false)
    }
  }, [policy, saving, isAdmin, confirm])

  if (!policy) {
    return (
      <span className="sot-ocr-toggle sot-ocr-toggle--muted">
        {error || 'Pure Ollama AI Mail Detection …'}
      </span>
    )
  }

  return (
    <span className="sot-ocr-toggle">
      <span className="sot-ocr-toggle__label">Pure Ollama AI Mail Detection</span>
      <button
        type="button"
        className={`sot-ocr-toggle__switch${policy.enabled ? ' sot-ocr-toggle__switch--on' : ''}`}
        onClick={toggle}
        disabled={saving || !isAdmin}
        aria-pressed={Boolean(policy.enabled)}
        title={isAdmin
          ? 'ON sends every mail to Ollama first; OFF uses the current keyword rules'
          : 'Only an admin can change Pure Ollama AI Mail Detection'}
      >
        {saving ? '…' : policy.enabled ? 'ON' : 'OFF'}
      </button>
      {error && <span className="sot-ocr-toggle__error" role="alert">{error}</span>}
    </span>
  )
}

export default PureOllamaToggle
