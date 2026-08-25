/**
 * A round-wise booking has to be able to pay for itself.
 *
 * The payment upload files a proof under a service type, and /bookings/confirm
 * only resolves a proof whose service type matches the booking. The form used
 * to post only the name, so every proof was filed under "profile_service" and
 * a round-wise confirmation could never claim it — the upload answered 200,
 * the booking answered 400 "Upload and verify the payment screenshot to
 * continue.", and re-uploading changed nothing.
 *
 * The upload control was also unreachable for round-wise: it hung off
 * `needs_payment_proof`, which is the profile-service balance of a roster row,
 * and round-wise clients are typed in by hand and keep no such row.
 *
 * These drive the real form and read the real FormData, because the payload the
 * browser sends is the thing that was wrong.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const ROUND_FEE = 5000
// A roster row for the profile-service case. Round-wise deliberately has none.
const CANDIDATE = {
  id: 'cand-1', name: 'Gopichand', needs_payment_proof: true, balance_due: 20000,
}

const PAID_IN_FULL = {
  status: 'ok',
  proof_ids: ['proof-a'],
  verified_total: ROUND_FEE,
  remaining_due: 0,
  amount_due: ROUND_FEE,
  payment_complete: true,
  rejected: [],
  ai_extractions: [{ is_payment_screenshot: true, amount: ROUND_FEE, verified: true }],
}

function screenshot(label) {
  return new File([label], `${label}.jpg`, { type: 'image/jpeg' })
}

function stubFetch(uploadReplies = [PAID_IN_FULL]) {
  const calls = { uploads: [], confirms: [] }
  const fetchStub = vi.fn((url, options) => {
    const target = String(url)
    if (target.includes('/public/slots/payment-proof')) {
      calls.uploads.push(options.body)
      const body = uploadReplies[calls.uploads.length - 1] || PAID_IN_FULL
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    }
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options.body)
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ status: 'ok', candidate: { name: 'Raju' } }),
      })
    }
    const body = target.includes('/public/slots/booked')
      ? { status: 'ok', slots: [] }
      : { status: 'ok', candidates: [CANDIDATE], round_wise_fee: ROUND_FEE }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  })
  vi.stubGlobal('fetch', fetchStub)
  return calls
}

/** Switch the form to round-wise and fill the identity it books under. */
async function chooseRoundWise({ name = 'Raju', phone = '9876543210' } = {}) {
  render(<SubmitSlotPage />)
  fireEvent.click(await screen.findByRole('button', { name: /profile service/i }))
  fireEvent.click(await screen.findByText('Round-wise'))
  const nameInput = await screen.findByPlaceholderText(/type client name/i)
  fireEvent.change(nameInput, { target: { value: name } })
  const phoneInput = screen.getByPlaceholderText(/10-digit phone number/i)
  if (phone) fireEvent.change(phoneInput, { target: { value: phone } })
  return { nameInput, phoneInput }
}

function paymentInput() {
  // The payment drop is the multi-select one; the invite drop is not.
  return document.querySelectorAll('input[type="file"]')[0]
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

function saveButton() {
  return screen.getByRole('button', { name: /save payment proof/i })
}

describe('Submit slot — round-wise payment proof identity', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    // jsdom has no object URLs; the thumbnails need them.
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(f => `blob:${f.name}`),
      revokeObjectURL: vi.fn(),
    })
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('offers the payment card to a round-wise client with no roster row', async () => {
    stubFetch()
    await chooseRoundWise()
    // The fee comes from the server, which is also what /bookings/confirm
    // re-derives; nothing here invents a price.
    expect(await screen.findByText('₹5,000')).toBeTruthy()
    expect(saveButton()).toBeTruthy()
  })

  it('files the proof under the identity the booking is confirmed with', async () => {
    const calls = stubFetch()
    await chooseRoundWise()

    attach(paymentInput(), [screenshot('pay-1')])
    fireEvent.click(saveButton())
    await waitFor(() => expect(calls.uploads).toHaveLength(1))

    const upload = calls.uploads[0]
    // Without these three the proof is filed under "profile_service" and the
    // round-wise confirmation can never resolve it.
    expect(upload.get('service_type')).toBe('round_wise')
    expect(upload.get('phone')).toBe('9876543210')
    expect(upload.get('candidate_id')).toBe('')
    expect(upload.get('name')).toBe('Raju')
  })

  it('sends the same identity to the upload that it sends to the booking', async () => {
    const calls = stubFetch()
    await chooseRoundWise()

    attach(paymentInput(), [screenshot('pay-1')])
    fireEvent.click(saveButton())
    await waitFor(() => expect(calls.uploads).toHaveLength(1))
    await screen.findByText(/payment proof on file/i)

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })
    fireEvent.change(screen.getByPlaceholderText(/technology/i), { target: { value: 'ETL' } })
    attach(document.querySelector('input[type="file"]'), [screenshot('invite')])

    await waitFor(() => expect(screen.getByRole('button', { name: /confirm booking/i })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /confirm booking/i }))
    await waitFor(() => expect(calls.confirms).toHaveLength(1))

    const upload = calls.uploads[0]
    const confirm = calls.confirms[0]
    for (const field of ['name', 'service_type', 'phone', 'candidate_id']) {
      expect(upload.get(field)).toBe(confirm.get(field))
    }
  })

  it('will not upload a round-wise proof before the phone is known', async () => {
    const calls = stubFetch()
    await chooseRoundWise({ phone: '' })

    attach(paymentInput(), [screenshot('pay-1')])
    fireEvent.click(saveButton())

    // The proof is filed under the phone, so verifying it first would file it
    // under nothing and waste the verification.
    expect(await screen.findByText(/enter the candidate phone number/i)).toBeTruthy()
    expect(calls.uploads).toHaveLength(0)
  })

  it('drops a proof filed under a phone that has since been changed', async () => {
    stubFetch()
    const { phoneInput } = await chooseRoundWise()

    attach(paymentInput(), [screenshot('pay-1')])
    fireEvent.click(saveButton())
    expect(await screen.findByText(/payment proof on file/i)).toBeTruthy()

    // The stored proof belongs to the old phone and can no longer be claimed,
    // so keeping it would fail at booking as an unexplained "upload the
    // payment screenshot".
    fireEvent.change(phoneInput, { target: { value: '9000000001' } })
    await waitFor(() => expect(screen.queryByText(/payment proof on file/i)).toBeNull())
    expect(saveButton()).toBeTruthy()
  })

  it('still names the service on a profile-service upload', async () => {
    const calls = stubFetch()
    render(<SubmitSlotPage />)
    const name = await screen.findByPlaceholderText(/choose or type your name/i)
    fireEvent.change(name, { target: { value: 'Gopichand' } })
    fireEvent.click(await screen.findByRole('option', { name: 'Gopichand' }))
    await screen.findByText(/payment due/i)

    attach(paymentInput(), [screenshot('pay-1')])
    fireEvent.click(saveButton())
    await waitFor(() => expect(calls.uploads).toHaveLength(1))

    expect(calls.uploads[0].get('service_type')).toBe('profile_service')
    expect(calls.uploads[0].get('candidate_id')).toBe('cand-1')
    // Profile service keeps reading its due amount from the roster row.
    expect(screen.getByText('₹20,000')).toBeTruthy()
  })
})
