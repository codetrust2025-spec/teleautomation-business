/**
 * OCR policy is an Operations-owned capability that the split lost, because in
 * the monolith it lived inside Marketing's AI-settings overlay and that overlay
 * was not carried over. These tests cover the behaviour, not just the render:
 * load, admin gating, the confirmed update, and both failure paths.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { OcrPolicyPanel } from './OcrPolicyPanel.jsx'

let role = 'admin'
let confirmResult = true

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ username: 'tester', role }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => () => Promise.resolve(confirmResult),
}))

const POLICY_ON = {
  status: 'ok',
  enabled: true,
  mode: 'ocr+ollama',
  source: 'admin',
  env_default: true,
  updated_at: '2026-08-15T10:00:00Z',
  updated_by: 'someone',
}

function jsonResponse(body, ok = true, status = 200) {
  return {
    ok,
    status,
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve(body),
  }
}

function routeFetch(handlers) {
  return vi.fn((url, options) => {
    const u = String(url)
    if (u.includes('/ai/ocr-policy/audit')) return Promise.resolve(handlers.audit())
    if (options?.method === 'PUT') return Promise.resolve(handlers.put(options))
    return Promise.resolve(handlers.get())
  })
}

beforeEach(() => {
  role = 'admin'
  confirmResult = true
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('OcrPolicyPanel', () => {
  it('shows a loading state, then the current policy', async () => {
    global.fetch = routeFetch({
      get: () => jsonResponse(POLICY_ON),
      audit: () => jsonResponse({ status: 'ok', entries: [] }),
      put: () => jsonResponse(POLICY_ON),
    })
    render(<OcrPolicyPanel />)
    expect(screen.getByRole('status')).toHaveTextContent(/loading/i)
    await waitFor(() => expect(screen.getByText(/OCR is/)).toBeInTheDocument())
    expect(screen.getByText('ON')).toBeInTheDocument()
    expect(screen.getByText(/ocr\+ollama/)).toBeInTheDocument()
  })

  it('turns OCR off through PUT /ai/ocr-policy and reports success', async () => {
    const fetchMock = routeFetch({
      get: () => jsonResponse(POLICY_ON),
      audit: () => jsonResponse({ status: 'ok', entries: [] }),
      put: () => jsonResponse({ ...POLICY_ON, enabled: false, mode: 'ollama-only' }),
    })
    global.fetch = fetchMock
    render(<OcrPolicyPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: /turn ocr off/i })).toBeEnabled())

    fireEvent.click(screen.getByRole('button', { name: /turn ocr off/i }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/OCR is now OFF/i))
    const put = fetchMock.mock.calls.find(([, o]) => o?.method === 'PUT')
    expect(put).toBeTruthy()
    expect(String(put[0])).toContain('/ai/ocr-policy')
    expect(JSON.parse(put[1].body)).toEqual({ enabled: false })
    expect(put[1].credentials).toBe('include')
  })

  it('does not call the API when the confirmation is declined', async () => {
    confirmResult = false
    const fetchMock = routeFetch({
      get: () => jsonResponse(POLICY_ON),
      audit: () => jsonResponse({ status: 'ok', entries: [] }),
      put: () => jsonResponse(POLICY_ON),
    })
    global.fetch = fetchMock
    render(<OcrPolicyPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: /turn ocr off/i })).toBeEnabled())

    fireEvent.click(screen.getByRole('button', { name: /turn ocr off/i }))

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([, o]) => o?.method === 'PUT')).toBe(false)
    })
  })

  it('disables the control for a non-admin viewer', async () => {
    role = 'staff'
    global.fetch = routeFetch({
      get: () => jsonResponse(POLICY_ON),
      audit: () => jsonResponse({ status: 'ok', entries: [] }),
      put: () => jsonResponse(POLICY_ON),
    })
    render(<OcrPolicyPanel />)
    await waitFor(() => expect(screen.getByText(/only an admin can change/i)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /turn ocr off/i })).toBeDisabled()
  })

  it('surfaces a load failure with a retry', async () => {
    global.fetch = routeFetch({
      get: () => jsonResponse({}, false, 500),
      audit: () => jsonResponse({ status: 'ok', entries: [] }),
      put: () => jsonResponse(POLICY_ON),
    })
    render(<OcrPolicyPanel />)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/could not read/i))
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('reports the server rejecting a non-admin update', async () => {
    global.fetch = routeFetch({
      get: () => jsonResponse(POLICY_ON),
      audit: () => jsonResponse({ status: 'ok', entries: [] }),
      put: () => jsonResponse({ detail: 'Only an admin can change the OCR policy' }, false, 403),
    })
    render(<OcrPolicyPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: /turn ocr off/i })).toBeEnabled())

    fireEvent.click(screen.getByRole('button', { name: /turn ocr off/i }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/only an admin/i))
  })

  it('still renders the control when the admin audit trail is unavailable', async () => {
    global.fetch = routeFetch({
      get: () => jsonResponse(POLICY_ON),
      audit: () => jsonResponse({}, false, 403),
      put: () => jsonResponse(POLICY_ON),
    })
    render(<OcrPolicyPanel />)
    await waitFor(() => expect(screen.getByText(/OCR is/)).toBeInTheDocument())
    expect(screen.queryByText(/recent changes/i)).not.toBeInTheDocument()
  })
})
