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

  it('realerts after a Gmail mailbox recovers', () => {
    expect(notifyGmailReconnect([{ id: 'mailbox-1' }])).toBe(true)
    expect(notifyGmailReconnect([{ id: 'mailbox-1' }])).toBe(false)
    expect(notifyGmailReconnect([])).toBe(false)
    expect(notifyGmailReconnect([{ id: 'mailbox-1' }])).toBe(true)
  })
})
