/**
 * How a confirmed interview slot came to exist.
 *
 * Read from the persisted `interview_booking_source` and nothing else — never
 * inferred from the candidate, the technology, or anything the UI happens to
 * know. A row whose source was never recorded (legacy rows predate the field)
 * is reported as plain "Booked" rather than being guessed into one of the two
 * real sources.
 */

export const BOOKING_SOURCE_AI = 'ai_auto_booked'
export const BOOKING_SOURCE_CANDIDATE = 'candidate_booked'

export function bookingSourceMeta(source) {
  const value = String(source || '').trim().toLowerCase()
  if (value === BOOKING_SOURCE_AI) {
    return {
      label: 'AI Auto-booked',
      tone: 'auto',
      known: true,
      title: 'Booked automatically from a validated interview email.',
    }
  }
  if (value === BOOKING_SOURCE_CANDIDATE) {
    return {
      label: 'Candidate booked',
      tone: 'candidate',
      known: true,
      title: 'Booked through the candidate or manual slot workflow.',
    }
  }
  return {
    label: 'Booked',
    tone: 'unknown',
    known: false,
    title: 'This booking has no recorded source.',
  }
}
