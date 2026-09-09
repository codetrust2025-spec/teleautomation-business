/**
 * An interview written in a numeric offset still gets its IST line.
 *
 * The backend deliberately stores the zone the sender wrote, because the
 * conversion needs something to convert from. For a fixed offset,
 * `interview_timezones.label()` returns a `tzname()` string -- "UTC+00:00",
 * "UTC+05:30" -- and Intl has no zone by that name, so `timeZoneOffsetMs`
 * threw, `scheduleInstant` returned null, and `formatScheduleIstDateTime`
 * returned ''. The detail view reads '' as "already IST, no extra line
 * needed", so the row simply vanished.
 *
 * Found end to end on a real mail: Karat's "We've Scheduled Your Altimetrik
 * Interview for the Citi Scaled Hiring FPC" stored 2026-09-11 08:30 AM with
 * timezone UTC+00:00. The operator saw "8:30 am (UTC+00:00)" and no IST line,
 * in a list where 59 of 61 other rows are Asia/Kolkata. The interview is at
 * 2:00 pm IST -- a five and a half hour error.
 */
import { describe, it, expect } from 'vitest'
import {
  formatScheduleDateTime,
  formatScheduleIstDateTime,
  isIstTimeZone,
} from './istTime.js'

const DATE = '2026-09-11'
const TIME = '08:30 AM'

describe('the Altimetrik record', () => {
  it('now shows an IST line', () => {
    expect(formatScheduleIstDateTime(DATE, TIME, 'UTC+00:00')).toBe('11 Sept 2026, 2:00 pm IST')
  })

  it('still shows the invite wording untouched beside it', () => {
    expect(formatScheduleDateTime(DATE, TIME, 'UTC+00:00')).toContain('8:30 am')
    expect(formatScheduleDateTime(DATE, TIME, 'UTC+00:00')).toContain('UTC+00:00')
  })
})

describe('written offsets convert like any other zone', () => {
  it.each([
    ['UTC+00:00', '11 Sept 2026, 2:00 pm IST'],
    ['UTC-08:00', '11 Sept 2026, 10:00 pm IST'],
    ['GMT+0200', '11 Sept 2026, 12:00 pm IST'],
    ['+00:00', '11 Sept 2026, 2:00 pm IST'],
    ['-5', '11 Sept 2026, 7:00 pm IST'],
  ])('%s', (zone, expected) => {
    expect(formatScheduleIstDateTime(DATE, TIME, zone)).toBe(expected)
  })

  it('rolls the date over when the offset pushes past midnight', () => {
    // 20:00 UTC on the 11th is 01:30 IST on the 12th.
    expect(formatScheduleIstDateTime(DATE, '08:00 PM', 'UTC+00:00'))
      .toBe('12 Sept 2026, 1:30 am IST')
  })
})

describe('india written as an offset is still india', () => {
  it.each(['UTC+05:30', '+05:30', '+0530', 'GMT+05:30'])('%s counts as IST', (zone) => {
    expect(isIstTimeZone(zone)).toBe(true)
  })

  it('so no second line repeats the same clock reading', () => {
    expect(formatScheduleIstDateTime(DATE, TIME, 'UTC+05:30')).toBe('')
  })
})

describe('what must not change', () => {
  it('named zones convert exactly as before', () => {
    expect(formatScheduleIstDateTime(DATE, TIME, 'Etc/UTC')).toBe('11 Sept 2026, 2:00 pm IST')
    expect(formatScheduleIstDateTime(DATE, TIME, 'UTC')).toBe('11 Sept 2026, 2:00 pm IST')
    expect(formatScheduleIstDateTime(DATE, '09:00 AM', 'America/New_York'))
      .toBe('11 Sept 2026, 6:30 pm IST')
  })

  it('an already-IST schedule still shows no second line', () => {
    expect(formatScheduleIstDateTime(DATE, TIME, 'Asia/Kolkata')).toBe('')
    expect(formatScheduleIstDateTime(DATE, TIME, 'IST')).toBe('')
  })

  it('a missing or unreadable zone still yields nothing rather than throwing', () => {
    for (const zone of ['', null, undefined, 'Not Provided', 'Mars/Olympus']) {
      expect(formatScheduleIstDateTime(DATE, TIME, zone)).toBe('')
    }
  })

  it('an impossible offset is not treated as a zone', () => {
    // Beyond the real range of UTC offsets; must fall through, not convert.
    expect(formatScheduleIstDateTime(DATE, TIME, 'UTC+25:00')).toBe('')
  })

  it('a missing date or time still yields nothing', () => {
    expect(formatScheduleIstDateTime('', TIME, 'UTC+00:00')).toBe('')
    expect(formatScheduleIstDateTime(DATE, '', 'UTC+00:00')).toBe('')
  })
})
