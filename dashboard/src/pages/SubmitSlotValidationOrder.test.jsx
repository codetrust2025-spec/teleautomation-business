/**
 * Confirm booking reports the first missing field, in form order.
 *
 * It used to be one OR across every rule: it flagged all of them at once, said
 * nothing about any of them, and left the browser to choose where to send you.
 * The browser chose Interview round, because `required` sat on that select and
 * on nothing else, so an empty form jumped past Client name, Candidate phone
 * and Technology to say "Please select an item in the list." -- which names
 * neither the field nor what to do about it.
 *
 * The rules are unchanged. They are ordered, and reported one at a time.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = dirname(fileURLToPath(import.meta.url))
const page = readFileSync(join(SRC, 'SubmitSlotPage.jsx'), 'utf8')

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    if (target.includes('/public/slots/payment-requirement')) {
      return reply({ status: 'ok', service_type: 'round_wise', amount_due: 5000, payment_required: true })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: [] })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
}

async function openForm({ roundWise = false } = {}) {
  stubFetch()
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
  if (roundWise) {
    // The page opens on Profile service, where Client name is a picker and
    // phone and technology do not apply. Round-wise is the form with every
    // step in the sequence on it.
    fireEvent.click(screen.getByRole('button', { name: /Profile service/i }))
    fireEvent.click(screen.getByText('Round-wise'))
  }
  return confirm
}

/** The ordered checks, read out of the component. */
function steps() {
  const start = page.indexOf('const steps = [')
  const end = page.indexOf(']', page.indexOf('inviteRef', start))
  expect(start).toBeGreaterThan(-1)
  const block = page.slice(start, end)
  return [...block.matchAll(/\{ ref: (\w+)(?:, fallbackRef: \w+)?, ok: /g)].map(m => m[1])
}

describe('the browser no longer decides', () => {
  it('the form opts out of native validation', () => {
    expect(page).toMatch(/<form className="sbs-form" noValidate/)
  })

  it('no control carries a bare required attribute any more', () => {
    // One `required` on a mid-form select is what sent an empty form to
    // Interview round.
    expect(page).not.toMatch(/disabled=\{busy \|\| parsing\} required>/)
  })
})

describe('validation runs top to bottom', () => {
  it('checks the fields in the order they appear on the form', () => {
    expect(steps()).toEqual([
      'nameRef', 'phoneRef', 'technologyRef', 'roundRef', 'paymentRef', 'inviteRef',
    ])
  })

  it('payment is validated after the candidate and interview details', () => {
    // What is owed depends on the service type, the round and the candidate, so
    // checking it earlier would judge an amount that is not settled yet.
    const order = steps()
    expect(order.indexOf('paymentRef')).toBeGreaterThan(order.indexOf('roundRef'))
    expect(order.indexOf('paymentRef')).toBeGreaterThan(order.indexOf('technologyRef'))
  })

  it('the invite screenshot is checked after payment', () => {
    const order = steps()
    expect(order.indexOf('inviteRef')).toBeGreaterThan(order.indexOf('paymentRef'))
  })

  it('stops at the first missing field rather than reporting all of them', () => {
    expect(page).toContain('const firstMissing = steps.find(step => !step.ok)')
  })

  it('says which field, in the application\'s own words', () => {
    for (const message of [
      'Enter the client name for this round.',
      'Enter the candidate phone number.',
      'Choose the technology for this interview.',
      'Choose the interview round.',
      'Attach a payment screenshot that covers the amount due.',
      'Attach the interview invite screenshot.',
    ]) {
      expect(page).toContain(message)
    }
  })

  it('brings that field into view', () => {
    expect(page).toContain('focusField(firstMissing.ref')
    expect(page).toMatch(/scrollIntoView\(\{ behavior: 'smooth', block: 'center' \}\)/)
  })
})

describe('the rules themselves are unchanged', () => {
  it('phone and technology stay round-wise only', () => {
    expect(page).toContain("ok: serviceType !== 'round_wise' || !!roundWisePhone.trim()")
    expect(page).toContain("ok: serviceType !== 'round_wise' || !!effectiveTechnology")
  })

  it('payment still gates on the same needsPaymentProof flag', () => {
    expect(page).toContain('ok: !needsPaymentProof')
  })

  it('the past-date rule still runs after the sequence', () => {
    const sequenceAt = page.indexOf('const firstMissing')
    const pastDateAt = page.indexOf('Interview date is in the past')
    expect(pastDateAt).toBeGreaterThan(sequenceAt)
  })
})

describe('every field in the sequence can actually be reached', () => {
  it('each ref is attached to something in the markup', () => {
    for (const ref of steps()) {
      expect(page).toContain(`ref={${ref}}`)
    }
  })

  it('client name is reachable under both service types', () => {
    // It renders as a text input for round-wise and a picker for profile
    // service, so the wrapper carries the ref the sequence scrolls to.
    expect(page).toContain('ref={nameFieldRef} className="sbs-field"')
    expect(page).toContain('fallbackRef: nameFieldRef')
  })

  it('scrolling and focusing are both optional, so a submit cannot throw', () => {
    // jsdom implements neither, and a container element is not focusable.
    expect(page).toContain("typeof node.scrollIntoView === 'function'")
    expect(page).toContain("typeof node.focus === 'function'")
  })
})


describe("an empty form reports the first field, not the browser's choice", () => {
  it('asks for the client name, not the interview round', async () => {
    // The symptom: an empty form used to jump to Interview round and say
    // "Please select an item in the list."
    const confirm = await openForm()
    fireEvent.click(confirm)

    await waitFor(() =>
      expect(screen.getByText('Enter the client name for this round.')).toBeInTheDocument(),
    )
    expect(screen.queryByText('Choose the interview round.')).not.toBeInTheDocument()
  })

  it('moves to the phone only once the name is filled', async () => {
    const confirm = await openForm({ roundWise: true })
    fireEvent.change(screen.getByPlaceholderText('Type client name'), {
      target: { value: 'Code Trust' },
    })
    fireEvent.click(confirm)

    await waitFor(() =>
      expect(screen.getByText('Enter the candidate phone number.')).toBeInTheDocument(),
    )
    expect(screen.queryByText('Enter the client name for this round.')).not.toBeInTheDocument()
  })

  it('reaches the interview round only after name, phone and technology', async () => {
    const confirm = await openForm({ roundWise: true })
    fireEvent.change(screen.getByPlaceholderText('Type client name'), {
      target: { value: 'Code Trust' },
    })
    const phone = document.querySelector('input[type="tel"], input[inputMode="numeric"]')
      || [...document.querySelectorAll('input')].find(i => i !== screen.getByPlaceholderText('Type client name'))
    fireEvent.change(phone, { target: { value: '1234567890' } })
    fireEvent.click(confirm)

    await waitFor(() =>
      expect(screen.getByText('Choose the technology for this interview.')).toBeInTheDocument(),
    )
  })
})
