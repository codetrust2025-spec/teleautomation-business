import React, { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { useDialogA11y } from '../hooks/useDialogA11y.js'
import { copyToClipboard } from '../utils/copyToClipboard.js'

const API_BASE =
  typeof window !== 'undefined' && window.location.port === '3000'
    ? ''
    : typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.host}`
      : ''

function slugId(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40) || `item_${Date.now()}`
}

function CopyChip({ label, text, copyKey, activeKey, onCopy }) {
  const copied = activeKey === copyKey
  return (
    <button
      type="button"
      className={`dr-copy-btn${copied ? ' dr-copy-btn--copied' : ''}`}
      title={`Copy ${label}`}
      onClick={() => onCopy(copyKey, text)}
    >
      {copied ? '✓' : '📋'}
    </button>
  )
}

function accountStatus(row) {
  const label = String(row.label || '').toLowerCase()
  if (label.includes('deprecated') || label.includes('legacy')) return 'old'
  if (label.includes('current') || label.includes('2026')) return 'active'
  return 'active'
}

function isPrimary(row) {
  const label = String(row.label || '').toLowerCase()
  return label.includes('current') || label.includes('2026')
}

// What an operator actually has to type.
//
// `id` and `service` were asked for on this form and neither needed to be. The
// id is a stable slug the backend requires but nobody chooses: `slugId` already
// derived one from the title whenever the box was left blank, so the field only
// ever offered a way to get it wrong. `service` was free text rendered with a
// "Gmail" fallback in the table, which is what almost every row said anyway.
//
// The id is still generated and still sent; it is just no longer asked for.
const SVC_FIELDS = [
  { key: 'label', label: 'Account name', placeholder: 'e.g. Karthik Gmail (current)', full: true },
  { key: 'username', label: 'Username / email', placeholder: 'name@example.com' },
  { key: 'password', label: 'Password', type: 'password' },
  { key: 'notes', label: 'Notes (optional)', type: 'textarea', rows: 3, full: true },
]

function VaultModal({ title, fields, form, onChange, onSave, onClose, error }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(true, onClose)
  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  return createPortal(
    <div className="dr-modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="dr-modal cand-card"
        ref={dialogRef} role="dialog"
        aria-modal="true"
        aria-labelledby="dr-vault-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="dr-vault-modal-title" className="cand-title">{title}</h2>
        {error && <p className="dr-error">{error}</p>}
        <div className="dr-form-grid">
          {fields.map((f) => (
            <label key={f.key} className={f.full ? 'dr-form-full' : ''}>
              {f.label}
              {f.type === 'textarea' ? (
                <textarea
                  className="cand-input"
                  rows={f.rows || 3}
                  value={form[f.key] || ''}
                  onChange={(e) => onChange({ ...form, [f.key]: e.target.value })}
                />
              ) : (
                <input
                  className="cand-input"
                  type={f.type || 'text'}
                  placeholder={f.placeholder || ''}
                  value={form[f.key] || ''}
                  readOnly={f.readOnly}
                  onChange={(e) => onChange({ ...form, [f.key]: e.target.value })}
                />
              )}
            </label>
          ))}
        </div>
        <div className="dr-modal-actions">
          <button type="button" className="cand-btn" onClick={onClose}>Cancel</button>
          <button type="button" className="cand-btn cand-btn--primary" onClick={onSave}>Save</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

export function DataRoomAccountsTab({ accounts = [], onReload }) {
  const { confirm } = useConfirm()
  const [activeKey, setActiveKey] = useState(null)
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')
  const [search, setSearch] = useState('')

  const onCopy = useCallback(async (key, text) => {
    const ok = await copyToClipboard(text)
    setActiveKey(ok ? key : null)
    if (ok) window.setTimeout(() => setActiveKey(k => (k === key ? null : k)), 1600)
  }, [])

  const openAdd = () => {
    setModalError('')
    setModal({ mode: 'create', form: { id: '', label: '', service: '', username: '', password: '', notes: '' } })
  }

  const openEdit = (row) => {
    setModalError('')
    setModal({ mode: 'edit', id: row.id, form: { id: row.id, label: row.label || '', service: row.service || '', username: row.username || '', password: row.password || '', notes: row.notes || '' } })
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete account?', message: `Remove "${row.label || row.id}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await fetch(`${API_BASE}/data-room/credentials/vault/service_accounts/${row.id}`, { method: 'DELETE', credentials: 'include' })
    onReload()
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    const body = { ...form }
    if (mode === 'create' && !body.id.trim()) body.id = slugId(body.label || body.username)
    const url = mode === 'create'
      ? `${API_BASE}/data-room/credentials/vault/service_accounts`
      : `${API_BASE}/data-room/credentials/vault/service_accounts/${id}`
    const method = mode === 'create' ? 'POST' : 'PATCH'
    const res = await fetch(url, { method, credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json()
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setModal(null)
    onReload()
  }

  const totalAccounts = accounts.length
  const activeAccounts = accounts.filter(r => accountStatus(r) === 'active').length
  const oldAccounts = accounts.filter(r => accountStatus(r) === 'old').length
  const primaryAccounts = accounts.filter(r => isPrimary(r)).length

  const filtered = search.trim()
    ? accounts.filter(r => {
        const q = search.toLowerCase()
        return (r.label || '').toLowerCase().includes(q) ||
          (r.username || '').toLowerCase().includes(q) ||
          (r.service || '').toLowerCase().includes(q)
      })
    : accounts

  return (
    <section className="dr-section dr-section--active dr-accounts-tab">
      {/* Header */}
      <div className="dr-tab-header">
        <div>
          <h2 className="dr-section-title">Accounts</h2>
          <p className="dr-section-desc">Manage Gmail and service accounts used across operations.</p>
        </div>
        <div className="dr-tab-header-actions">
          <div className="dr-search-wrap">
            <span className="dr-search-icon">🔍</span>
            <input
              type="search"
              className="cand-input dr-search-input"
              placeholder="Search accounts..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <button type="button" className="cand-btn cand-btn--primary" onClick={openAdd}>+ Add account</button>
        </div>
      </div>

      {/* Stats row */}
      <div className="dr-tab-stats">
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Total accounts</span>
          </div>
          <div className="dr-tab-stat-value">{totalAccounts}</div>
          <div className="dr-tab-stat-sub">{activeAccounts} active · {oldAccounts} deprecated</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Active</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--green">{activeAccounts}</div>
          <div className="dr-tab-stat-sub">Currently in use</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Locked / Old</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--yellow">{oldAccounts}</div>
          <div className="dr-tab-stat-sub">Deprecated or locked accounts</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Primary accounts</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--purple">{primaryAccounts}</div>
          <div className="dr-tab-stat-sub">Marked as current primary</div>
        </div>
      </div>

      {/* Table */}
      <div className="dr-tab-table-wrap">
        <table className="dr-tab-table">
          <thead>
            <tr>
              <th>Account</th>
              <th>Type</th>
              <th>Username</th>
              <th>Password</th>
              <th>Status</th>
              <th>Last used</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan={7} className="dr-empty">No accounts found.</td></tr>
            )}
            {filtered.map(row => {
              const status = accountStatus(row)
              const primary = isPrimary(row)
              return (
                <tr key={row.id}>
                  <td>
                    <div className="dr-acct-cell">
                      <span className={`dr-acct-dot dr-acct-dot--${status}`} />
                      <div>
                        <span className="dr-acct-name">{row.label || row.id}</span>
                        {primary && <span className="dr-acct-primary-badge">★ Primary</span>}
                      </div>
                    </div>
                  </td>
                  <td>{row.service || 'Gmail'}</td>
                  <td>
                    <div className="dr-acct-cred-cell">
                      <code>{row.username || '—'}</code>
                      {row.username && <CopyChip label="Copy" text={row.username} copyKey={`${row.id}-user`} activeKey={activeKey} onCopy={onCopy} />}
                    </div>
                  </td>
                  <td>
                    <div className="dr-acct-cred-cell">
                      <code className="dr-creds-pass">{row.password || '—'}</code>
                      {row.password && <CopyChip label="Copy" text={row.password} copyKey={`${row.id}-pass`} activeKey={activeKey} onCopy={onCopy} />}
                    </div>
                  </td>
                  <td>
                    <span className={`dr-acct-status dr-acct-status--${status}`}>
                      ● {status === 'active' ? 'Active' : 'Old'}
                    </span>
                  </td>
                  <td className="dr-muted">{row.last_used || '—'}</td>
                  <td>
                    <div className="dr-acct-actions">
                      <button type="button" className="cand-btn cand-btn--sm" onClick={() => openEdit(row)}>Edit</button>
                      <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={() => handleDelete(row)}>Delete</button>
                      <button
                        type="button"
                        className={`dr-copy-btn${activeKey === `${row.id}-all` ? ' dr-copy-btn--copied' : ''}`}
                        onClick={() => onCopy(`${row.id}-all`, [row.label || row.id, row.service ? `Service: ${row.service}` : '', row.username ? `Username: ${row.username}` : '', row.password ? `Password: ${row.password}` : '', row.notes || ''].filter(Boolean).join('\n'))}
                      >
                        {activeKey === `${row.id}-all` ? '✓' : 'Copy all'}
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Tip bar */}
      <div className="dr-tip-bar">
        <span className="dr-tip-icon">ℹ</span>
        <span>Tip: Use quick actions (Copy, Copy all) to speed up access. Keep the CURRENT account as primary.</span>
      </div>

      {modal && (
        <VaultModal
          title={modal.mode === 'create' ? 'Add service account' : 'Edit service account'}
          // `id` and `service` stay on the form object so an edit preserves
          // what a row already has; they are simply not asked for.
          fields={SVC_FIELDS}
          form={modal.form}
          onChange={(f) => setModal(s => ({ ...s, form: f }))}
          onSave={handleSave}
          onClose={() => setModal(null)}
          error={modalError}
        />
      )}
    </section>
  )
}
