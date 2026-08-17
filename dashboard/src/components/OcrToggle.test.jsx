/**
 * OCR is the one piece of the decommissioned Settings page that survives, so
 * its behaviour is covered here rather than just its render: load, admin
 * gating, the confirmed update, and both failure paths.
 *
 * The mock returns `{ confirm }` because that is what ConfirmProvider actually
 * puts on the context. The panel this control replaces did
 * `const confirm = useConfirm()` and then called it, which throws — its test
 * mocked a bare function and so never caught it. Keeping the mock faithful to
 * the provider is the point of these tests, not an incidental detail.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { OcrToggle } from './OcrToggle.jsx'

let role = 'admin'
let confirmResult = true
const confirmSpy = vi.fn(() => Promise.resolve(confirmResult))

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ username: 'tester', role }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: confirmSpy }),
}))

const POLICY_ON = {
  status: 'ok',
  enabled: true,
  mode: 'ocr+ollama',
  source: 'admin',
  env_default: true,
}
const POLICY_OFF = { ...POLICY_ON, enabled: false, mode: 'ollama-only' }

function jsonResponse(body, ok = true, status = 200) {
  return {
    ok,
    status,
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve(body),
  }
}

beforeEach(() => {
  role = 'admin'
  confirmResult = true
  confirmSpy.mockClear()
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('OcrToggle', () => {
  it('reads the current policy and shows it as ON', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse(POLICY_ON))))
    render(<OcrToggle />)
    await waitFor(() => expect(screen.getByRole('button')).toBeTruthy())
    expect(screen.getByRole('button').textContent).toBe('ON')
    expect(screen.getByRole('button').getAttribute('aria-pressed')).toBe('true')
  })

  it('turns OCR off through PUT /ai/ocr-policy after confirmation', async () => {
    const fetchMock = vi.fn((url, options) =>
      Promise.resolve(jsonResponse(options?.method === 'PUT' ? POLICY_OFF : POLICY_ON)),
    )
    vi.stubGlobal('fetch', fetchMock)
    render(<OcrToggle />)
    await waitFor(() => expect(screen.getByRole('button').textContent).toBe('ON'))

    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(screen.getByRole('button').textContent).toBe('OFF'))

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const put = fetchMock.mock.calls.find(([, o]) => o?.method === 'PUT')
    expect(put[0]).toContain('/ai/ocr-policy')
    expect(JSON.parse(put[1].body)).toEqual({ enabled: false })
  })

  it('does not call the API when the confirmation is declined', async () => {
    confirmResult = false
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(POLICY_ON)))
    vi.stubGlobal('fetch', fetchMock)
    render(<OcrToggle />)
    await waitFor(() => expect(screen.getByRole('button').textContent).toBe('ON'))

    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled())
    expect(fetchMock.mock.calls.some(([, o]) => o?.method === 'PUT')).toBe(false)
    expect(screen.getByRole('button').textContent).toBe('ON')
  })

  it('disables the control for a non-admin viewer', async () => {
    role = 'viewer'
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse(POLICY_ON))))
    render(<OcrToggle />)
    await waitFor(() => expect(screen.getByRole('button')).toBeTruthy())
    const button = screen.getByRole('button')
    expect(button.disabled).toBe(true)
    expect(button.getAttribute('title')).toContain('admin')
  })

  it('reports the server rejecting a non-admin update', async () => {
    vi.stubGlobal('fetch', vi.fn((url, options) =>
      Promise.resolve(options?.method === 'PUT'
        ? jsonResponse({ detail: 'Only an admin can change the OCR policy' }, false, 403)
        : jsonResponse(POLICY_ON)),
    ))
    render(<OcrToggle />)
    await waitFor(() => expect(screen.getByRole('button').textContent).toBe('ON'))

    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByRole('alert').textContent).toContain('admin')
  })

  it('degrades to a muted label when the policy cannot be read', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({}, false, 500))))
    render(<OcrToggle />)
    await waitFor(() => expect(screen.queryByRole('button')).toBeNull())
    expect(screen.getByText('OCR setting unavailable')).toBeTruthy()
  })
})
