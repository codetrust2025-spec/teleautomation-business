/**
 * The Gmail operations page, with the list high enough to see.
 *
 * The header, the AI node grid and four tall metric cards took roughly half
 * the viewport before the mailbox table began, so the list an operator opened
 * the page for started below the fold. Nothing was removed to fix that: the
 * node grid collapses, the cards became chips, and search and Add Gmail moved
 * down to sit beside the list they act on.
 *
 * These are source and stylesheet assertions rather than a rendered layout,
 * because jsdom does no layout — it reports every height as zero, so a test
 * that "measured" the page here would be measuring nothing. What is pinned
 * instead is the structure and the specific spacing values, which is what a
 * regression would change.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const panel = readFileSync(resolve(HERE, 'RecruitmentMailPanelRedesign.jsx'), 'utf8')
const css = readFileSync(resolve(HERE, '..', 'recruitmentMail.css'), 'utf8')

describe('AI nodes collapse', () => {
  it('is a disclosure, not an always-open section', () => {
    expect(panel).toContain('<details className="sot-ai-nodes"')
    expect(panel).not.toContain('<section className="sot-ai-nodes"')
  })

  it('is collapsed by default', () => {
    const tag = panel.match(/<details className="sot-ai-nodes"[^>]*>/)[0]
    expect(tag).not.toMatch(/\bopen\b/)
  })

  it('still says whether the nodes are healthy while collapsed', () => {
    expect(panel).toContain('sot-ai-nodes-glance')
    expect(panel).toMatch(/\$\{online\}\/\$\{nodes\.length\} online/)
  })

  it('colours the glance when a node is down', () => {
    expect(panel).toContain('online < nodes.length ? " is-degraded" : ""')
    expect(css).toMatch(/\.sot-ai-nodes-glance\.is-degraded \{[^}]*--sot-amber/)
  })

  it('keeps Refresh from toggling the panel it sits in', () => {
    // Scoped to the AI-nodes disclosure: the page has an earlier <summary>
    // (the row action menu), so an unanchored search finds the wrong one.
    const nodes = panel.slice(panel.indexOf('<details className="sot-ai-nodes"'))
    const summary = nodes.slice(nodes.indexOf('<summary>'), nodes.indexOf('</summary>'))
    expect(summary).toContain('event.preventDefault()')
    expect(summary).toContain('event.stopPropagation()')
    expect(summary).toContain('onRefresh()')
  })

  it('replaces the native marker with one that still reads as expandable', () => {
    expect(css).toMatch(/\.sot-ai-nodes > summary::-webkit-details-marker \{\s*display: none;/)
    expect(css).toMatch(/\.sot-ai-nodes > summary::before \{[^}]*content: "▸"/)
    expect(css).toMatch(/\.sot-ai-nodes\[open\] > summary::before \{[^}]*rotate\(90deg\)/)
  })
})

describe('search and Add Gmail sit above the list', () => {
  it('are inside the toolbar row', () => {
    const toolbar = panel.slice(
      panel.indexOf('sot-list-toolbar-actions'),
      panel.indexOf('sot-list-toolbar-actions') + 700,
    )
    expect(toolbar).toContain('<SearchInput')
    expect(toolbar).toContain('+ Add Gmail')
  })

  it('come after the metric chips rather than above them', () => {
    expect(panel.indexOf('sot-mailbox-metrics'))
      .toBeLessThan(panel.indexOf('sot-list-toolbar'))
  })

  it('no longer sit in the section heading', () => {
    const head = panel.slice(
      panel.indexOf('<div className="sot-overview-head">'),
      panel.indexOf('sot-mailbox-metrics'),
    )
    expect(head).not.toContain('<SearchInput')
    expect(head).not.toContain('+ Add Gmail')
  })

  it('opens its form next to the button rather than further up the page', () => {
    expect(panel.indexOf('sot-list-toolbar'))
      .toBeLessThan(panel.indexOf('className="sot-add-mailbox-form"'))
  })

  it('keeps the tablist free of things that are not tabs', () => {
    const tablist = panel.slice(
      panel.indexOf('className="sot-mailbox-view-tabs"'),
      panel.indexOf('sot-list-toolbar-actions'),
    )
    expect(tablist).not.toContain('<SearchInput')
    expect(tablist.match(/role="tab"/g)).toHaveLength(2)
  })
})

describe('the metric cards became chips', () => {
  const scoped = css.slice(css.indexOf('Compact Gmail operations layout'))

  it('drops the fixed card height', () => {
    expect(scoped).toMatch(/\.sot-mailboxes-page \.sot-mailbox-metric \{[^}]*min-height: 0;/)
  })

  it('tightens their padding and spacing', () => {
    expect(scoped).toMatch(/\.sot-mailboxes-page \.sot-mailbox-metric \{[^}]*padding: 7px 10px;/)
    expect(scoped).toMatch(/\.sot-mailboxes-page \.sot-mailbox-metrics \{[^}]*margin: 0 0 10px;/)
  })

  it('still shows all four, reconnect count included', () => {
    const metrics = panel.slice(panel.indexOf('sot-mailbox-metrics'))
    for (const label of ['Total Mailboxes', 'Monitoring Active', 'Pending Gmail',
                         'Reconnect Required']) {
      expect(metrics).toContain(`label="${label}"`)
    }
  })

  it('keeps the reconnect chip hidden when nothing is broken', () => {
    expect(panel).toContain('{reconnectRequiredCount > 0 && (')
  })

  it('stays a single row on desktop', () => {
    expect(css).toMatch(/\.sot-mailbox-metrics \{[^}]*repeat\(4, minmax\(0, 1fr\)\)/)
  })
})

describe('nothing was taken away', () => {
  it('keeps every toolbar control', () => {
    for (const control of ['Global candidate filter', '<PureOllamaToggle />',
                           '<OcrToggle />', '↻ Refresh', '<SearchInput',
                           '+ Add Gmail']) {
      expect(panel).toContain(control)
    }
  })

  it('keeps both list tabs and the node actions', () => {
    expect(panel).toContain('Pending Gmail <span>')
    expect(panel).toContain('onMakePrimary(node)')
    expect(panel).toContain('Unload')
  })
})

describe('the compaction is scoped and ordered', () => {
  it('applies only to this page, so other pages keep their spacing', () => {
    const scoped = css.slice(css.indexOf('Compact Gmail operations layout'))
    const overrides = scoped.match(/^\.sot-(header|title|content-card|mailbox-metric|overview-head)[^,{]*\{/gm)
    expect(overrides).toBeNull()
  })

  it('comes after the rules it overrides, or it would be dead', () => {
    // Anchored to the start of a line so the scoped ".sot-mailboxes-page
    // .sot-mailbox-metric {" rules in the new block do not match themselves.
    const base = [...css.matchAll(/^\.sot-(?:mailbox-metric|header) \{/gm)]
    expect(base.length).toBeGreaterThan(0)
    const lastBase = Math.max(...base.map(match => match.index))
    expect(css.indexOf('Compact Gmail operations layout')).toBeGreaterThan(lastBase)
  })
})

describe('responsive', () => {
  const scoped = css.slice(css.indexOf('Compact Gmail operations layout'))

  it('gives search full width once the toolbar stacks', () => {
    const tablet = scoped.slice(scoped.indexOf('@media (max-width: 900px)'))
    expect(tablet).toMatch(/\.sot-list-toolbar-actions \{[^}]*width: 100%;/)
  })

  it('halves the chip row on tablet', () => {
    const tablet = scoped.slice(scoped.indexOf('@media (max-width: 900px)'))
    expect(tablet).toMatch(/repeat\(2, minmax\(0, 1fr\)\)/)
  })

  it('stacks the header on a phone', () => {
    const phone = scoped.slice(scoped.indexOf('@media (max-width: 560px)'))
    expect(phone).toMatch(/\.sot-mailboxes-page \.sot-header \{[^}]*flex-direction: column;/)
  })

  it('lets the toolbar wrap rather than overflow', () => {
    expect(scoped).toMatch(/\.sot-list-toolbar \{[^}]*flex-wrap: wrap;/)
    expect(scoped).toMatch(/\.sot-mailboxes-page \.sot-header-actions \{[^}]*flex-wrap: wrap;/)
  })
})
