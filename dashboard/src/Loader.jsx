import React from 'react'

/** Small spinning indicator — use inside buttons, labels, overlays. */
export function Spinner({ size = 16, className = '' }) {
  return (
    <span
      className={`ui-spinner ${className}`.trim()}
      style={{ width: size, height: size }}
      aria-hidden
    />
  )
}

/** Inline text + spinner (status lines, buttons). */
export function InlineLoader({ label = 'Loading…', size = 14 }) {
  return (
    <span className="inline-loader" role="status">
      <Spinner size={size} />
      <span>{label}</span>
    </span>
  )
}

/** Full-area overlay for panels or initial boot. */
export function OverlayLoader({ label = 'Loading…' }) {
  return (
    <div className="overlay-loader" role="status" aria-live="polite">
      <Spinner size={28} />
      <span className="overlay-loader-label">{label}</span>
    </div>
  )
}

/** Button content: spinner + label when loading, else children. */
export function ButtonContent({ loading, loadingLabel, children }) {
  if (!loading) return children
  return (
    <>
      <Spinner size={14} className="ui-spinner--on-dark" />
      <span>{loadingLabel || 'Loading…'}</span>
    </>
  )
}
