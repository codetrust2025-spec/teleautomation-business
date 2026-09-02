import React, { useState } from 'react'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { useDialogA11y } from '../hooks/useDialogA11y.js'

const API_BASE =
  typeof window !== 'undefined' && window.location.port === '3000'
    ? ''
    : typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.host}`
      : ''

async function readApiResponse(response) {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) return response.json()
  const text = await response.text()
  if (response.status === 413) {
    throw new Error('The PDF is too large for the server. Maximum file size is 25 MB.')
  }
  throw new Error(
    response.ok
      ? 'The server returned an invalid response. Please try again.'
      : `Upload failed (${response.status}). ${text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 120)}`,
  )
}

// What a person knows that the PDF does not say for itself.
//
// The other five boxes asked for values the upload already produces:
// `create_offer_letter_from_pdf` slugs the id from the filename, reads the
// filename, size and date from the bytes, records `uploaded_at`, and writes the
// PDF to DATA_DIR/data_room/offer_letters_cache. `drive_file_id` is set to ""
// by that path and never filled in by hand. Asking for them offered no
// information and one way to get each wrong.
//
// They are still stored. `update_vault_item` merges the keys it is given, so
// omitting them from the PATCH preserves whatever the row already holds --
// which also repairs a quieter fault: the old form sent `size_kb: ''` for any
// record without one, overwriting a real value with an empty string.
const OFFER_FIELDS = [
  { key: 'candidate', label: 'Candidate name' },
  { key: 'company_name', label: 'Company name' },
  { key: 'notes', label: 'Notes (optional)', type: 'textarea', rows: 3, full: true },
]

// Only these three are ever sent. Everything else on the row is the upload's.
const EDITABLE_KEYS = ['candidate', 'company_name', 'notes']

function VaultModal({ title, fields, form, onChange, onSave, onClose, error, uploadPanel, saving }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(true, onClose)
  return (
    <div className="dr-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="dr-modal cand-card" ref={dialogRef} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2 className="cand-title">{title}</h2>
        {error && <p className="dr-error">{error}</p>}
        {uploadPanel}
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
          <button type="button" className="cand-btn cand-btn--primary" onClick={onSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function DataRoomOffersTab({ offers = [], onReload }) {
  const { confirm } = useConfirm()
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadId, setUploadId] = useState(null)
  const [uploadError, setUploadError] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [analysisMessage, setAnalysisMessage] = useState('')

  const openAdd = () => {
    setModalError('')
    setAnalysisMessage('')
    setModal({ mode: 'create', form: { candidate: '', company_name: '', notes: '' } })
  }

  const openEdit = (row) => {
    setModalError('')
    // Only the editable three are loaded. The rest of the row stays on the
    // server and is preserved by the merge, so opening and saving an existing
    // record cannot blank its filename, size, date or drive id.
    setModal({ mode: 'edit', id: row.id, row, form: { candidate: row.candidate || '', company_name: row.company_name || '', notes: row.notes || '' } })
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete offer letter?', message: `Remove "${row.filename || row.id}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await fetch(`${API_BASE}/data-room/credentials/vault/offer_letters/${row.id}`, { method: 'DELETE', credentials: 'include' })
    onReload()
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    // A new offer letter is the PDF. `upload-analyze` writes the file, derives
    // the row and switches this modal to edit, so reaching Save in create mode
    // means no PDF was chosen and there is nothing to catalogue.
    if (mode === 'create') {
      setModalError('Choose the offer letter PDF first.')
      return
    }
    // Only the editable keys. update_vault_item merges, so everything the
    // upload derived is preserved by not being sent.
    const body = Object.fromEntries(EDITABLE_KEYS.map(key => [key, form[key] ?? '']))
    const url = `${API_BASE}/data-room/credentials/vault/offer_letters/${id}`
    const method = 'PATCH'
    setSaving(true)
    setModalError('')
    try {
      const res = await fetch(url, { method, credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const data = await readApiResponse(res)
      if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
      setModal(null)
      setAnalysisMessage('')
      onReload()
    } catch (error) {
      setModalError(error.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleInitialUpload = async (file) => {
    if (!file) return
    if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
      setModalError('Please upload a PDF offer letter.')
      return
    }
    if (file.size > 25 * 1024 * 1024) {
      setModalError('PDF is too large. Maximum size is 25 MB.')
      return
    }
    setAnalyzing(true)
    setModalError('')
    setAnalysisMessage('')
    const body = new FormData()
    body.append('file', file)
    try {
      const res = await fetch(`${API_BASE}/data-room/offer-letters/upload-analyze`, {
        method: 'POST',
        credentials: 'include',
        body,
      })
      const data = await readApiResponse(res)
      if (!res.ok || data.status !== 'ok') {
        throw new Error(data.message || 'Upload or analysis failed')
      }
      const row = data.offer_letter || {}
      setModal({
        mode: 'edit',
        id: row.id,
        row,
        form: {
          candidate: row.candidate || '',
          company_name: row.company_name || '',
          notes: row.notes || '',
        },
      })
      setAnalysisMessage(data.message || 'PDF saved and fields auto-filled.')
      onReload()
    } catch (error) {
      setModalError(error.message || 'Upload or analysis failed')
    } finally {
      setAnalyzing(false)
    }
  }

  const handleUpload = async (rowId, file) => {
    if (!file) return
    setUploadId(rowId)
    setUploading(true)
    setUploadError('')
    const fd = new FormData()
    fd.append('file', file)
    try {
      const res = await fetch(`${API_BASE}/data-room/offer-letters/${rowId}/upload`, { method: 'POST', credentials: 'include', body: fd })
      const data = await readApiResponse(res)
      if (data.status !== 'ok') setUploadError(data.message || 'Upload failed')
      else onReload()
    } catch (e) {
      setUploadError(String(e))
    } finally {
      setUploading(false)
      setUploadId(null)
    }
  }

  return (
    <section className="dr-section dr-section--active dr-offers-tab">
      <div className="dr-tab-header">
        <div>
          <h2 className="dr-section-title">Offer Letters</h2>
          <p className="dr-section-desc">Catalogued offer letters for proof in Data Room vault.</p>
        </div>
        <button type="button" className="cand-btn cand-btn--primary" onClick={openAdd}>+ Add offer</button>
      </div>

      <div className="dr-tab-stats">
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Total offers</span>
          </div>
          <div className="dr-tab-stat-value">{offers.length}</div>
          <div className="dr-tab-stat-sub">Catalogued offer letters for proof</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Unique candidates</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--green">{new Set(offers.map(o => o.candidate).filter(Boolean)).size}</div>
          <div className="dr-tab-stat-sub">Distinct candidate names</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Companies</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--purple">{new Set(offers.map(o => o.company_name).filter(Boolean)).size}</div>
          <div className="dr-tab-stat-sub">Unique company names</div>
        </div>
      </div>

      {uploadError && <p className="dr-error">{uploadError}</p>}

      <div className="dr-tab-table-wrap">
        <table className="dr-tab-table">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>File</th>
              <th>Company</th>
              <th>Modified</th>
              <th>Size</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {offers.length === 0 && (
              <tr><td colSpan={6} className="dr-empty">No offer letters yet.</td></tr>
            )}
            {offers.map(row => (
              <tr key={row.id}>
                <td>{row.candidate || '—'}</td>
                <td><strong className="dr-offer-filename">{row.filename || row.id}</strong></td>
                <td>{row.company_name || '—'}</td>
                <td>{row.date_modified || '—'}</td>
                <td>{row.size_kb ? `${row.size_kb} KB` : '—'}</td>
                <td>
                  <div className="dr-acct-actions">
                    <a href={`${API_BASE}/data-room/offer-letters/${row.id}/preview`} target="_blank" rel="noopener noreferrer" className="dr-offer-icon-btn" title="View">👁</a>
                    <a href={`${API_BASE}/data-room/offer-letters/${row.id}/download`} download className="dr-offer-icon-btn" title="Download">⬇</a>
                    <button type="button" className="cand-btn cand-btn--sm" onClick={() => openEdit(row)}>Edit</button>
                    <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={() => handleDelete(row)}>Delete</button>
                    <label className={`cand-btn cand-btn--sm${uploading && uploadId === row.id ? ' cand-btn--disabled' : ''}`} title="Upload PDF">
                      {uploading && uploadId === row.id ? '…' : '↑'}
                      <input type="file" accept="application/pdf" style={{ display: 'none' }} onChange={(e) => handleUpload(row.id, e.target.files[0])} />
                    </label>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && (
        <VaultModal
          title={modal.mode === 'create' ? 'Add offer letter' : 'Edit offer letter'}
          fields={OFFER_FIELDS}
          form={modal.form}
          onChange={(f) => setModal(s => ({ ...s, form: f }))}
          onSave={handleSave}
          onClose={() => setModal(null)}
          error={modalError}
          saving={saving}
          uploadPanel={modal.mode === 'create' ? (
            <div className={`dr-offer-upload-panel${analyzing ? ' dr-offer-upload-panel--busy' : ''}`}>
              <div className="dr-offer-upload-copy">
                <strong>{analyzing ? 'Saving and analyzing PDF…' : 'Upload offer letter PDF'}</strong>
                <span>
                  The PDF is stored first, then the candidate and company are
                  filled in from it for you to check. Filename, size and date are
                  read from the file.
                </span>
              </div>
              <label className={`cand-btn cand-btn--primary${analyzing ? ' cand-btn--disabled' : ''}`}>
                {analyzing ? 'Analyzing…' : 'Choose PDF'}
                <input
                  type="file"
                  accept="application/pdf,.pdf"
                  disabled={analyzing}
                  style={{ display: 'none' }}
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    event.target.value = ''
                    handleInitialUpload(file)
                  }}
                />
              </label>
            </div>
          ) : (
            /* A saved record: what it holds, and a way to open it. The
               filename, size and date are read from the PDF, so they are shown
               rather than asked for. Preview used to appear only in the moment
               after an upload; it belongs on every edit. */
            <div className="dr-offer-attached">
              <span className="dr-offer-attached-file">
                {modal.row?.filename
                  ? `${modal.row.filename}${modal.row.size_kb ? ` · ${modal.row.size_kb} KB` : ''}${modal.row.date_modified ? ` · ${modal.row.date_modified}` : ''}`
                  : 'No PDF attached to this record.'}
              </span>
              {modal.row?.has_pdf && (
                <a
                  className="cand-btn cand-btn--sm"
                  href={`${API_BASE}/data-room/offer-letters/${modal.id}/preview`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  View PDF
                </a>
              )}
              {analysisMessage && <p className="dr-offer-analysis-ok">{analysisMessage}</p>}
            </div>
          )}
        />
      )}
    </section>
  )
}
