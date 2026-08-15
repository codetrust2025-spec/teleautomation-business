import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { DataRoomAccountsTab } from './DataRoomAccountsTab.jsx'

afterEach(() => {
  cleanup()
  document.body.style.overflow = ''
})

describe('DataRoomAccountsTab service account modal', () => {
  it('opens as an isolated modal and restores page scrolling when closed', () => {
    render(
      <ConfirmProvider>
        <DataRoomAccountsTab accounts={[]} onReload={() => {}} />
      </ConfirmProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: '+ Add account' }))

    const dialog = screen.getByRole('dialog', { name: 'Add service account' })
    expect(dialog).toHaveClass('dr-modal')
    expect(dialog.parentElement).toHaveClass('dr-modal-backdrop')
    expect(dialog.parentElement?.parentElement).toBe(document.body)
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(document.body.style.overflow).toBe('hidden')

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('dialog', { name: 'Add service account' })).not.toBeInTheDocument()
    expect(document.body.style.overflow).toBe('')
  })
})
