import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AttendanceProvider } from './AttendanceContext.jsx'

let authValue
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => authValue }))

function response(payload, ok = true, status = ok ? 200 : 403) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(payload) })
}

function statusPayload({ name = 'Venu', eligible = true, marked = false, reason, nextCheck } = {}) {
  return {
    status: 'ok',
    profile: { username: name.toLowerCase(), display_name: name, role: 'handler' },
    popup: {
      eligible, marked, working_day: true,
      reason: reason || (marked ? 'ALREADY_MARKED' : eligible ? 'ATTENDANCE_REQUIRED' : 'BEFORE_START_TIME'),
      attendance_date: '2026-09-01',
      next_check_at: nextCheck || '2026-09-02T09:00:00+05:30',
    },
    eligibility: { attended_working_days: marked ? 1 : 0, required_working_days: 1, attendance_ratio: marked ? 100 : 0, eligibility_amount: marked ? 40000 : 15000 },
    records: [],
  }
}

describe('morning attendance experience', () => {
  beforeEach(() => {
    authValue = { authenticated: true, role: 'handler', displayName: 'Venu' }
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('shows the authenticated user display name and not a hardcoded employee', async () => {
    fetch.mockReturnValue(response(statusPayload({ name: 'Venu' })))
    render(<AttendanceProvider><div>Workspace</div></AttendanceProvider>)
    expect(await screen.findByRole('heading', { name: 'Good morning, Venu 👋' })).toBeInTheDocument()
    expect(screen.queryByText(/Thrilok/)).not.toBeInTheDocument()
  })

  it('shows a different authenticated user their own name', async () => {
    authValue = { authenticated: true, role: 'handler', displayName: 'Pavan' }
    fetch.mockReturnValue(response(statusPayload({ name: 'Pavan' })))
    render(<AttendanceProvider><div>Workspace</div></AttendanceProvider>)
    expect(await screen.findByRole('heading', { name: 'Good morning, Pavan 👋' })).toBeInTheDocument()
  })

  it('does not show before 9 and becomes eligible at the server-provided boundary', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-01T08:30:00+05:30'))
    fetch
      .mockReturnValueOnce(response(statusPayload({ eligible: false, reason: 'BEFORE_START_TIME', nextCheck: '2026-09-01T09:00:00+05:30' })))
      .mockReturnValue(response(statusPayload({ eligible: true, nextCheck: '2026-09-01T09:05:00+05:30' })))
    render(<AttendanceProvider><div>Workspace</div></AttendanceProvider>)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await act(async () => { vi.advanceTimersByTime(30 * 60 * 1000 + 500); await Promise.resolve(); await Promise.resolve() })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('dismissal does not mark attendance and the reminder can return later', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-01T10:00:00+05:30'))
    fetch.mockReturnValue(response(statusPayload()))
    render(<AttendanceProvider><div>Workspace</div></AttendanceProvider>)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
    await act(async () => { vi.advanceTimersByTime(30 * 60 * 1000 + 100); await Promise.resolve() })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('shows the office Wi-Fi rejection and keeps attendance unmarked', async () => {
    fetch
      .mockReturnValueOnce(response(statusPayload()))
      .mockReturnValueOnce(response({ detail: 'Connect to Office Wi-Fi to mark attendance.' }, false, 403))
    render(<AttendanceProvider><div>Workspace</div></AttendanceProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Mark Attendance' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Connect to Office Wi-Fi to mark attendance.')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('closes after success, confirms the real time, and does not reappear when status is marked', async () => {
    fetch
      .mockReturnValueOnce(response(statusPayload()))
      .mockReturnValueOnce(response({ status: 'marked', attendance: { marked_at: '2026-09-01T13:07:00+05:30' } }))
      .mockReturnValue(response(statusPayload({ marked: true, eligible: false })))
    render(<AttendanceProvider><div>Workspace</div></AttendanceProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Mark Attendance' }))
    expect(await screen.findByRole('status')).toHaveTextContent(/Attendance marked at 1:07 pm/i)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(fetch.mock.calls.filter(call => call[1]?.method === 'POST')).toHaveLength(1)
  })

  it('does not show on Sunday, a public holiday, or after a previous mark', async () => {
    for (const payload of [
      statusPayload({ eligible: false, reason: 'SUNDAY' }),
      statusPayload({ eligible: false, reason: 'PUBLIC_HOLIDAY' }),
      statusPayload({ eligible: false, marked: true }),
    ]) {
      fetch.mockReset()
      fetch.mockReturnValue(response(payload))
      const view = render(<AttendanceProvider><div>Workspace</div></AttendanceProvider>)
      await waitFor(() => expect(fetch).toHaveBeenCalled())
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      view.unmount()
    }
  })
})
