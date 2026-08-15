import React, { useEffect, useRef } from 'react'
import { Button } from './ui/Button.jsx'

const ICONS = {
  danger: '⏹',
  warn: '⚠️',
  default: '❓',
}

export function ConfirmDialog({
  title,
  message,
  details = [],
  cleared = [],
  kept = [],
  variant = 'default',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
}) {
  const clearedItems = cleared.length ? cleared : details
  const cancelRef = useRef(null)

  useEffect(() => {
    cancelRef.current?.focus()
    function onKey(e) {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <div className="modal-backdrop confirm-backdrop" onClick={onCancel} role="presentation">
      <div
        className={`confirm-card confirm-card--${variant}`}
        onClick={(e) => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-desc"
      >
        <div className={`confirm-card-icon confirm-card-icon--${variant}`} aria-hidden>
          {ICONS[variant] || ICONS.default}
        </div>
        <h2 id="confirm-dialog-title" className="confirm-card-title">
          {title}
        </h2>
        {message && (
          <p id="confirm-dialog-desc" className="confirm-card-message">
            {message}
          </p>
        )}
        {clearedItems.length > 0 && (
          <div className="confirm-card-section">
            <p className="confirm-card-section-title confirm-card-section-title--cleared">
              Will be reset to zero
            </p>
            <ul className="confirm-card-details confirm-card-details--cleared">
              {clearedItems.map((line, i) => (
                <li key={`c-${i}`}>{line}</li>
              ))}
            </ul>
          </div>
        )}
        {kept.length > 0 && (
          <div className="confirm-card-section">
            <p className="confirm-card-section-title confirm-card-section-title--kept">
              Not deleted — kept as-is
            </p>
            <ul className="confirm-card-details confirm-card-details--kept">
              {kept.map((line, i) => (
                <li key={`k-${i}`}>{line}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="confirm-card-actions">
          <Button variant="ghost" ref={cancelRef} onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button
            variant={variant === 'danger' ? 'danger' : variant === 'warn' ? 'warning' : 'primary'}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}
