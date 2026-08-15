/**
 * The public booking boundary is a project invariant:
 * "/bookings/confirm is the only public booking creation boundary."
 *
 * The split shipped this page posting to /public/slots/book, which the backend
 * answers with HTTP 410 "This booking endpoint is retired." Every public slot
 * booking would have failed. No test caught it, because no test exercised the
 * submit path against the real route table.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = resolve(HERE, '..', '..', '..')
const page = readFileSync(join(HERE, 'SubmitSlotPage.jsx'), 'utf8')
const api = readFileSync(join(REPO, 'core', 'public_slot_api.py'), 'utf8')

describe('public booking boundary', () => {
  it('submits bookings to /bookings/confirm', () => {
    expect(page).toMatch(/\/bookings\/confirm/)
  })

  it('never posts to the retired /public/slots/book endpoint', () => {
    expect(page).not.toMatch(/\/public\/slots\/book['"`]/)
  })

  it('confirms the backend still treats /public/slots/book as retired', () => {
    // If this ever changes, the assertion above needs revisiting rather than
    // being silently wrong.
    expect(api).toMatch(/public\/slots\/book/)
    expect(api).toMatch(/retired/i)
  })

  it('sends an idempotency key so a retry cannot double-book', () => {
    expect(page).toMatch(/idempotency_key/)
  })

  it('sends the phone identity that round-wise booking requires', () => {
    // /bookings/confirm rejects round_wise without a valid phone identity.
    expect(api).toMatch(/valid phone number is required for round-wise/i)
    expect(page).toMatch(/fd\.append\('phone'/)
  })

  it('sends candidate_id so an existing candidate is matched, not duplicated', () => {
    expect(page).toMatch(/fd\.append\('candidate_id'/)
  })
})
