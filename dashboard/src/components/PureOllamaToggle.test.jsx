/**
 * The Pure Ollama AI Mail Detection switch.
 *
 * ON is the default and the state an operator should normally see. What is
 * covered here is the behaviour rather than the render: the loaded state, the
 * admin gate, the confirmed write, and that the setting shown comes from the
 * server on every load rather than from anything the component remembers.
 *
 * The confirm mock returns `{ confirm }` because that is what ConfirmProvider
 * puts on the context — a bare function passes a careless test and throws in
 * the browser.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { PureOllamaToggle } from './PureOllamaToggle.jsx'

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
  status: 'ok', enabled: true, mode: 'pure-ollama',
  source: 'environment', env_default: true, updated_at: '', updated_by: '',
}
const POLICY_OFF = { ...POLICY_ON, enabled: false, mode: 'rules-first', source: 'admin' }

function stubFetch(get, put) {
  return vi.fn((url, options) => {
    const body = (options?.method === 'PUT') ? put : get
    return Promise.resolve({
      ok: body.ok !== false,
      status: body.status ?? 200,
      headers: { get: () => 'application/json' },
      json: () => Promise.resolve(body.payload ?? body),
    })
  })
}

beforeEach(() => { role = 'admin'; confirmResult = true; confirmSpy.mockClear() })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('what it shows', () => {
  it('reads the setting from the server on load', async () => {
    const fetchSpy = stubFetch(POLICY_ON)
    vi.stubGlobal('fetch', fetchSpy)
    render(<PureOllamaToggle />)
    await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('ON'))
    expect(String(fetchSpy.mock.calls[0][0])).toContain('/ai/pure-ollama-policy')
  })

  it('shows ON when detection is on', async () => {
    vi.stubGlobal('fetch', stubFetch(POLICY_ON))
    render(<PureOllamaToggle />)
    await waitFor(() => expect(screen.getByRole('button', { pressed: true })).toBeInTheDocument())
  })

  it('shows OFF when an admin has turned it off', async () => {
    vi.stubGlobal('fetch', stubFetch(POLICY_OFF))
    render(<PureOllamaToggle />)
    await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('OFF'))
    expect(screen.getByRole('button', { pressed: false })).toBeInTheDocument()
  })

  it('names the feature', async () => {
    vi.stubGlobal('fetch', stubFetch(POLICY_ON))
    render(<PureOllamaToggle />)
    await waitFor(() =>
      expect(screen.getByText('Pure Ollama AI Mail Detection')).toBeInTheDocument())
  })

  it('says so when the setting cannot be read', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    render(<PureOllamaToggle />)
    await waitFor(() =>
      expect(screen.getByText('Pure Ollama setting unavailable')).toBeInTheDocument())
  })
})

describe('changing it', () => {
  it('confirms before turning detection off', async () => {
    vi.stubGlobal('fetch', stubFetch(POLICY_ON, POLICY_OFF))
    render(<PureOllamaToggle />)
    await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('ON'))
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled())
    expect(confirmSpy.mock.calls[0][0].title).toMatch(/off/i)
  })

  it('writes the new value and shows what came back', async () => {
    const fetchSpy = stubFetch(POLICY_ON, POLICY_OFF)
    vi.stubGlobal('fetch', fetchSpy)
    render(<PureOllamaToggle />)
    await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('ON'))
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('OFF'))
    const put = fetchSpy.mock.calls.find(call => call[1]?.method === 'PUT')
    expect(JSON.parse(put[1].body)).toEqual({ enabled: false })
  })

  it('is reversible', async () => {
    vi.stubGlobal('fetch', stubFetch(POLICY_OFF, POLICY_ON))
    render(<PureOllamaToggle />)
    await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('OFF'))
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('ON'))
  })

  it('changes nothing when the confirmation is declined', async () => {
    confirmResult = false
    const fetchSpy = stubFetch(POLICY_ON, POLICY_OFF)
    vi.stubGlobal('fetch', fetchSpy)
    render(<PureOllamaToggle />)
    await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('ON'))
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled())
    expect(fetchSpy.mock.calls.some(call => call[1]?.method === 'PUT')).toBe(false)
  })

  it('is disabled for anyone who is not an admin', async () => {
    role = 'handler'
    vi.stubGlobal('fetch', stubFetch(POLICY_ON))
    render(<PureOllamaToggle />)
    await waitFor(() => expect(screen.getByRole('button')).toBeDisabled())
  })

  it('reports a refused write', async () => {
    vi.stubGlobal('fetch', stubFetch(POLICY_ON, {
      ok: false, status: 403, payload: { detail: 'nope' },
    }))
    render(<PureOllamaToggle />)
    await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('ON'))
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/Only an admin/i))
  })
})
