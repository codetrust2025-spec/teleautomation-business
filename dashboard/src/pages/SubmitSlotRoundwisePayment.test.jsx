/**
 * Round-wise payment flow regression tests.
 *
 * Issue 1: Payment card depends on Profile roster match.
 *   A manually typed Round-wise candidate must see the payment card whenever
 *   the backend says payment is required, regardless of whether the name
 *   matches the Profile-roster candidates list.
 *
 * Issue 2: Payment proof context mismatch.
 *   The payment upload must carry service_type="round_wise", phone, technology,
 *   and interview_round so the proof is stored and retrieved correctly.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

// Profile-service roster candidates — none of these are "venkat"
const PROFILE_CANDIDATES = [
  { id: 'c1', name: 'Gopichand', technology: 'React JS', needs_payment_proof: true, balance_due: 20000 },
  { id: 'c2', name: 'Manu', technology: 'Java', needs_payment_proof: false, balance_due: 0 },
]

function screenshot(label) {
  return new File([label], `${label}.jpg`, { type: 'image/jpeg' })
}

/** An interview date that stays ahead of the clock.
 *
 * Confirm is disabled for a past date, so a hardcoded one turns into a silent
 * failure the day it expires: the click lands on a disabled button and nothing
 * is submitted.
 */
function upcomingDate() {
  const date = new Date()
  date.setDate(date.getDate() + 7)
  return date.toISOString().slice(0, 10)
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

function stubFetch({ paymentInfoOverride, uploadReply, confirmReply } = {}) {
  const calls = { uploads: [], confirms: [], paymentInfoCalls: [] }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })

    if (target.includes('/public/slots/payment-info')) {
      calls.paymentInfoCalls.push(target)
      return reply(paymentInfoOverride || { status: 'ok', service_type: 'round_wise', amount_due: 5000, needs_payment: true, waived: false })
    }
    if (target.includes('/public/slots/payment-proof')) {
      calls.uploads.push(options.body)
      return reply(uploadReply || {
        status: 'ok', proof_ids: ['proof-rw-1'], verified_total: 5000,
        remaining_due: 0, amount_due: 5000, payment_complete: true,
        rejected: [], ai_extractions: [{ is_payment_screenshot: true, amount: 5000, verified: true, utr_number: '123456789012' }],
      })
    }
    if (target.includes('/extract-invite-ai')) {
      return reply({ status: 'ok', success: true, data: { interview_date: upcomingDate(), start_time: '03:00 PM', confidence_score: 92 } })
    }
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options.body)
      return reply(confirmReply || { status: 'ok', candidate: { name: 'venkat' } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: PROFILE_CANDIDATES })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  return calls
}

async function chooseRoundWise() {
  render(<SubmitSlotPage />)
  fireEvent.click(await screen.findByRole('button', { name: /profile service/i }))
  fireEvent.click(await screen.findByText('Round-wise'))
}

describe('Round-wise payment — non-roster candidate gets payment card', () => {
  beforeEach(() => { vi.restoreAllMocks() })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('shows payment card for a manually typed name not in roster', async () => {
    stubFetch()
    await chooseRoundWise()

    // Type a name not in roster
    fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'venkat' } })

    // Payment card must appear
    expect(await screen.findByText(/payment due/i)).toBeTruthy()
    expect(screen.getByText(/₹5,000/)).toBeTruthy()
  })

  it('shows correct amount from backend payment-info', async () => {
    stubFetch({ paymentInfoOverride: { status: 'ok', service_type: 'round_wise', amount_due: 9000, needs_payment: true, waived: false } })
    await chooseRoundWise()

    fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'New Candidate' } })

    expect(await screen.findByText(/payment due/i)).toBeTruthy()
    expect(screen.getByText(/₹9,000/)).toBeTruthy()
  })

  it('roster candidate still gets correct behavior (profile service)', async () => {
    stubFetch()
    render(<SubmitSlotPage />)

    // Profile service is default — type a name from the roster
    const nameInput = await screen.findByPlaceholderText(/choose or type your name/i)
    fireEvent.change(nameInput, { target: { value: 'Gopichand' } })
    fireEvent.click(await screen.findByRole('option', { name: 'Gopichand' }))

    // Profile-service candidate with balance_due > 0 shows payment card
    expect(await screen.findByText(/payment due/i)).toBeTruthy()
    expect(screen.getByText(/₹20,000/)).toBeTruthy()
  })

  it('waiver/no-payment path still works (re-service eligible)', async () => {
    stubFetch({ paymentInfoOverride: { status: 'ok', service_type: 'round_wise', amount_due: 5000, needs_payment: false, waived: true } })
    await chooseRoundWise()

    fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'Waived Candidate' } })

    // Payment card should NOT appear for waived candidate
    await waitFor(() => {
      expect(screen.queryByText(/payment due/i)).toBeNull()
    })
  })
})

