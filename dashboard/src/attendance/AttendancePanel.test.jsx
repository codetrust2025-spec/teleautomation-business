import React from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AttendancePanel } from './AttendancePanel.jsx'

let authValue
let attendanceValue
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => authValue }))
vi.mock('./AttendanceContext.jsx', () => ({ useAttendance: () => attendanceValue }))

describe('attendance and earnings panel', () => {
  afterEach(() => cleanup())
  beforeEach(() => {
    authValue = { role: 'handler', displayName: 'Venu' }
    attendanceValue = {
      loading: false, error: '', refresh: vi.fn(),
      data: {
        profile: { display_name: 'Venu' },
        popup: { attendance_date: '2026-09-01', reason: 'ALREADY_MARKED', marked: true },
        eligibility: {
          period_start: '2026-09-01', period_end: '2026-09-01',
          attended_working_days: 1, required_working_days: 1,
          attendance_ratio: 100, eligibility_amount: 40000,
        },
        records: [{ attendance_date: '2026-09-01', marked_at: '2026-09-01T09:10:00+05:30', status: 'VERIFIED', office_network_verified: true }],
      },
    }
    vi.stubGlobal('fetch', vi.fn())
  })

  it('displays the server-authoritative ratio and eligibility tier', () => {
    render(<AttendancePanel />)
    expect(screen.getByText('100% ✅')).toBeInTheDocument()
    expect(screen.getByText('1 / 1 Working Days')).toBeInTheDocument()
    expect(screen.getByText(/₹40,000/)).toBeInTheDocument()
    expect(screen.getByText('Salary changes require payroll-admin approval')).toBeInTheDocument()
  })

  it('does not expose holiday or salary approval controls to handlers', () => {
    render(<AttendancePanel />)
    expect(screen.queryByRole('heading', { name: 'Public holidays' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Salary recommendations' })).not.toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('loads credential-safe admin data without rendering credentials', async () => {
    authValue = { role: 'admin', displayName: 'Operations Admin' }
    fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ holidays: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ recommendations: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        users: [{ name: 'Venu', username: 'venu-login', role: 'handler', active: true, password_configured: true, last_login: null, account_source: 'config/dashboard_handlers.yaml', account_id: 'handler:venu-login' }],
        findings: { duplicate_identity_groups: [], inactive_usernames: [], orphaned_usernames: [], multiple_auth_source_usernames: [] },
      }) })
    render(<AttendancePanel />)
    expect(await screen.findByText('venu-login')).toBeInTheDocument()
    expect(screen.getByText('Configured')).toBeInTheDocument()
    expect(screen.queryByText(/password123|hash|token/i)).not.toBeInTheDocument()
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
  })
})
