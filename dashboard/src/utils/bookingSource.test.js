/**
 * The booking-source badge reads the persisted source and nothing else.
 *
 * A legacy row predates the field, so it must not be guessed into one of the
 * two real sources — it reads as plain "Booked".
 */

import { describe, expect, it } from 'vitest'
import { bookingSourceMeta } from './bookingSource.js'

describe('bookingSourceMeta', () => {
  it('labels a candidate-booked slot', () => {
    const meta = bookingSourceMeta('candidate_booked')
    expect(meta.label).toBe('Candidate booked')
    expect(meta.tone).toBe('candidate')
    expect(meta.known).toBe(true)
  })

  it('labels an AI auto-booked slot', () => {
    const meta = bookingSourceMeta('ai_auto_booked')
    expect(meta.label).toBe('AI Auto-booked')
    expect(meta.tone).toBe('auto')
    expect(meta.known).toBe(true)
  })

  it('reports a legacy row with no recorded source as plain Booked', () => {
    for (const legacy of ['', null, undefined, '   ']) {
      const meta = bookingSourceMeta(legacy)
      expect(meta.label).toBe('Booked')
      expect(meta.tone).toBe('unknown')
      expect(meta.known).toBe(false)
    }
  })

  it('never guesses a source it does not recognise', () => {
    const meta = bookingSourceMeta('imported_from_somewhere_else')
    expect(meta.label).toBe('Booked')
    expect(meta.known).toBe(false)
  })

  it('tolerates casing and padding on a real source', () => {
    expect(bookingSourceMeta('  AI_AUTO_BOOKED ').label).toBe('AI Auto-booked')
    expect(bookingSourceMeta('Candidate_Booked').label).toBe('Candidate booked')
  })

  it('gives the three tones distinct values so they can be styled apart', () => {
    const tones = new Set([
      bookingSourceMeta('candidate_booked').tone,
      bookingSourceMeta('ai_auto_booked').tone,
      bookingSourceMeta('').tone,
    ])
    expect(tones.size).toBe(3)
  })
})
