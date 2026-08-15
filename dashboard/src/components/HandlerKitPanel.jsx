import React, { useCallback, useEffect, useState } from 'react'
import { Spinner } from '../Loader.jsx'
import { copyToClipboard } from '../utils/copyToClipboard.js'
import { ChangePasswordModal } from './ChangePasswordModal.jsx'

const API_BASE = typeof window !== 'undefined' && window.location.port === '3000'
  ? ''
  : (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.host}` : '')

export function HandlerKitPanel({ username, reference }) {
  const [kit, setKit] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState('')
  const [showPw, setShowPw] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/auth/handler-kit`, { credentials: 'include' })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || 'Could not load handler kit')
        return
      }
      setKit(data.kit || null)
    } catch {
      setError('Network error')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function onCopy(key, text) {
    const ok = await copyToClipboard(text)
    if (ok) {
      setCopied(key)
      window.setTimeout(() => setCopied(k => (k === key ? '' : k)), 1500)
    }
  }

  if (loading) {
    return <div className="handler-kit handler-kit--loading"><Spinner size={28} /></div>
  }
  if (error) {
    return <div className="handler-kit"><p className="auth-error">{error}</p></div>
  }
  if (!kit) return null

  const prompts = kit.prompts || []
  const resources = kit.resources || []

  return (
    <div className="handler-kit">
      <header className="handler-kit-head">
        <h2 className="handler-kit-title">Handler kit</h2>
        <p className="handler-kit-lead">
          Login, prompts, and links in one place — no need to ask on WhatsApp.
        </p>
      </header>

      <section className="handler-kit-block">
        <h3>Dashboard login</h3>
        <dl className="handler-kit-dl">
          <dt>Site</dt>
          <dd>
            <a href={kit.site_url} target="_blank" rel="noreferrer">{kit.site_url}</a>
            <button type="button" className="btn btn--ghost btn--xs" onClick={() => onCopy('site', kit.site_url)}>
              {copied === 'site' ? 'Copied' : 'Copy'}
            </button>
          </dd>
          <dt>Username</dt>
          <dd><code>{kit.username || username}</code></dd>
          <dt>Referrer</dt>
          <dd>{kit.reference || reference || '—'}</dd>
        </dl>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setShowPw(true)}>
          Change password
        </button>
      </section>

      <section className="handler-kit-block">
        <h3>Submit interview slot</h3>
        <p className="handler-kit-hint">Share this link with candidates instead of posting screenshots manually.</p>
        <p>
          <a href={kit.submit_slot_url} target="_blank" rel="noreferrer">{kit.submit_slot_url}</a>
          <button type="button" className="btn btn--ghost btn--xs" onClick={() => onCopy('slot', kit.submit_slot_url)}>
            {copied === 'slot' ? 'Copied' : 'Copy link'}
          </button>
        </p>
      </section>

      {prompts.length > 0 ? (
        <section className="handler-kit-block">
          <h3>Interview prompts</h3>
          <ul className="handler-kit-list">
            {prompts.map(p => (
              <li key={p.id || p.title}>
                <strong>{p.title || p.label || p.id}</strong>
                {p.body ? <pre className="handler-kit-pre">{p.body}</pre> : null}
                {p.text ? <pre className="handler-kit-pre">{p.text}</pre> : null}
                <button
                  type="button"
                  className="btn btn--ghost btn--xs"
                  onClick={() => onCopy(`p-${p.id}`, p.body || p.text || p.title || '')}
                >
                  {copied === `p-${p.id}` ? 'Copied' : 'Copy'}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {resources.length > 0 ? (
        <section className="handler-kit-block">
          <h3>Links &amp; resources</h3>
          <ul className="handler-kit-list">
            {resources.map(r => (
              <li key={r.id || r.label}>
                <strong>{r.label || r.title || r.id}</strong>
                {r.url ? (
                  <p>
                    <a href={r.url} target="_blank" rel="noreferrer">{r.url}</a>
                    <button type="button" className="btn btn--ghost btn--xs" onClick={() => onCopy(`r-${r.id}`, r.url)}>
                      {copied === `r-${r.id}` ? 'Copied' : 'Copy'}
                    </button>
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <ChangePasswordModal open={showPw} onClose={() => setShowPw(false)} />
    </div>
  )
}
