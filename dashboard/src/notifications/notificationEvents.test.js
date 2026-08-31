import { beforeEach, describe, expect, it } from 'vitest'
import { installRecordingAudioStub } from './audioTestStub.js'
import { __resetNotificationEvents, notifyGmailReconnect, notifyTrackedMail } from './notificationEvents.js'

beforeEach(() => {
  installRecordingAudioStub()
  __resetNotificationEvents()
  sessionStorage.clear()
})

describe('Operations sound dispatch', () => {
  it('deduplicates recruitment events', () => {
    const event = { event: 'notification_created', classification: 'offer_received', event_id: 'one' }
    expect(notifyTrackedMail(event)).toBe(true)
    expect(notifyTrackedMail(event)).toBe(false)
  })

  it('deduplicates one notification even when updates used different event IDs', () => {
    expect(notifyTrackedMail({
      event: 'notification_created', classification: 'interview_confirmed',
      notification_id: 'notification-1', event_id: 'created-1',
    })).toBe(true)
    expect(notifyTrackedMail({
      event: 'notification_created', classification: 'interview_confirmed',
      notification_id: 'notification-1', event_id: 'booking-finished-2',
    })).toBe(false)
  })

  it('realerts after a Gmail mailbox recovers', () => {
    expect(notifyGmailReconnect([{ id: 'mailbox-1' }])).toBe(true)
    expect(notifyGmailReconnect([{ id: 'mailbox-1' }])).toBe(false)
    expect(notifyGmailReconnect([])).toBe(false)
    expect(notifyGmailReconnect([{ id: 'mailbox-1' }])).toBe(true)
  })
})
