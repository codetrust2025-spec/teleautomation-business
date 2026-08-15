/** Mute message-related sounds during local quiet hours (default 11 PM – 8 AM). */

const STORAGE_KEY = 'tf_sound_quiet_hours_enabled'
export const QUIET_HOURS_START = 23 // 11 PM
export const QUIET_HOURS_END = 8 // 8 AM

export function isQuietHoursEnabled() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'false') return false
    return true
  } catch {
    return true
  }
}

export function setQuietHoursEnabled(enabled) {
  try {
    localStorage.setItem(STORAGE_KEY, enabled ? 'true' : 'false')
  } catch {
    /* ignore */
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('sound-quiet-hours-change'))
  }
}

/**
 * True during 11:00 PM – 7:59 AM local time (overnight window).
 */
export function isInQuietHours(now = new Date()) {
  if (!isQuietHoursEnabled()) return false
  const mins = now.getHours() * 60 + now.getMinutes()
  const start = QUIET_HOURS_START * 60
  const end = QUIET_HOURS_END * 60
  if (start > end) {
    return mins >= start || mins < end
  }
  return mins >= start && mins < end
}

/** Whether message chime + inbox alert music should be silent right now. */
export function isMessageSoundMuted() {
  return isInQuietHours()
}

export function formatQuietHoursRange() {
  const fmt = (h) => {
    const period = h >= 12 ? 'PM' : 'AM'
    const hour12 = h % 12 === 0 ? 12 : h % 12
    return `${hour12} ${period}`
  }
  return `${fmt(QUIET_HOURS_START)} – ${fmt(QUIET_HOURS_END)}`
}

/** Ms until quiet period ends (for status label). */
export function msUntilQuietHoursEnd(now = new Date()) {
  const end = new Date(now)
  end.setHours(QUIET_HOURS_END, 0, 0, 0)
  if (end <= now) {
    end.setDate(end.getDate() + 1)
  }
  return Math.max(0, end.getTime() - now.getTime())
}
