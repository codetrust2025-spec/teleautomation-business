import { SELECTION_CLASSIFICATIONS, isTrackedMailAlert, showMailAlertNotification } from '../utils/mailAlertSound.js'
import { describeReconnectTargets } from '../utils/mailboxStatus.js'
import { playNotification } from './notificationSounds.js'

const MAIL_DEDUPE_MS = 8000
const recentMailEvents = new Map()
const GMAIL_ALERTED_KEY = 'teleautomation:operations:gmail-reconnect-alerted'

function firstSightOfMailEvent(eventId) {
  const now = Date.now()
  for (const [key, at] of recentMailEvents) if (now - at > MAIL_DEDUPE_MS) recentMailEvents.delete(key)
  if (eventId == null) return true
  const key = String(eventId)
  if (recentMailEvents.has(key)) return false
  recentMailEvents.set(key, now)
  return true
}

export function notifyTrackedMail(payload) {
  if (payload?.event !== 'notification_created') return false
  if (!isTrackedMailAlert(payload?.classification)) return false
  if (!firstSightOfMailEvent(payload.event_id || payload.notification_id)) return false
  const selection = SELECTION_CLASSIFICATIONS.includes(payload.classification)
  const played = playNotification(selection ? 'mail_selection' : 'mail_interview_booking')
  showMailAlertNotification(payload)
  return played
}

function readAlertedMailboxIds() {
  try { return new Set(JSON.parse(sessionStorage.getItem(GMAIL_ALERTED_KEY) || '[]').map(String)) } catch { return new Set() }
}

export function notifyGmailReconnect(disconnected = []) {
  const alerted = readAlertedMailboxIds()
  const newly = disconnected.filter(row => !alerted.has(String(row.id)))
  if (newly.length) {
    playNotification('gmail_reconnect')
    showMailAlertNotification({ status: 'Gmail connection expired', candidate_name: describeReconnectTargets(newly), notification_id: `gmail-reconnect-${newly.map(row => row.id).join('-')}` })
  }
  try { sessionStorage.setItem(GMAIL_ALERTED_KEY, JSON.stringify(disconnected.map(row => String(row.id)))) } catch {}
  return newly.length > 0
}

export function notifyInterviewReminder() {
  return playNotification('interview_reminder')
}

export function __resetNotificationEvents() {
  recentMailEvents.clear()
}
