import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  notifyGmailReconnect: vi.fn(),
  notifyTrackedMail: vi.fn(() => true),
  traceMailAlert: vi.fn(),
}))
let mailHandler
vi.mock('./mailEventStream.js', () => ({
  subscribeMailEvents: handler => { mailHandler = handler; return () => {} },
  traceMailAlert: mocks.traceMailAlert,
}))
vi.mock('./notificationEvents.js', () => ({
  notifyGmailReconnect: mocks.notifyGmailReconnect,
  notifyTrackedMail: mocks.notifyTrackedMail,
}))
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => ({ authenticated: true }) }))
vi.mock('./useInterviewReminders.js', () => ({ useInterviewReminders: () => {} }))

const { GlobalNotificationSounds } = await import('./GlobalNotificationSounds.jsx')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

it('subscribes globally without an Operations page mounted', () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: false }) })))
  render(<GlobalNotificationSounds />)
  expect(mailHandler).toBeTypeOf('function')
})

it('never turns a reconnect replay into a new-alert sound', () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: false }) })))
  render(<GlobalNotificationSounds />)
  const replay = {
    event: 'notification_created', event_id: 'historical-event',
    notification_id: 'historical-notification', classification: 'candidate_rejected',
    delivery_source: 'replay',
  }
  mailHandler(replay, { fromSocket: true })
  expect(mocks.notifyTrackedMail).not.toHaveBeenCalled()
  expect(mocks.traceMailAlert).toHaveBeenCalledWith('sound_suppressed_replay', replay)
})

it('still sounds exactly once for a live notification creation', () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: false }) })))
  render(<GlobalNotificationSounds />)
  const live = {
    event: 'notification_created', event_id: 'live-event',
    notification_id: 'live-notification', classification: 'offer_received',
    delivery_source: 'live',
  }
  mailHandler(live, { fromSocket: true })
  expect(mocks.notifyTrackedMail).toHaveBeenCalledTimes(1)
  expect(mocks.traceMailAlert).toHaveBeenCalledWith('sound', live)
})
