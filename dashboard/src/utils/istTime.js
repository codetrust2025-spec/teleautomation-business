/**
 * All user-visible dates/times use India Standard Time (Asia/Kolkata).
 * Storage remains UTC ISO / unix; only display is localized to IST.
 */

export const IST_TIMEZONE = 'Asia/Kolkata'
export const IST_LOCALE = 'en-IN'

const DEFAULT_DATE_TIME = {
  timeZone: IST_TIMEZONE,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: true,
}

/** Parse ISO, unix sec/ms, or legacy "YYYY-MM-DD HH:MM UTC|IST" strings. */
export function parseInstant(value) {
  if (value == null || value === '') return null
  if (typeof value === 'number') {
    const ms = value < 1e12 ? value * 1000 : value
    const d = new Date(ms)
    return Number.isNaN(d.getTime()) ? null : d
  }
  const text = String(value).trim()
  if (!text) return null
  if (/^\d{2}:\d{2}:\d{2}$/.test(text)) return null
  if (text.endsWith(' UTC')) {
    const iso = text.replace(' UTC', ':00Z').replace(' ', 'T')
    const d = new Date(iso)
    return Number.isNaN(d.getTime()) ? null : d
  }
  if (text.endsWith(' IST')) {
    const iso = text.replace(' IST', '+05:30').replace(' ', 'T')
    const d = new Date(iso)
    return Number.isNaN(d.getTime()) ? null : d
  }
  const d = new Date(text)
  return Number.isNaN(d.getTime()) ? null : d
}

export function formatIstDateTime(value, options = {}) {
  const d = parseInstant(value)
  if (!d) return '—'
  return d.toLocaleString(IST_LOCALE, {
    ...DEFAULT_DATE_TIME,
    ...options,
    timeZone: IST_TIMEZONE,
  })
}

export function formatIstTime(value, options = {}) {
  const d = parseInstant(value)
  if (!d) return '—'
  return d.toLocaleTimeString(IST_LOCALE, {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
    timeZone: IST_TIMEZONE,
    ...options,
  })
}

/** Format a stored time-of-day value (HH:MM or HH:MM:SS) for display only. */
export function formatClockTime(value) {
  const raw = String(value || '').trim()
  const match = raw.match(/^(\d{1,2}):(\d{2})(?::\d{2})?$/)
  if (!match) return raw || '—'
  const hour = Number(match[1])
  const minute = Number(match[2])
  if (!Number.isInteger(hour) || hour < 0 || hour > 23 || minute < 0 || minute > 59) return raw
  const period = hour >= 12 ? 'PM' : 'AM'
  const hour12 = hour % 12 || 12
  return `${hour12}:${String(minute).padStart(2, '0')} ${period}`
}

export function formatIstDate(value, options = {}) {
  const d = parseInstant(value)
  if (!d) return '—'
  return d.toLocaleDateString(IST_LOCALE, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: IST_TIMEZONE,
    ...options,
  })
}

/**
 * Format a calendar schedule whose date and clock time are already expressed
 * in the supplied event timezone. The clock is not converted to another zone.
 */
export function formatScheduleDateTime(dateValue, timeValue, timeZone = '') {
  const rawDate = String(dateValue || '').trim()
  const dateMatch = rawDate.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!dateMatch) return rawDate || '—'

  const date = new Date(Date.UTC(
    Number(dateMatch[1]),
    Number(dateMatch[2]) - 1,
    Number(dateMatch[3]),
    12,
  ))
  const dateLabel = date.toLocaleDateString(IST_LOCALE, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  })

  const rawTime = String(timeValue || '').trim()
  const timeMatch = rawTime.match(/^(\d{1,2}):(\d{2})(?:\s*(AM|PM))?$/i)
  let timeLabel = ''
  if (timeMatch) {
    let hour = Number(timeMatch[1])
    const minute = Number(timeMatch[2])
    const period = String(timeMatch[3] || '').toUpperCase()
    if (period === 'AM') hour %= 12
    if (period === 'PM') hour = (hour % 12) + 12
    if (hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59) {
      timeLabel = new Date(Date.UTC(2000, 0, 1, hour, minute)).toLocaleTimeString(IST_LOCALE, {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
        timeZone: 'UTC',
      })
    }
  }

  // Deliberately unchanged: this field is the record of what was detected, so
  // the source zone is rendered exactly as parsed. The IST reading is offered
  // as a separate field rather than by rewriting this one.
  const zoneLabel = timeZone === IST_TIMEZONE ? ' IST' : (timeZone ? ` (${timeZone})` : '')
  return timeLabel ? `${dateLabel}, ${timeLabel}${zoneLabel}` : `${dateLabel}${zoneLabel}`
}

