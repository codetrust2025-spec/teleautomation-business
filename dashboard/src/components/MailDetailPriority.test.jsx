/**
 * The Selected email modal, in the order it is actually read.
 *
 * The email is the thing being judged, and it was third: below a nine-card
 * metric grid, inside a section capped at 260px that scrolled the label and
 * the From/To/Subject/Received lines along with the body — so about two lines
 * of the email were visible, and long lines ran off sideways because <pre>
 * overrides the wrapping it inherits.
 */
import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const css = fs.readFileSync(
  path.join(__dirname, '..', 'recruitmentMail.css'), 'utf8',
)
const jsx = fs.readFileSync(
  path.join(__dirname, 'MailMonitoringNotifications.jsx'), 'utf8',
)

/** Body of the last rule with this exact selector. */
function rule(selector) {
  const found = [...css.matchAll(
    new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`, 'g'),
  )]
  return found.length ? found[found.length - 1][1] : ''
}

describe('the email comes first', () => {
  it('is rendered above the metric grid', () => {
    const email = jsx.indexOf('className="mail-detail__original"')
    const metrics = jsx.indexOf('<dl>')
    expect(email).toBeGreaterThan(-1)
    expect(metrics).toBeGreaterThan(-1)
    expect(email).toBeLessThan(metrics)
  })

  it('sits directly under the dialog header', () => {
    expect(jsx).toMatch(/<\/header>\s*<section className="mail-detail__original"/)
  })

  it('is followed by the summary, then the two AI sections', () => {
    const email = jsx.indexOf('className="mail-detail__original"')
    const summary = jsx.indexOf('<strong>Summary</strong>')
    const reason = jsx.indexOf('<summary>Detection reason</summary>')
    const action = jsx.indexOf('<summary>Recommended action</summary>')
    expect(email).toBeLessThan(summary)
    expect(summary).toBeLessThan(reason)
    expect(reason).toBeLessThan(action)
  })
})

describe('the body is not clipped', () => {
  it('no longer caps the whole section', () => {
    // The cap here was what squeezed the body down to a couple of lines.
    expect(rule('\\.mail-detail__original')).not.toMatch(/max-height/)
  })

  it('caps the body alone, and generously', () => {
    const body = rule('\\.mail-detail__email-body')
    expect(body).toMatch(/max-height:\s*min\(46vh,\s*520px\)/)
    expect(body).toMatch(/overflow:\s*auto/)
  })

  it('gives an ordinary email room to need no scrolling', () => {
    expect(rule('\\.mail-detail__email-body')).toMatch(/min-height:\s*180px/)
  })

  it('wraps long lines instead of running off sideways', () => {
    // <pre> sets white-space: pre itself, overriding what it inherits.
    const body = rule('\\.mail-detail__email-body')
    expect(body).toMatch(/white-space:\s*pre-wrap/)
    expect(body).toMatch(/overflow-wrap:\s*anywhere/)
  })
})

describe('the header lines stay put', () => {
  it('keeps From, To, Received and Subject in the markup', () => {
    for (const label of ['From:', 'To:', 'Received:', 'Subject:']) {
      expect(jsx).toContain(`<b>${label}</b>`)
    }
  })

  it('leaves the meta outside the scrolling body', () => {
    // Both are children of the section; only the body scrolls, so the meta
    // cannot scroll out of view the way it used to.
    const meta = jsx.indexOf('className="mail-detail__email-meta"')
    const body = jsx.indexOf('className="mail-detail__email-body"')
    expect(meta).toBeGreaterThan(-1)
    expect(meta).toBeLessThan(body)
    expect(rule('\\.mail-detail__email-meta')).not.toMatch(/overflow/)
  })
})

describe('the lower AI sections fold away', () => {
  it('renders Detection reason and Recommended action as details', () => {
    expect(jsx).toMatch(/<details className="mail-detail__aside" open>\s*<summary>Detection reason<\/summary>/)
    expect(jsx).toMatch(/<details className="mail-detail__aside" open>\s*<summary>Recommended action<\/summary>/)
  })

  it('leaves the summary itself always visible', () => {
    // Priority 2 is not something the reader should have to open.
    const summaryBlock = jsx.slice(
      jsx.indexOf('className="mail-detail__copy"'),
      jsx.indexOf('<details className="mail-detail__aside"'),
    )
    expect(summaryBlock).toContain('<strong>Summary</strong>')
    expect(summaryBlock).not.toContain('<details')
  })

  it('opens them by default, so nothing is hidden without being asked', () => {
    expect(jsx).not.toMatch(/<details className="mail-detail__aside">/)
  })
})

describe('responsive', () => {
  it('shortens the body on narrow viewports', () => {
    expect(css).toMatch(/@media \(max-width: 900px\) \{\s*\.mail-detail__email-body \{[^}]*max-height:\s*40vh/)
  })

  it('declares that override after the base rule so it wins', () => {
    // Equal specificity: source order decides. The first attempt put this
    // above the base rule, where it did nothing.
    const base = css.indexOf('\n.mail-detail__email-body {')
    const override = css.search(/@media \(max-width: 900px\) \{\s*\.mail-detail__email-body/)
    expect(base).toBeGreaterThan(-1)
    expect(override).toBeGreaterThan(base)
  })
})
