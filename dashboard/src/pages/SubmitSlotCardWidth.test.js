/**
 * The booking card must not collapse back to a narrow column on desktop.
 *
 * `.sbs-card` was pinned to `max-width: 450px`. On a 1366px or 1920px screen
 * that left the whole booking form as a narrow strip, while every control
 * inside it — `.sbs-input`, `.sbs-cta` — is `width: 100%` and so had nothing to
 * fill. It was reported as "compressed layout" on both production URLs.
 *
 * Verified against production before the change: the served asset
 * (`/assets/index-*.css`, 474358 bytes) contained
 * `.sbs-card{...max-width:450px...}` byte-for-byte matching the repo, so this
 * was never a stale-asset or wrong-checkout problem — the rule had simply never
 * been widened.
 *
 * These assertions are deliberately literal, on the stylesheet rather than a
 * rendered element: jsdom does not do layout, so a rendered-width assertion
 * would pass no matter what this value said.
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'index.css'),
  'utf-8',
)

/** The body of a top-level rule, comments stripped so a commented-out
 *  declaration cannot satisfy an assertion. */
function rule(selector) {
  const start = CSS.indexOf(`${selector} {`)
  expect(start, `${selector} not found in index.css`).toBeGreaterThan(-1)
  const end = CSS.indexOf('}', start)
  return CSS.slice(start, end).replace(/\/\*[\s\S]*?\*\//g, '')
}

describe('submit-slot booking card width', () => {
  it('is wide enough to use a desktop screen', () => {
    const body = rule('.sbs-card')
    const match = body.match(/max-width:\s*(\d+)px/)
    expect(match, '.sbs-card has no px max-width').not.toBeNull()

    const px = Number(match[1])
    expect(px).toBeGreaterThanOrEqual(700)
    expect(px).toBeLessThanOrEqual(850)
  })

  it('still fills the viewport on small screens', () => {
    // `width: 100%` under the cap is what keeps phones full-bleed. Removing it
    // in favour of a fixed width would fix desktop and break mobile.
    expect(rule('.sbs-card')).toMatch(/width:\s*100%/)
  })

  it('stays centred rather than pinned left', () => {
    // The card itself sets no margin; centring comes from the flex parent.
    expect(rule('.sbs-screen')).toMatch(/align-items:\s*center/)
  })

  it('keeps the controls filling the card', () => {
    // If these stopped being full-width, widening the card would leave the
    // form looking exactly as narrow as before.
    expect(rule('.sbs-input')).toMatch(/width:\s*100%/)
    expect(rule('.sbs-cta')).toMatch(/width:\s*100%/)
  })
})
