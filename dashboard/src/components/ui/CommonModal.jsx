import React, { useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'

/**
 * CommonModal — unified reusable modal system for the entire application.
 *
 * Usage:
 *   <CommonModal
 *     open={true}
 *     type="danger"
 *     title="Remove payment proof?"
 *     description="This action cannot be undone."
 *     confirmText="Remove"
 *     cancelText="Cancel"
 *     onConfirm={handleRemove}
 *     onClose={close}
 *     loading={isDeleting}
 *     error={errorMsg}
 *     confirmDisabled={!isValid}
 *   >
 *     <MyFormContent />
 *   </CommonModal>
 *
 * Props:
 *   open            - boolean, controls visibility
 *   type            - "default" | "form" | "info" | "success" | "warning" | "danger"
 *   title           - string, modal heading
 *   description     - string, subtitle below title
 *   confirmText     - string, action button label (default: "Confirm")
 *   cancelText      - string, cancel button label (default: "Cancel")
 *   onConfirm       - function, called when action button clicked
 *   onClose         - function, called on cancel/escape/backdrop click
 *   loading         - boolean, shows spinner and disables buttons
 *   error           - string, displays error message inside modal
 *   confirmDisabled - boolean, disables the action button
 *   hideCancel      - boolean, hides the cancel button
 *   hideConfirm     - boolean, hides the confirm button
 *   children        - ReactNode, dynamic body content (forms, messages, etc.)
 *   className       - string, additional class on the card
 *   wide            - boolean, wider modal for complex forms
 */
export function CommonModal({
  open = false,
  type = 'default',
  title,
  description,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  onConfirm,
  onClose,
  loading = false,
  error = '',
  confirmDisabled = false,
  hideCancel = false,
  hideConfirm = false,
  children,
  className = '',
  wide = false,
}) {
  const cardRef = useRef(null)
  const previousFocus = useRef(null)

  // Store focus on open, restore on close
  useEffect(() => {
    if (open) {
      previousFocus.current = document.activeElement
      // Focus trap: focus the card
      const timer = setTimeout(() => {
        cardRef.current?.focus()
      }, 50)
      return () => clearTimeout(timer)
    } else if (previousFocus.current) {
      previousFocus.current.focus?.()
      previousFocus.current = null
    }
  }, [open])

  // Escape key
  useEffect(() => {
    if (!open) return undefined
    function onKey(e) {
      if (e.key === 'Escape' && !loading) {
        e.stopPropagation()
        onClose?.()
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [open, loading, onClose])

  // Focus trap
  const handleKeyDown = useCallback((e) => {
    if (e.key !== 'Tab') return
    const card = cardRef.current
    if (!card) return
    const focusable = card.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
    if (!focusable.length) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }, [])

  if (!open) return null

  const btnVariant = type === 'danger' ? 'cm-btn--danger'
    : type === 'warning' ? 'cm-btn--warning'
    : type === 'success' ? 'cm-btn--success'
    : 'cm-btn--primary'

  const modal = (
    <div
      className="cm-backdrop"
      onClick={loading ? undefined : onClose}
      role="presentation"
      aria-hidden="true"
    >
      <div
        ref={cardRef}
        className={`cm-card ${wide ? 'cm-card--wide' : ''} ${className}`.trim()}
        onClick={e => e.stopPropagation()}
        onKeyDown={handleKeyDown}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? 'cm-title' : undefined}
        aria-describedby={description ? 'cm-desc' : undefined}
        tabIndex={-1}
      >
        {/* Header */}
        <header className="cm-header">
          <div className="cm-header-text">
            {title && <h2 id="cm-title" className="cm-title">{title}</h2>}
            {description && <p id="cm-desc" className="cm-description">{description}</p>}
          </div>
          <button
            type="button"
            className="cm-close"
            onClick={onClose}
            disabled={loading}
            aria-label="Close"
          >
            ×
          </button>
        </header>

        {/* Body */}
        {children && (
          <div className="cm-body">
            {children}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="cm-error" role="alert">
            {error}
          </div>
        )}

        {/* Footer */}
        {(!hideCancel || !hideConfirm) && (
          <footer className="cm-footer">
            {!hideCancel && (
              <button
                type="button"
                className="cm-btn cm-btn--ghost"
                onClick={onClose}
                disabled={loading}
              >
                {cancelText}
              </button>
            )}
            {!hideConfirm && (
              <button
                type="button"
                className={`cm-btn ${btnVariant}`}
                onClick={onConfirm}
                disabled={loading || confirmDisabled}
              >
                {loading && <span className="cm-spinner" aria-hidden />}
                {confirmText}
              </button>
            )}
          </footer>
        )}
      </div>
    </div>
  )

  return createPortal(modal, document.body)
}

export default CommonModal
