import React, { useRef, useState } from 'react'

export function SubmitSlotFileDrop({
  label,
  hint,
  accept = 'image/*',
  disabled = false,
  busy = false,
  file,
  previewUrl,
  onFile,
  compact = false,
}) {
  const inputRef = useRef(null)
  const [drag, setDrag] = useState(false)

  function pick() {
    if (!disabled && !busy) inputRef.current?.click()
  }

  function onInput(ev) {
    const f = ev.target.files?.[0] || null
    onFile(f)
    ev.target.value = ''
  }

  function onDrop(ev) {
    ev.preventDefault()
    setDrag(false)
    if (disabled || busy) return
    const f = ev.dataTransfer.files?.[0]
    if (f && f.type.startsWith('image/')) onFile(f)
  }

  return (
    <div className={`submit-slot-drop-wrap${compact ? ' submit-slot-drop-wrap--compact' : ''}`}>
      {label ? <span className="submit-slot-field-label">{label}</span> : null}
      <div
        className={[
          'submit-slot-drop',
          drag ? 'submit-slot-drop--drag' : '',
          file ? 'submit-slot-drop--has-file' : '',
          disabled || busy ? 'submit-slot-drop--disabled' : '',
        ].filter(Boolean).join(' ')}
        onDragOver={ev => { ev.preventDefault(); if (!disabled && !busy) setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        onClick={pick}
        onKeyDown={ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick() } }}
        role="button"
        tabIndex={disabled || busy ? -1 : 0}
        aria-disabled={disabled || busy}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          className="submit-slot-drop-input"
          disabled={disabled || busy}
          onChange={onInput}
          tabIndex={-1}
        />
        {previewUrl ? (
          <div className="submit-slot-drop-preview">
            <img src={previewUrl} alt="" />
          </div>
        ) : (
          <div className="submit-slot-drop-icon" aria-hidden="true">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M12 16V4m0 0L8 8m4-4 4 4" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M4 14v2a4 4 0 004 4h8a4 4 0 004-4v-2" strokeLinecap="round" />
            </svg>
          </div>
        )}
        <div className="submit-slot-drop-copy">
          {file ? (
            <>
              <strong>{file.name}</strong>
              <span>Tap to replace</span>
            </>
          ) : (
            <>
              <strong>{compact ? 'Upload screenshot' : 'Drop invite screenshot here'}</strong>
              <span>or tap to browse · PNG, JPG, WebP</span>
            </>
          )}
        </div>
      </div>
      {hint ? <p className="submit-slot-drop-hint">{hint}</p> : null}
    </div>
  )
}
