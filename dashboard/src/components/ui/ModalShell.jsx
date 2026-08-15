import React, { useEffect, useRef } from 'react'

export function ModalShell({
  title,
  subtitle,
  open = true,
  onClose,
  role = 'dialog',
  labelledBy,
  className = '',
  actions,
  children,
}) {
  const panelRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    function onKey(e) {
      if (e.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  const titleId = labelledBy || 'modal-shell-title'

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        ref={panelRef}
        className={['modal-card', className].filter(Boolean).join(' ')}
        onClick={e => e.stopPropagation()}
        role={role}
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
      >
        {(title || subtitle || actions) && (
          <header className="modal-header">
            <div>
              {title && <h2 id={titleId}>{title}</h2>}
              {subtitle && <p className="modal-sub">{subtitle}</p>}
            </div>
            {actions}
          </header>
        )}
        {children}
      </div>
    </div>
  )
}
