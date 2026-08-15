import React, { useState } from 'react'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { useDialogA11y } from '../hooks/useDialogA11y.js'

const API_BASE =
  typeof window !== 'undefined' && window.location.port === '3000'
    ? ''
    : typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.host}`
      : ''

function slugId(text) {
  return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || `item_${Date.now()}`
}

const LINK_FIELDS = [
  { key: 'id', label: 'ID (stable slug)', placeholder: 'e.g. drive_folder' },
  { key: 'title', label: 'Title / label' },
  { key: 'url', label: 'URL', placeholder: 'https://' },
  { key: 'notes', label: 'Notes', full: true },
]

function VaultModal({ title, fields, form, onChange, onSave, onClose, error }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(true, onClose)
  return (
    <div className="dr-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="dr-modal cand-card" ref={dialogRef} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2 className="cand-title">{title}</h2>
        {error && <p className="dr-error">{error}</p>}
        <div className="dr-form-grid">
          {fields.map((f) => (
            <label key={f.key} className={f.full ? 'dr-form-full' : ''}>
              {f.label}
              {f.type === 'textarea' ? (
                <textarea className="cand-input" rows={f.rows || 3} value={form[f.key] || ''} onChange={(e) => onChange({ ...form, [f.key]: e.target.value })} />
              ) : (
                <input className="cand-input" type={f.type || 'text'} placeholder={f.placeholder || ''} value={form[f.key] || ''} readOnly={f.readOnly} onChange={(e) => onChange({ ...form, [f.key]: e.target.value })} />
              )}
            </label>
          ))}
        </div>
        <div className="dr-modal-actions">
          <button type="button" className="cand-btn" onClick={onClose}>Cancel</button>
          <button type="button" className="cand-btn cand-btn--primary" onClick={onSave}>Save</button>
        </div>
      </div>
    </div>
  )
}

export function DataRoomLinksTab({ resources = [], onReload }) {
  const { confirm } = useConfirm()
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')

  const openAdd = () => {
    setModalError('')
    setModal({ mode: 'create', form: { id: '', title: '', url: '', notes: '' } })
  }

  const openEdit = (row) => {
    setModalError('')
    setModal({ mode: 'edit', id: row.id, form: { id: row.id, title: row.title || '', url: row.url || '', notes: row.notes || '' } })
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete link?', message: `Remove "${row.title || row.url}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await fetch(`${API_BASE}/data-room/credentials/vault/resources/${row.id}`, { method: 'DELETE', credentials: 'include' })
    onReload()
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    const body = { ...form }
    if (mode === 'create' && !body.id.trim()) body.id = slugId(body.title || body.url)
    const url = mode === 'create'
      ? `${API_BASE}/data-room/credentials/vault/resources`
      : `${API_BASE}/data-room/credentials/vault/resources/${id}`
    const method = mode === 'create' ? 'POST' : 'PATCH'
    const res = await fetch(url, { method, credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json()
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setModal(null)
    onReload()
  }

  return (
    <section className="dr-section dr-section--active dr-links-tab">
      <div className="dr-tab-header">
        <div>
          <h2 className="dr-section-title">Key Links</h2>
          <p className="dr-section-desc">Important URLs, Drive folders, tools, and reference links.</p>
        </div>
        <button type="button" className="cand-btn cand-btn--primary" onClick={openAdd}>+ Add link</button>
      </div>

      <div className="dr-tab-stats">
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Total links</span>
          </div>
          <div className="dr-tab-stat-value">{resources.length}</div>
          <div className="dr-tab-stat-sub">Important URLs, Drive folders, and tools</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Drive links</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--green">{resources.filter(r => (r.url || '').includes('drive.google')).length}</div>
          <div className="dr-tab-stat-sub">Google Drive resources</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">GitHub links</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--purple">{resources.filter(r => (r.url || '').includes('github')).length}</div>
          <div className="dr-tab-stat-sub">Repositories and code links</div>
        </div>
      </div>

      <div className="dr-tab-table-wrap">
        <table className="dr-tab-table">
          <thead>
            <tr>
              <th>Title</th>
              <th>URL</th>
              <th>Description</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {resources.length === 0 && (
              <tr><td colSpan={4} className="dr-empty">No links yet.</td></tr>
            )}
            {resources.map(row => (
              <tr key={row.id}>
                <td><strong>{row.title || row.id}</strong></td>
                <td>
                  <a href={row.url} target="_blank" rel="noopener noreferrer" className="dr-link-url">
                    {row.url ? (row.url.length > 50 ? row.url.slice(0, 50) + '…' : row.url) : '—'}
                  </a>
                </td>
                <td className="dr-muted">{row.notes || '—'}</td>
                <td>
                  <div className="dr-acct-actions">
                    <button type="button" className="cand-btn cand-btn--sm" onClick={() => openEdit(row)}>Edit</button>
                    <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={() => handleDelete(row)}>Delete</button>
                    <a href={row.url} target="_blank" rel="noopener noreferrer" className="cand-btn cand-btn--sm">Open ↗</a>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && (
        <VaultModal
          title={modal.mode === 'create' ? 'Add link' : 'Edit link'}
          fields={LINK_FIELDS.map(f => modal.mode === 'edit' && f.key === 'id' ? { ...f, readOnly: true } : f)}
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
