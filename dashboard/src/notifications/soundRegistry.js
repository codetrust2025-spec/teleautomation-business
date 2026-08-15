import { playInterviewBookingBell } from './sounds/interviewBookingBell.js'
import { playSelectionFanfare } from './sounds/selectionFanfare.js'
import { playGmailFault } from './sounds/gmailFault.js'
import { playInterviewReminder } from './sounds/interviewReminder.js'

export const NOTIFICATION_IDS = [
  'mail_interview_booking',
  'mail_selection',
  'gmail_reconnect',
  'interview_reminder',
]

export const NOTIFICATION_SOUNDS = {
  mail_interview_booking: { id: 'mail_interview_booking', label: 'Interview booking mail', play: playInterviewBookingBell, start: null, stop: null, loop: false, quietHours: false, crmToggle: false, dedupe: 'mail event id' },
  mail_selection: { id: 'mail_selection', label: 'Selection / offer mail', play: playSelectionFanfare, start: null, stop: null, loop: false, quietHours: false, crmToggle: false, dedupe: 'mail event id' },
  gmail_reconnect: { id: 'gmail_reconnect', label: 'Gmail reconnect fault', play: playGmailFault, start: null, stop: null, loop: false, quietHours: false, crmToggle: false, dedupe: 'mailbox id set' },
  interview_reminder: { id: 'interview_reminder', label: 'Interview reminder', play: playInterviewReminder, start: null, stop: null, loop: false, quietHours: false, crmToggle: false, dedupe: 'interview id' },
}

export function soundEntry(id) {
  const entry = NOTIFICATION_SOUNDS[id]
  if (!entry) throw new Error(`Unknown notification sound: ${id}`)
  return entry
}
