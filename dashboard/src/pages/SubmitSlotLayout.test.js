/**
 * The booking form has to be comfortable on a phone.
 *
 * Most people book from a mobile screen, and measured on a 375x812 viewport the
 * form was cramped vertically, not horizontally:
 *
 *   "Confirm booking"   38px tall   (below the 44px touch-target minimum)
 *   text inputs         40px tall   (also below it)
 *   label -> control    2.88px gap  (reads as one undifferentiated block)
 *   form row gap        6.08px
 *   card height         457px of an 812px viewport - 56%, with room to spare
 *
 * The primary action was the smallest target on the page and the one every
 * booking has to hit.
 *
 * The form was cramped on BOTH axes: a 450px column on a wide screen, with
 * 38px controls inside it. Widening alone fixed nothing on a phone; raising
 * the touch targets alone left the desktop layout narrow. Both are asserted
 * here, because fixing either one on its own reads as a regression of the
 * other.
 *
 * Heights are pinned with `min-height` rather than left to padding plus font
 * metrics, because that drifts whenever the font does. The assertions are on
 * the stylesheet, since jsdom does no layout and a rendered-height check would
 * pass regardless of the value.
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'index.css'),
  'utf-8',
)

const ROOT_FONT_PX = 16
const MIN_TOUCH_PX = 44

/** Body of a top-level rule, comments stripped. */
function rule(selector) {
  const start = CSS.indexOf(`${selector} {`)
  expect(start, `${selector} not found in index.css`).toBeGreaterThan(-1)
  return CSS.slice(start, CSS.indexOf('}', start)).replace(/\/\*[\s\S]*?\*\//g, '')
}

/** A `rem`/`px` declaration converted to px. */
function px(selector, prop) {
  const m = rule(selector).match(new RegExp(`${prop}:\\s*([\\d.]+)(rem|px)`))
  expect(m, `${selector} has no ${prop}`).not.toBeNull()
  return m[2] === 'rem' ? parseFloat(m[1]) * ROOT_FONT_PX : parseFloat(m[1])
}

describe('submit-slot form is usable on a phone', () => {
  it('gives the primary action a comfortable target', () => {
    // Every booking ends here; it was the smallest thing on the page.
    expect(px('.sbs-cta', 'min-height')).toBeGreaterThanOrEqual(MIN_TOUCH_PX)
  })

  it('gives text inputs at least the minimum touch target', () => {
    expect(px('.sbs-input', 'min-height')).toBeGreaterThanOrEqual(MIN_TOUCH_PX)
  })

  it('pins those heights instead of leaving them to font metrics', () => {
    // Padding alone produced 38-40px and would move again with any font change.
    for (const sel of ['.sbs-cta', '.sbs-input']) {
      expect(rule(sel), `${sel} should declare min-height`).toMatch(/min-height:/)
    }
  })

  it('separates a label from its control enough to read as two things', () => {
    expect(px('.sbs-field', 'gap')).toBeGreaterThanOrEqual(4)
  })

  it('leaves real space between form rows', () => {
    expect(px('.sbs-form', 'gap')).toBeGreaterThanOrEqual(10)
  })

  it('uses the width available on a desktop screen', () => {
    // The form was cramped on both axes. A 450px column on a 1366px+ screen is
    // the horizontal half of that, and the controls inside are all width:100%
    // so they had nothing to fill.
    const max = px('.sbs-card', 'max-width')
    expect(max).toBeGreaterThanOrEqual(700)
    expect(max).toBeLessThanOrEqual(850)
  })

  it('still fills the viewport on small screens', () => {
    expect(rule('.sbs-card')).toMatch(/width:\s*100%/)
  })

  it('keeps the controls filling the card', () => {
    expect(rule('.sbs-input')).toMatch(/width:\s*100%/)
    expect(rule('.sbs-cta')).toMatch(/width:\s*100%/)
  })
})
