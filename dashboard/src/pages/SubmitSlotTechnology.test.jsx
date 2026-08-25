/**
 * Round-wise booking has to be able to supply a technology.
 *
 * `/bookings/confirm` refuses a round-wise booking without one — "Technology is
 * required for round-wise booking. Select the technology and try again." — but
 * the form had no technology field at all. `technology` was only ever sent when
 * the invite happened to name it, so an invite that did not (a plain "your L1
 * is scheduled" mail) made the booking unreachable: the error named a control
 * that did not exist.
 *
 * These assert the field exists for round-wise, that the invite still fills it
 * in when it can, that a typed value survives a later extraction, and that
 * profile-service is left alone.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

// The form refuses a date in the past, so the invite has to name one ahead of
// today. A literal date silently stops testing anything the day it goes stale:
// these read as passing right up until the confirm button quietly disabled
// itself and every assertion downstream of the submit stopped being reached.
const AHEAD = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)

const CANDIDATES = [
  { id: 'c1', name: 'Gopichand', technology: 'React JS', needs_payment_proof: false, balance_due: 0 },
  { id: 'c2', name: 'Manu', technology: 'Java', needs_payment_proof: false, balance_due: 0 },
  { id: 'c3', name: 'Ashok', technology: 'Unspecified', needs_payment_proof: false, balance_due: 0 },
]

function stubFetch({ extraction } = {}) {
  const calls = { confirms: [] }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    if (target.includes('/extract-invite-ai')) {
      return reply({ status: 'ok', success: true, data: extraction || {} })
    }
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options.body)
      return reply({ status: 'ok', candidate: { name: 'venkat' } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: CANDIDATES })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  return calls
}

/** The invite drop, addressed directly.
 *
 * Round-wise also renders a payment drop, and it comes first in the document,
 * so "the first file input" is no longer the invite. The invite is the
 * single-file one and the payment drop is the multi-select one.
 */
function inviteInput() {
  return document.querySelector('input[type="file"]:not([multiple])')
}

function screenshot(name) {
  return new File([name], name, { type: 'image/jpeg' })
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

async function chooseRoundWise() {
  render(<SubmitSlotPage />)
  fireEvent.click(await screen.findByRole('button', { name: /profile service/i }))
  fireEvent.click(await screen.findByText('Round-wise'))
}

describe('Submit slot — round-wise technology', () => {
  beforeEach(() => { vi.restoreAllMocks() })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('offers a technology field and sends what was chosen', async () => {
    const calls = stubFetch({ extraction: { interview_date: AHEAD, start_time: '03:00 PM', confidence_score: 90 } })
    await chooseRoundWise()

    const tech = await screen.findByPlaceholderText(/choose or type the technology/i)
    fireEvent.focus(tech)
    // The roster's technologies are offered; "Unspecified" is not a choice.
    expect(await screen.findByRole('option', { name: 'React JS' })).toBeTruthy()
    expect(screen.queryByRole('option', { name: 'Unspecified' })).toBeNull()
    fireEvent.click(screen.getByRole('option', { name: 'Java' }))
    expect(tech.value).toBe('Java')

    fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'venkat' } })
    fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i), { target: { value: '7306994576' } })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })
    attach(inviteInput(), [screenshot('invite.jpg')])

    await waitFor(() => expect(screen.queryByText(/reading invite with ai/i)).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: /confirm booking/i }))

    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('technology')).toBe('Java')
    expect(calls.confirms[0].get('service_type')).toBe('round_wise')
  })

  it('refuses to submit without a technology and says so on the field', async () => {
    const calls = stubFetch({ extraction: { interview_date: AHEAD, start_time: '03:00 PM', confidence_score: 90 } })
    await chooseRoundWise()

    fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'venkat' } })
    fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i), { target: { value: '7306994576' } })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })
    attach(inviteInput(), [screenshot('invite.jpg')])
    await waitFor(() => expect(screen.queryByText(/reading invite with ai/i)).toBeNull())

    fireEvent.click(screen.getByRole('button', { name: /confirm booking/i }))

    // Flagged on the field, not bounced off the server.
    expect(await screen.findByText(/round-wise booking needs the technology/i)).toBeTruthy()
    expect(calls.confirms).toHaveLength(0)
  })

  it('fills the field from the invite when it names the technology', async () => {
    stubFetch({ extraction: { interview_date: AHEAD, start_time: '03:00 PM', technology: 'Automation Testing', confidence_score: 90 } })
    await chooseRoundWise()

    attach(inviteInput(), [screenshot('invite.jpg')])
    await waitFor(() =>
      expect(screen.getByPlaceholderText(/choose or type the technology/i).value).toBe('Automation Testing'))
  })

  it('keeps a typed technology when a later invite says something else', async () => {
    stubFetch({ extraction: { interview_date: AHEAD, start_time: '03:00 PM', technology: 'Automation Testing', confidence_score: 90 } })
    await chooseRoundWise()

    const tech = await screen.findByPlaceholderText(/choose or type the technology/i)
    fireEvent.change(tech, { target: { value: 'Golang' } })
    attach(inviteInput(), [screenshot('invite.jpg')])

    await waitFor(() => expect(screen.queryByText(/reading invite with ai/i)).toBeNull())
    expect(tech.value).toBe('Golang')
  })

  it('leaves profile service without a technology field', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByText(/service type/i)
    expect(screen.queryByPlaceholderText(/choose or type the technology/i)).toBeNull()
  })
})
