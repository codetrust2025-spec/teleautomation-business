import React, { forwardRef } from 'react'
import { ButtonContent } from '../../Loader.jsx'

const VARIANT_CLASS = {
  primary: 'btn--primary',
  secondary: 'btn--ghost',
  danger: 'btn--danger',
  success: 'btn--success',
  warning: 'btn--warn',
  ghost: 'btn--ghost',
  toolbar: 'btn--ghost btn--toolbar',
  segment: 'btn--segment',
}

const SIZE_CLASS = {
  xs: 'btn--xs',
  sm: 'btn--sm',
  md: '',
  lg: 'btn--lg',
}

export const Button = forwardRef(function Button({
  children,
  className = '',
  variant = 'secondary',
  size = 'md',
  loading = false,
  loadingLabel,
  active = false,
  type = 'button',
  ...props
}, ref) {
  const classes = [
    'btn',
    VARIANT_CLASS[variant] || VARIANT_CLASS.secondary,
    SIZE_CLASS[size] || '',
    active ? 'btn--segment-active' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <button ref={ref} type={type} className={classes} aria-pressed={active || undefined} {...props}>
      <ButtonContent loading={loading} loadingLabel={loadingLabel}>
        {children}
      </ButtonContent>
    </button>
  )
})
