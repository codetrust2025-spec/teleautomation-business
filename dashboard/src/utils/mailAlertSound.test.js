import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { isTrackedMailAlert, showMailAlertNotification } from './mailAlertSound.js'

/**
 * The sounds moved to src/notifications/sounds/ — see soundRegistry.test.js for
 * the proof that selections and interview bookings are now different sounds
 * rather than two sweep counts of one klaxon. What stays this module's job is
 * the classification list and the desktop notification, including asking the
 * operating system not to add its own chime on top of ours.
 */
describe('tracked mail classification', () => {
  it('tracks selection and interview booking classifications only', () => {
    expect(isTrackedMailAlert('job_selection_confirmed')).toBe(true)
    expect(isTrackedMailAlert('interview_confirmed')).toBe(true)
    expect(isTrackedMailAlert('interview_cancelled')).toBe(true)
    expect(isTrackedMailAlert('mail_needs_review')).toBe(false)
    expect(isTrackedMailAlert('')).toBe(false)
    expect(isTrackedMailAlert(undefined)).toBe(false)
  })
})

describe('desktop notification', () => {
  let created

  beforeEach(() => {
    created = []
    class FakeNotification {
      constructor(title, options) {
        created.push({ title, options })
        this.title = title
        this.options = options
      }

      close() {}
    }
    FakeNotification.permission = 'granted'
    FakeNotification.requestPermission = vi.fn(() => Promise.resolve('granted'))
    vi.stubGlobal('Notification', FakeNotification)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('asks for a silent notification so the OS chime does not double our sound', () => {
    showMailAlertNotification({
      classification: 'offer_received',
      candidate_name: 'Asha',
      notification_id: 'n-1',
    })
    expect(created).toHaveLength(1)
    expect(created[0].options.silent).toBe(true)
  })

  it('keeps the booking notification silent too, and still shows it', () => {
    showMailAlertNotification({
      event: 'slot_auto_booked',
      candidate_name: 'Asha',
      company_name: 'Acme',
      interview_date: '2026-08-04',
      start_time: '17:00',
      booking_id: 'b-9',
    })
    expect(created).toHaveLength(1)
    expect(created[0].options.silent).toBe(true)
    expect(created[0].title).toContain('Interview Auto-Booked')
    expect(created[0].options.body).toContain('Asha')
  })

  it('stays quiet when permission was refused', () => {
    Notification.permission = 'denied'
    showMailAlertNotification({ classification: 'offer_received', candidate_name: 'Asha' })
    expect(created).toHaveLength(0)
  })
})
