import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

let mailHandler
vi.mock('./mailEventStream.js', () => ({ subscribeMailEvents: handler => { mailHandler = handler; return () => {} } }))
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => ({ authenticated: true }) }))
vi.mock('./useInterviewReminders.js', () => ({ useInterviewReminders: () => {} }))

const { GlobalNotificationSounds } = await import('./GlobalNotificationSounds.jsx')

afterEach(() => cleanup())

it('subscribes globally without an Operations page mounted', () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: false }) })))
  render(<GlobalNotificationSounds />)
  expect(mailHandler).toBeTypeOf('function')
})