describe('Round-wise payment — upload carries correct service_type', () => {
  beforeEach(() => { vi.restoreAllMocks() })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('sends service_type=round_wise and phone/technology/interview_round with payment upload', async () => {
    const calls = stubFetch()
    await chooseRoundWise()

    // Fill out all round-wise fields
    fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'venkat' } })
    fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i), { target: { value: '7306994576' } })
    const tech = screen.getByPlaceholderText(/choose or type the technology/i)
    fireEvent.change(tech, { target: { value: 'Java' } })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })

    // Payment card should be visible
    expect(await screen.findByText(/payment due/i)).toBeTruthy()

    // Attach payment screenshot
    const payInput = document.querySelectorAll('input[type="file"]')[0]
    attach(payInput, [screenshot('payment-proof')])
    fireEvent.click(await screen.findByRole('button', { name: /save payment proof/i }))

    await waitFor(() => expect(calls.uploads).toHaveLength(1))
    const uploadBody = calls.uploads[0]
    expect(uploadBody.get('service_type')).toBe('round_wise')
    expect(uploadBody.get('phone')).toBe('7306994576')
    expect(uploadBody.get('technology')).toBe('Java')
    expect(uploadBody.get('interview_round')).toBe('L1')
  })

  it('profile service upload sends service_type=profile_service (no phone/tech)', async () => {
    const calls = stubFetch()
    render(<SubmitSlotPage />)

    // Profile service is default
    const nameInput = await screen.findByPlaceholderText(/choose or type your name/i)
    fireEvent.change(nameInput, { target: { value: 'Gopichand' } })
    fireEvent.click(await screen.findByRole('option', { name: 'Gopichand' }))

    expect(await screen.findByText(/payment due/i)).toBeTruthy()
    const payInput = document.querySelectorAll('input[type="file"]')[0]
    attach(payInput, [screenshot('payment-proof')])
    fireEvent.click(await screen.findByRole('button', { name: /save payment proof/i }))

    await waitFor(() => expect(calls.uploads).toHaveLength(1))
    const uploadBody = calls.uploads[0]
    expect(uploadBody.get('service_type')).toBe('profile_service')
    // No phone/technology/interview_round for profile service
    expect(uploadBody.get('phone')).toBeNull()
  })

  it('technology is included in confirm payload', async () => {
    const calls = stubFetch()
    await chooseRoundWise()

    fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'venkat' } })
    fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i), { target: { value: '7306994576' } })
    fireEvent.change(screen.getByPlaceholderText(/choose or type the technology/i), { target: { value: 'Python' } })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L2' } })

    // Upload payment
    const payInput = document.querySelectorAll('input[type="file"]')[0]
    attach(payInput, [screenshot('pay')])
    fireEvent.click(await screen.findByRole('button', { name: /save payment proof/i }))
    await screen.findByText(/payment proof saved/i)

    // Upload invite
    const inviteInput = document.querySelector('input[type="file"]')
    attach(inviteInput, [screenshot('invite')])
    await waitFor(() => expect(screen.queryByText(/reading invite with ai/i)).toBeNull())

    // Submit
    fireEvent.click(screen.getByRole('button', { name: /confirm booking/i }))
    await waitFor(() => expect(calls.confirms).toHaveLength(1))

    const confirmBody = calls.confirms[0]
    expect(confirmBody.get('service_type')).toBe('round_wise')
    expect(confirmBody.get('phone')).toBe('7306994576')
    expect(confirmBody.get('technology')).toBe('Python')
    expect(confirmBody.get('interview_round')).toBe('L2')
    expect(confirmBody.get('payment_proof_ids')).toBe('proof-rw-1')
  })

  it('technology required validation fires on the field', async () => {
    stubFetch()
    await chooseRoundWise()

    fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'venkat' } })
    fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i), { target: { value: '7306994576' } })
    // Do NOT type a technology
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })

    // Upload payment
    const payInput = document.querySelectorAll('input[type="file"]')[0]
    attach(payInput, [screenshot('pay')])
    fireEvent.click(await screen.findByRole('button', { name: /save payment proof/i }))
    await screen.findByText(/payment proof saved/i)

    // Upload invite
    const inviteInput = document.querySelector('input[type="file"]')
    attach(inviteInput, [screenshot('invite')])
    await waitFor(() => expect(screen.queryByText(/reading invite with ai/i)).toBeNull())

    fireEvent.click(screen.getByRole('button', { name: /confirm booking/i }))

    // Technology validation error on the field
    expect(await screen.findByText(/round-wise booking needs the technology/i)).toBeTruthy()
  })

  it('invite auto-fills technology', async () => {
    stubFetch()
    vi.stubGlobal('fetch', vi.fn((url) => {
      const target = String(url)
      const reply = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
      if (target.includes('/extract-invite-ai')) {
        return reply({ status: 'ok', success: true, data: { interview_date: upcomingDate(), start_time: '03:00 PM', technology: 'React Native', confidence_score: 92 } })
      }
      if (target.includes('/public/slots/payment-info')) {
        return reply({ status: 'ok', service_type: 'round_wise', amount_due: 5000, needs_payment: true, waived: false })
      }
      if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
      return reply({ status: 'ok', candidates: PROFILE_CANDIDATES })
    }))

    await chooseRoundWise()
    const inviteInput = document.querySelector('input[type="file"]')
    attach(inviteInput, [screenshot('invite')])

    await waitFor(() =>
      expect(screen.getByPlaceholderText(/choose or type the technology/i).value).toBe('React Native'))
  })

  it('manually entered technology survives later invite re-read', async () => {
    stubFetch()
    // Override fetch specifically for this test
    vi.stubGlobal('fetch', vi.fn((url) => {
      const target = String(url)
      const reply = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
      if (target.includes('/extract-invite-ai')) {
        return reply({ status: 'ok', success: true, data: { interview_date: upcomingDate(), start_time: '03:00 PM', technology: 'Overwritten Tech', confidence_score: 92 } })
      }
      if (target.includes('/public/slots/payment-info')) {
        return reply({ status: 'ok', service_type: 'round_wise', amount_due: 5000, needs_payment: true, waived: false })
      }
      if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
      return reply({ status: 'ok', candidates: PROFILE_CANDIDATES })
    }))

    await chooseRoundWise()
    const tech = screen.getByPlaceholderText(/choose or type the technology/i)
    fireEvent.change(tech, { target: { value: 'Golang' } })

    // Now upload invite which tries to set a different technology
    const inviteInput = document.querySelector('input[type="file"]')
    attach(inviteInput, [screenshot('invite')])

    await waitFor(() => expect(screen.queryByText(/reading invite with ai/i)).toBeNull())
    // The manually typed technology must survive
    expect(tech.value).toBe('Golang')
  })
})
