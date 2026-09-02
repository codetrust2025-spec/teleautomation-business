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

  it('asks only for what an operator has to type', () => {
    // ID and Service were on this form and neither needed to be: the id is a
    // slug derived from the title when left blank, and Service was free text
    // the table already defaulted to Gmail.
    render(
      <ConfirmProvider>
        <DataRoomAccountsTab accounts={[]} onReload={() => {}} />
      </ConfirmProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: '+ Add account' }))

    // Scoped to the dialog: "Password" is also a column header in the table
    // behind it, so an unscoped query matches both.
    const dialog = screen.getByRole('dialog')
    const labels = [...dialog.querySelectorAll('label')].map(l => l.textContent.trim())
    expect(labels).toEqual([
      'Account name',
      'Username / email',
      'Password',
      'Notes (optional)',
    ])
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(labels.some(l => /ID|slug|Service/i.test(l))).toBe(false)
  })

  it('still sends a generated id, because the backend requires one', async () => {
    // create_vault_item rejects a row without an id, so removing the field
    // must not remove the value.
    const calls = []
    const originalFetch = global.fetch
    global.fetch = async (url, init) => {
      calls.push({ url: String(url), body: JSON.parse(init.body) })
      return { ok: true, json: async () => ({ status: 'ok' }) }
    }
    try {
      render(
        <ConfirmProvider>
          <DataRoomAccountsTab accounts={[]} onReload={() => {}} />
        </ConfirmProvider>,
      )
      fireEvent.click(screen.getByRole('button', { name: '+ Add account' }))
      const inputs = screen.getByRole('dialog').querySelectorAll('input, textarea')
      fireEvent.change(inputs[0], { target: { value: 'Karthik Gmail 2026' } })
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      await new Promise(resolve => setTimeout(resolve, 0))

      expect(calls).toHaveLength(1)
      expect(calls[0].body.id).toBe('karthik_gmail_2026')
      expect(calls[0].body.label).toBe('Karthik Gmail 2026')
    } finally {
      global.fetch = originalFetch
    }
  })
})
