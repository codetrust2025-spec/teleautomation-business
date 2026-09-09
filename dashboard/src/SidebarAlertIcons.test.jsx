/**
 * Sidebar items that report a count, not a section.
 *
 * Daily Ops and Mail Alerts both draw their glyph only when something is
 * waiting; the rest of the sidebar keeps its icon either way, because those
 * icons name a section rather than signal a number. The rule now lives on the
 * item as `alertIcon` instead of being a growing list of badge names.
 *
 * The count behind Daily Ops also has to move when attendance changes. Its
 * provider polls every two minutes, so without a signal the badge sat on a
 * stale number for up to that long after a status was set.
 */
import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { publishPendingWorkChanged } from './dailyOps/PendingWorksProvider.jsx'

const app = fs.readFileSync(path.join(__dirname, 'App.jsx'), 'utf8')
const provider = fs.readFileSync(
  path.join(__dirname, 'dailyOps', 'PendingWorksProvider.jsx'), 'utf8',
)
const roster = fs.readFileSync(
  path.join(__dirname, 'dailyOps', 'InterviewRoster.jsx'), 'utf8',
)

/** The sidebar's own rules, applied to one row. */
function SidebarRow({ item, badgeValue }) {
  const showIcon = item.alertIcon ? badgeValue > 0 : true
  return (
    <button type="button">
      <span data-testid="icon">{showIcon ? item.icon : ''}</span>
      <span>{item.label}</span>
      {badgeValue > 0 && (
        <span data-testid="badge" aria-label={`${badgeValue} pending`}>
          {badgeValue > 99 ? '99+' : badgeValue}
        </span>
      )}
    </button>
  )
}

const DAILY_OPS = { id: 'daily-ops', label: 'Daily Ops', icon: '▤', alertIcon: true }
const CANDIDATES = { id: 'candidates', label: 'Candidates', icon: '▣' }

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Daily Ops with nothing pending', () => {
  it('shows plain text, no icon and no badge', () => {
    render(<SidebarRow item={DAILY_OPS} badgeValue={0} />)
    expect(screen.getByText('Daily Ops')).toBeInTheDocument()
    expect(screen.getByTestId('icon').textContent).toBe('')
    expect(screen.queryByTestId('badge')).toBeNull()
  })

  it('keeps the icon slot so the label stays aligned', () => {
    // Fixed 20px in CSS; dropping the element would shift this row's text.
    render(<SidebarRow item={DAILY_OPS} badgeValue={0} />)
    expect(screen.getByTestId('icon')).toBeInTheDocument()
  })
})

describe('Daily Ops with work pending', () => {
  it('shows the icon and the real count', () => {
    render(<SidebarRow item={DAILY_OPS} badgeValue={5} />)
    expect(screen.getByTestId('icon').textContent).toBe('▤')
    expect(screen.getByTestId('badge').textContent).toBe('5')
  })

  it('caps the badge at 99+', () => {
    render(<SidebarRow item={DAILY_OPS} badgeValue={120} />)
    expect(screen.getByTestId('badge').textContent).toBe('99+')
  })

  it('says what the count is', () => {
    render(<SidebarRow item={DAILY_OPS} badgeValue={2} />)
    expect(screen.getByLabelText('2 pending')).toBeInTheDocument()
  })

  it('drops the icon again when the count reaches zero', () => {
    const { rerender } = render(<SidebarRow item={DAILY_OPS} badgeValue={2} />)
    expect(screen.getByTestId('icon').textContent).toBe('▤')
    rerender(<SidebarRow item={DAILY_OPS} badgeValue={0} />)
    expect(screen.getByTestId('icon').textContent).toBe('')
    expect(screen.queryByTestId('badge')).toBeNull()
  })
})

describe('items that are not counters keep their icon', () => {
  it('leaves Candidates showing its glyph at zero', () => {
    render(<SidebarRow item={CANDIDATES} badgeValue={0} />)
    expect(screen.getByTestId('icon').textContent).toBe('▣')
  })
})

describe('the shell applies this rule', () => {
  it('flags Daily Ops and Mail Alerts, and nothing else', () => {
    expect(app).toMatch(/id: 'daily-ops'[^}]*alertIcon: true/)
    expect(app).toMatch(/id: 'mail-notifications'[^}]*alertIcon: true/)
    expect((app.match(/alertIcon: true/g) || []).length).toBe(2)
  })

  it('reads the flag rather than naming badges one by one', () => {
    expect(app).toContain('const showIcon = item.alertIcon ? badgeValue > 0 : true')
  })

  it('still feeds Daily Ops the real pending interview count', () => {
    expect(app).toMatch(/item\.badge === 'interviews'\s*\?\s*pending\?\.pendingInterviewCount/)
  })

  it('counts candidates on the Candidates badge, not tasks between them', () => {
    // Was pending?.count -- the number of to-do items -- which made the badge
    // disagree with its own label as soon as one candidate had two gaps.
    expect(app).toContain('pending?.candidateCount || 0')
    expect(app).not.toContain('pending?.count || 0')
  })
})

describe('the count refreshes on a status change', () => {
  it('reloads when the roster announces one', async () => {
    // The provider listens for the signal; without it the badge waits for the
    // two-minute poll.
    expect(provider).toContain("const PENDING_CHANGED = 'teleautomation:pending-work-changed'")
    expect(provider).toMatch(/window\.addEventListener\(PENDING_CHANGED, onChanged\)/)
    // It takes a published count when there is one and reloads otherwise, so
    // the handler is a block rather than the one-liner it used to be.
    expect(provider).toMatch(/const onChanged = \(event\) => \{/)
    expect(provider).toMatch(/reload\(\{ silent: true \}\)/)
  })

  it('announces from every roster mutation path', () => {
    // Four of them: attendance, attendee, slot and the row actions. Each
    // changes what is pending.
    expect(roster).toContain('import { publishPendingWorkChanged }')
    // Four call sites plus the wrapper's own definition.
    expect((roster.match(/notifyRosterChanged\(\)/g) || []).length).toBe(5)
    // And exactly one direct call to the prop — the one inside the wrapper.
    // A second would be a path that mutates without telling the sidebar.
    expect((roster.match(/onRosterMutate\?\.\(\)/g) || []).length).toBe(1)
  })

  it('refreshes silently, so the sidebar does not flash', () => {
    expect(provider).toMatch(/reload\(\{ silent: true \}\)/)
  })

  it('removes its listener on unmount', () => {
    expect(provider).toMatch(/removeEventListener\(PENDING_CHANGED, onChanged\)/)
  })

  it('publishes without throwing when nothing is listening', () => {
    expect(() => act(() => publishPendingWorkChanged())).not.toThrow()
  })

  it('reaches a listener', async () => {
    const seen = vi.fn()
    window.addEventListener('teleautomation:pending-work-changed', seen)
    act(() => publishPendingWorkChanged())
    await waitFor(() => expect(seen).toHaveBeenCalled())
    window.removeEventListener('teleautomation:pending-work-changed', seen)
  })
})
