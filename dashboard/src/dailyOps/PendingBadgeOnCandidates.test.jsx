/**
 * The Candidates badge survives the Candidates page.
 *
 * The provider used to switch its query off there — `enabled: authReady &&
 * !deferCandidates` — and being disabled does not merely skip a fetch, it
 * clears the state: works to [], count to 0. The badge hides at zero, so it
 * vanished on the one page whose name it carries, and on the very page whose
 * new Pending Works tab lists the eight items it counts.
 *
 * The head start that switch was protecting is kept: on the Candidates page the
 * first read is deferred, not cancelled, so nothing competes with that page's
 * own loading and no false zero is ever published.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ enabled: false, authenticated: true, loading: false }),
}))

import {
  PendingWorksProvider,
  publishPendingWorkChanged,
  usePendingWorksContext,
} from './PendingWorksProvider.jsx'

const provider = fs.readFileSync(path.join(__dirname, 'PendingWorksProvider.jsx'), 'utf8')

const PAYLOAD = {
  status: 'ok',
  count: 8,
  candidate_count: 8,
  works: [],
  by_kind: { missing_resume: 7, missing_phone: 1 },
}

function stub(payload = PAYLOAD) {
  const spy = vi.fn(() => Promise.resolve({
    ok: true,
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve(payload),
  }))
  vi.stubGlobal('fetch', spy)
  return spy
}

function Probe() {
  const { count, candidateCount } = usePendingWorksContext()
  return <span data-testid="counts">{`${candidateCount}/${count}`}</span>
}

const show = (mainView) => render(
  <PendingWorksProvider mainView={mainView}><Probe /></PendingWorksProvider>,
)

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('on the Candidates page', () => {
  it('still reports the real count', async () => {
    stub()
    show('candidates')
    await act(async () => { vi.advanceTimersByTime(700) })
    await waitFor(() => expect(screen.getByTestId('counts')).toHaveTextContent('8/8'))
  })

  it('never settles on a zero it did not read', async () => {
    stub()
    show('candidates')
    await act(async () => { vi.advanceTimersByTime(700) })
    await waitFor(() => expect(screen.getByTestId('counts')).not.toHaveTextContent('0/0'))
  })

  it('defers the first read rather than cancelling it', async () => {
    const spy = stub()
    show('candidates')
    // Held back while the Candidates page loads its own data...
    await act(async () => { vi.advanceTimersByTime(100) })
    expect(spy).not.toHaveBeenCalled()
    // ...but it does happen.
    await act(async () => { vi.advanceTimersByTime(700) })
    expect(spy).toHaveBeenCalled()
  })

  it('asks for the whole pipeline, the same as everywhere else', async () => {
    const spy = stub()
    show('candidates')
    await act(async () => { vi.advanceTimersByTime(700) })
    await waitFor(() => expect(spy).toHaveBeenCalled())
    expect(String(spy.mock.calls[0][0])).toContain('/candidates/pending-works')
  })
})

describe('everywhere else', () => {
  it('reads straight away, with no delay', async () => {
    const spy = stub()
    show('dashboard')
    await waitFor(() => expect(spy).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByTestId('counts')).toHaveTextContent('8/8'))
  })
})

describe('it keeps up with changes', () => {
  it('re-reads when pending work is announced', async () => {
    stub()
    show('dashboard')
    await waitFor(() => expect(screen.getByTestId('counts')).toHaveTextContent('8/8'))
    stub({ ...PAYLOAD, count: 6, candidate_count: 5 })
    await act(async () => { publishPendingWorkChanged() })
    await waitFor(() => expect(screen.getByTestId('counts')).toHaveTextContent('5/6'))
  })

  it('ignores the number on the event, which belongs to another badge', async () => {
    // publishPendingWorkChanged carries an *interview* count. Taking it here
    // would show interviews on the Candidates badge.
    stub()
    show('dashboard')
    await waitFor(() => expect(screen.getByTestId('counts')).toHaveTextContent('8/8'))
    stub({ ...PAYLOAD, count: 8, candidate_count: 8 })
    await act(async () => { publishPendingWorkChanged(99) })
    await waitFor(() => expect(screen.getByTestId('counts')).toHaveTextContent('8/8'))
  })
})

describe('the wiring', () => {
  it('no longer disables the query on the Candidates page', () => {
    expect(provider).not.toContain('enabled: authReady && !deferCandidates')
    expect(provider).toContain('enabled: authReady,')
  })

  it('defers there instead', () => {
    expect(provider).toContain('deferMs: deferCandidates ? 600 : 0')
  })

  it('listens for changes the way the interview count already did', () => {
    expect((provider.match(/window\.addEventListener\(PENDING_CHANGED/g) || []).length)
      .toBe(2)
  })
})

describe('the Pending Works tab announces a real change', () => {
  const tab = fs.readFileSync(
    path.join(__dirname, '..', 'candidates', 'PendingWorksTab.jsx'), 'utf8',
  )

  it('tells the shell when its count moves', () => {
    expect(tab).toContain('publishPendingWorkChanged()')
  })

  it('only on a change, so it cannot talk itself in a circle', () => {
    expect(tab).toContain('announced.current !== null && announced.current !== candidates')
  })

  it('without a number, which would mean the wrong thing', () => {
    expect(tab).not.toMatch(/publishPendingWorkChanged\(\s*[a-zA-Z0-9_.]+\s*\)/)
  })
})