/**
 * Is this zone already India Standard Time?
 *
 * Invites arrive tagged Asia/Calcutta as often as Asia/Kolkata — they are the
 * same zone, the former being the older IANA name — so both must read as IST
 * rather than being labelled like a foreign zone.
 */
export function isIstTimeZone(timeZone) {
  const zone = String(timeZone || '').trim().toLowerCase()
  return zone === 'asia/kolkata' || zone === 'asia/calcutta' || zone === 'ist'
}

/** The offset of `timeZone` at a given instant, in ms. */
function timeZoneOffsetMs(utcMs, timeZone) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(new Date(utcMs))
  const field = {}
  for (const part of parts) field[part.type] = part.value
  const asUtc = Date.UTC(
    Number(field.year),
    Number(field.month) - 1,
    Number(field.day),
    Number(field.hour) % 24,
    Number(field.minute),
    Number(field.second),
  )
  return asUtc - utcMs
}

/**
 * Turn a wall-clock date/time in `timeZone` into a real instant.
 *
 * Asking Intl what the zone reads at a guessed instant, then correcting by the
 * difference, is the one conversion path here — no offset is ever added by
 * hand, so DST zones and half-hour zones are both handled by the platform's
 * own tz database. The second pass settles the guess when it landed on the
 * other side of a DST transition.
 */
function scheduleInstant(dateValue, timeValue, timeZone) {
  const dateMatch = String(dateValue || '').trim().match(/^(\d{4})-(\d{2})-(\d{2})$/)
  const timeMatch = String(timeValue || '').trim().match(/^(\d{1,2}):(\d{2})(?:\s*(AM|PM))?$/i)
  if (!dateMatch || !timeMatch || !timeZone) return null

  let hour = Number(timeMatch[1])
  const minute = Number(timeMatch[2])
  const period = String(timeMatch[3] || '').toUpperCase()
  if (period === 'AM') hour %= 12
  if (period === 'PM') hour = (hour % 12) + 12
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null

  const wallClock = Date.UTC(
    Number(dateMatch[1]),
    Number(dateMatch[2]) - 1,
    Number(dateMatch[3]),
    hour,
    minute,
  )
  let offset
  try {
    offset = timeZoneOffsetMs(wallClock, timeZone)
  } catch {
    return null // an unknown zone name must not take the view down
  }
  let instant = wallClock - offset
  const settled = timeZoneOffsetMs(instant, timeZone)
  if (settled !== offset) instant = wallClock - settled
  const date = new Date(instant)
  return Number.isNaN(date.getTime()) ? null : date
}

/**
 * The same moment rendered in IST, or '' when the schedule is already IST.
 *
 * Callers show this as a separate line so the invite's own wording is never
 * rewritten. Date rollover is implicit: the instant is formatted in IST, so a
 * late-evening UTC slot correctly reads as the next day.
 */
export function formatScheduleIstDateTime(dateValue, timeValue, timeZone = '') {
  if (!timeZone || isIstTimeZone(timeZone)) return ''
  const instant = scheduleInstant(dateValue, timeValue, timeZone)
  if (!instant) return ''
  return `${instant.toLocaleString(IST_LOCALE, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
    timeZone: IST_TIMEZONE,
  })} IST`
}

/** Short: Jun 3, 10:28 am (no year if same calendar year optional via caller) */
export function formatIstShort(value) {
  const d = parseInstant(value)
  if (!d) return '—'
  return d.toLocaleString(IST_LOCALE, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
    timeZone: IST_TIMEZONE,
  })
}

/** Logs / timeline — 24h clock in IST */
export function formatIstLogTime(time) {
  if (!time) return '--:--:--'
  if (typeof time === 'string' && /^\d{1,2}:\d{2}(?::\d{2})?$/.test(time)) return `${formatClockTime(time)} IST`
  const d = parseInstant(time)
  if (!d) return String(time)
  return formatIstTime(d)
}

/** Relative age label; absolute part in IST when > 48h */
/** YYYY-MM-DD in IST — for same-day comparisons in inbox UI */
export function istDayKey(value = new Date()) {
  const d = parseInstant(value) ?? new Date()
  return d.toLocaleDateString(IST_LOCALE, {
    timeZone: IST_TIMEZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
}

export function formatIstAge(value) {
  const d = parseInstant(value)
  if (!d) return null
  const mins = Math.floor((Date.now() - d.getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 48) return `${hrs}h ago`
  return formatIstDate(d, { year: undefined })
}
