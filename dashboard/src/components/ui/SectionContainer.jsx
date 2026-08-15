import React from 'react'

export function SectionContainer({
  title,
  subtitle,
  actions,
  density = 'normal',
  variant = 'card',
  className = '',
  children,
}) {
  const classes = [
    'surface',
    variant ? `surface--${variant}` : '',
    density ? `surface--${density}` : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <section className={classes}>
      {(title || subtitle || actions) && (
        <header className="section-header section-header--compact">
          <div>
            {title && <h3 className="section-title">{title}</h3>}
            {subtitle && <p className="section-sub">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  )
}
