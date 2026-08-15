/**
 * Interview schedules and IST.
 *
 * An invite carries a wall-clock date/time plus its own timezone. The detected
 * source is shown untouched — it stays the record of what was parsed — and when
 * that zone is not IST the same moment is also shown converted, so a reader
 * never has to do the arithmetic. The conversions asserted below are derived
 * from the source values, not from any IST wording found in the mail body.
 */

import { describe, expect, it } from 'vitest'
import {
  formatScheduleDateTime,
  formatScheduleIstDateTime,
  isIstTimeZone,
} from './istTime.js'

describe('isIstTimeZone', () => {
  it('accepts both IANA spellings of India Standard Time', () => {
    expect(isIstTimeZone('Asia/Kolkata')).toBe(true)
    expect(isIstTimeZone('Asia/Calcutta')).toBe(true)
    expect(isIstTimeZone('asia/calcutta')).toBe(true)
    expect(isIstTimeZone('IST')).toBe(true)
  })

  it('rejects everything else', () => {
    expect(isIstTimeZone('Etc/UTC')).toBe(false)
    expect(isIstTimeZone('America/New_York')).toBe(false)
    expect(isIstTimeZone('')).toBe(false)
    expect(isIstTimeZone(undefined)).toBe(false)
  })
})

describe('formatScheduleDateTime — the source field, which must not change', () => {
  it('keeps a foreign zone exactly as detected', () => {
    const label = formatScheduleDateTime('2026-08-13', '06:00', 'Etc/UTC')
    expect(label).toContain('13 Aug 2026')
    expect(label).toContain('6:00')
    expect(label).toContain('Etc/UTC')
  })

  it('keeps Asia/Calcutta spelled as detected rather than rewriting it', () => {
    const label = formatScheduleDateTime('2026-08-13', '11:30', 'Asia/Calcutta')
    expect(label).toContain('11:30')
    expect(label).toContain('Asia/Calcutta')
  })

  it('keeps the existing Asia/Kolkata rendering', () => {
    expect(formatScheduleDateTime('2026-08-13', '11:30', 'Asia/Kolkata')).toContain('IST')
  })
})

describe('formatScheduleIstDateTime', () => {
  it('adds nothing when the invite is already IST', () => {
    expect(formatScheduleIstDateTime('2026-08-13', '11:30', 'Asia/Kolkata')).toBe('')
    expect(formatScheduleIstDateTime('2026-08-13', '11:30', 'Asia/Calcutta')).toBe('')
  })

  it('converts UTC to IST — the reported case', () => {
    // 06:00 UTC on 13 Aug is 11:30 IST the same day.
    const ist = formatScheduleIstDateTime('2026-08-13', '06:00', 'Etc/UTC')
    expect(ist).toContain('11:30')
    expect(ist).toContain('13 Aug 2026')
    expect(ist).toContain('IST')
  })

  it('rolls the date forward when the conversion crosses midnight', () => {
    // 21:00 UTC on 12 Aug is 02:30 IST on 13 Aug.
    const ist = formatScheduleIstDateTime('2026-08-12', '21:00', 'Etc/UTC')
    expect(ist).toContain('2:30')
    expect(ist).toContain('13 Aug 2026')
  })

  it('rolls the date backward when the source zone is ahead of IST', () => {
    // 03:00 on 13 Aug in Tokyo (UTC+9) is 23:30 IST on 12 Aug.
    const ist = formatScheduleIstDateTime('2026-08-13', '03:00', 'Asia/Tokyo')
    expect(ist).toContain('11:30')
    expect(ist).toContain('12 Aug 2026')
  })

  it('converts a DST zone using the offset in force on that date', () => {
    // 13 Aug is EDT (UTC-4): 09:00 New York is 18:30 IST the same day.
    const ist = formatScheduleIstDateTime('2026-08-13', '09:00', 'America/New_York')
    expect(ist).toContain('6:30')
    expect(ist).toContain('13 Aug 2026')

    // 13 Jan is EST (UTC-5): 09:00 New York is 19:30 IST — an hour later,
    // which is only right if the tz database was consulted per date.
    const winter = formatScheduleIstDateTime('2026-01-13', '09:00', 'America/New_York')
    expect(winter).toContain('7:30')
    expect(winter).toContain('13 Jan 2026')
  })

  it('accepts a 12-hour clock', () => {
    expect(formatScheduleIstDateTime('2026-08-13', '6:00 AM', 'Etc/UTC')).toContain('11:30')
  })

  it('returns nothing rather than throwing on unusable input', () => {
    expect(formatScheduleIstDateTime('', '06:00', 'Etc/UTC')).toBe('')
    expect(formatScheduleIstDateTime('2026-08-13', '', 'Etc/UTC')).toBe('')
    expect(formatScheduleIstDateTime('2026-08-13', '06:00', '')).toBe('')
    expect(formatScheduleIstDateTime('2026-08-13', '99:99', 'Etc/UTC')).toBe('')
    expect(formatScheduleIstDateTime('2026-08-13', '06:00', 'Not/AZone')).toBe('')
  })
})
