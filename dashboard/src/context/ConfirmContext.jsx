import React, { createContext, useCallback, useContext, useState } from 'react'
import { CommonModal } from '../components/ui/CommonModal.jsx'

const GLOBAL_CTX_KEY = '__TA_CONFIRM_CONTEXT__'
const GLOBAL_VALUE_KEY = '__TA_CONFIRM_VALUE__'

function getConfirmContext() {
  if (typeof globalThis !== 'undefined' && globalThis[GLOBAL_CTX_KEY]) {
    return globalThis[GLOBAL_CTX_KEY]
  }
  const ctx = createContext(null)
  if (typeof globalThis !== 'undefined') {
    globalThis[GLOBAL_CTX_KEY] = ctx
  }
  return ctx
}

const ConfirmContext = getConfirmContext()

/**
 * Unified confirm/modal provider.
 *
 * Usage (simple confirm — replaces window.confirm):
 *   const { confirm } = useConfirm()
 *   const ok = await confirm({
 *     title: 'Remove this proof?',
 *     message: 'filename.jpg',
 *     confirmLabel: 'Remove',
 *     variant: 'danger',
 *   })
 *
 * Usage (form modal):
 *   const { confirm } = useConfirm()
 *   const ok = await confirm({
 *     title: 'Mark as "Attended"?',
 *     message: 'Select attendee for Pavan Ravi',
 *     confirmLabel: 'Attended',
 *     content: <AttendanceForm ref={formRef} />,
 *   })
 */
export function ConfirmProvider({ children }) {
  const [pending, setPending] = useState(null)

  const confirm = useCallback((options) => {
    return new Promise((resolve) => {
      setPending({ ...options, resolve })
    })
  }, [])

  const close = useCallback((result) => {
    setPending((prev) => {
      if (prev?.resolve) prev.resolve(result)
      return null
    })
  }, [])

  const value = { confirm }

  if (typeof globalThis !== 'undefined') {
    globalThis[GLOBAL_VALUE_KEY] = value
  }

  // Map variant to CommonModal type
  const type = pending?.variant === 'danger' ? 'danger'
    : pending?.variant === 'warn' ? 'warning'
    : pending?.variant === 'success' ? 'success'
    : 'default'

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {pending && (
        <CommonModal
          open={true}
          type={type}
          title={pending.title}
          description={pending.message}
          confirmText={pending.confirmLabel || 'Confirm'}
          cancelText={pending.cancelLabel || 'Cancel'}
          onConfirm={() => close(true)}
          onClose={() => close(false)}
        >
          {/* Cleared / Kept lists (stats reset style) */}
          {(pending.cleared?.length > 0 || pending.details?.length > 0) && (
            <div className="cm-confirm-message">
              <p style={{ margin: '0 0 8px', fontSize: '0.78rem', fontWeight: 600, color: '#f87171', textTransform: 'uppercase', letterSpacing: '0.3px' }}>
                Will be reset to zero
              </p>
              <ul style={{ margin: 0, paddingLeft: '16px', fontSize: '0.83rem', color: 'rgba(226,232,240,.8)', lineHeight: 1.6 }}>
                {(pending.cleared || pending.details || []).map((line, i) => (
                  <li key={`c-${i}`}>{line}</li>
                ))}
              </ul>
            </div>
          )}
          {pending.kept?.length > 0 && (
            <div className="cm-confirm-message" style={{ marginTop: 12 }}>
              <p style={{ margin: '0 0 8px', fontSize: '0.78rem', fontWeight: 600, color: '#22c55e', textTransform: 'uppercase', letterSpacing: '0.3px' }}>
                Not deleted — kept as-is
              </p>
              <ul style={{ margin: 0, paddingLeft: '16px', fontSize: '0.83rem', color: 'rgba(226,232,240,.8)', lineHeight: 1.6 }}>
                {pending.kept.map((line, i) => (
                  <li key={`k-${i}`}>{line}</li>
                ))}
              </ul>
            </div>
          )}
          {/* Custom content (forms, etc.) */}
          {pending.content}
        </CommonModal>
      )}
    </ConfirmContext.Provider>
  )
}

export function useConfirm() {
  const ctx = useContext(ConfirmContext)
  if (ctx) return ctx
  if (typeof globalThis !== 'undefined' && globalThis[GLOBAL_VALUE_KEY]) {
    return globalThis[GLOBAL_VALUE_KEY]
  }
  throw new Error('useConfirm must be used within ConfirmProvider')
}
