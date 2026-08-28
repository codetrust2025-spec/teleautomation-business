/**
 * Controls must not fall through to browser defaults.
 *
 * Same failure mode aiMailReviewStyles.test.js already documents for the AI
 * nodes pool: the bulk import brought markup without its stylesheet, so class
 * names matched no rule. Nothing in the toolchain treats that as an error, so
 * it only surfaces when someone looks at the page.
 *
 * Three controls were still doing it in production on 2026-08-28, and they were
 * found by measuring rather than reading — walking every sidebar view and
 * collecting elements whose computed style was a browser default (`2px outset`
 * borders, `rgb(240,240,240)` backgrounds, `appearance: auto` on a select):
 *
 *   Mail Alerts      button.mail-clear-all, select (no class)
 *   AI Mail Review   select (no class), the Linked / Pending Gmail tablist
 *
 * Daily Ops, Candidates, Slot Booking and Data Room were clean, which is why
 * the fix had to be narrow: whatever restored these four could not disturb the
 * screens that were already right.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => readFileSync(join(SRC, rel), 'utf8')

// Only rules in the stylesheets main.jsx imports actually ship.
const STYLESHEETS = ['index.css', 'businessShell.css', 'dailyOps.css', 'recruitmentMail.css']
const css = STYLESHEETS.map(read).join('\n')

const hasRule = (cls) => new RegExp(`\\.${cls.replace(/[-]/g, '\\-')}\\b`).test(css)

describe('controls that shipped without rules', () => {
  it('styles the Clear all notifications button', () => {
    expect(hasRule('mail-clear-all')).toBe(true)
  })

  it('styles the mailbox view tablist', () => {
    expect(hasRule('sot-mailbox-view-tabs')).toBe(true)
  })

  it('gives the tablist an active state, since the markup sets class="active"', () => {
    // RecruitmentMailPanelRedesign renders className={mode === "linked" ? "active" : ""}.
    // Without a rule for it, both tabs look identical and the selected one is
    // indistinguishable.
    expect(/\.sot-mailbox-view-tabs\s+button\.active/.test(css)).toBe(true)
  })

  it('spaces the count and hint inside each tab', () => {
    // They are separate <span>/<small> elements, so with no rule the tab reads
    // "Linked 1616 active" with nothing between the parts.
    expect(/\.sot-mailbox-view-tabs\s+button\s+span/.test(css)).toBe(true)
    expect(/\.sot-mailbox-view-tabs\s+button\s+small/.test(css)).toBe(true)
  })
})

describe('base control styling', () => {
  it('styles a select that carries no class of its own', () => {
    // Both offending selects are written as bare <select> with only an
    // aria-label, so nothing class-based could ever reach them.
    expect(/:where\(select\)\s*\{/.test(css)).toBe(true)
  })

  it('removes the native chevron and draws one back', () => {
    const block = css.split(':where(select) {')[1].split('}')[0]
    expect(block).toContain('appearance: none')
    expect(block).toContain('background-image')
  })

  it('darkens the option popup, which the OS draws white by default', () => {
    expect(/:where\(select\)\s+option\s*\{/.test(css)).toBe(true)
  })

  it('uses zero specificity so existing controls are untouched', () => {
    // :where() means every class rule still wins. Without it this base would
    // override .cand-input and friends and change screens that were correct.
    expect(css).toMatch(/:where\(select\)/)
    expect(css).not.toMatch(/^select\s*\{[^}]*appearance/m)
  })

  it('does NOT add a blanket button base', () => {
    // Measured on the AI Mail Review screen: 72 of 85 buttons are deliberately
    // transparent (icon buttons, nav items, link-style buttons), and the `*`
    // reset sets padding: 0 at the same specificity a :where(button) rule would
    // have. A base button rule would therefore give all 72 a visible box and
    // re-pad them, breaking four screens to fix two. Buttons whose classes were
    // never written get an explicit rule instead.
    expect(/:where\(button\)\s*\{/.test(css)).toBe(false)
  })
})

describe('the monitoring page keeps its rules', () => {
  const page = read('components/MailMonitoringNotifications.jsx')
  const structural = [...page.matchAll(/className="([^"{]+)"/g)]
    .flatMap((m) => m[1].split(/\s+/))
    .filter((c) => /^(mail|sot)-/.test(c))

  it('renders at least one structural class to check', () => {
    expect(structural.length).toBeGreaterThan(5)
  })

  it.each([...new Set(structural)])('%s has a rule', (cls) => {
    expect(hasRule(cls)).toBe(true)
  })
})
