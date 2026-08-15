import React from 'react'
import { Button } from './Button.jsx'

export function SegmentedControl({ options, value, onChange, label, className = '', role = 'group' }) {
  return (
    <div className={['segmented-control', className].filter(Boolean).join(' ')} role={role} aria-label={label}>
      {options.map(option => (
        <Button
          key={option.value}
          variant="segment"
          size="xs"
          active={value === option.value}
          onClick={() => onChange(option.value)}
          disabled={option.disabled}
          role={option.role}
          aria-selected={option.role === 'tab' ? value === option.value : undefined}
          aria-controls={option.controls}
        >
          {option.label}
        </Button>
      ))}
    </div>
  )
}
