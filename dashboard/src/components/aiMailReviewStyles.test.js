/**
 * Guards the AI Mail Review page against shipping class names no rule matches.
 *
 * The AI nodes pool did exactly that. The 2026-08 split copied AiNodeManager
 * into this repo but not its stylesheet, so `.sot-ai-node*` existed only in the
 * markup and the section rendered in production as raw stacked text: the data
 * was all there, unstyled. Nothing failed, because an unmatched class is not an
 * error anywhere in the toolchain — not in the build, not in a render test.
 *
 * These tests therefore assert the pairing directly: every structural class the
 * page renders must have a rule somewhere in the stylesheets the entry point
 * imports. They are deliberately narrow. The app as a whole still ships many
 * unstyled classes inherited from the split, and fixing that is separate work;
 * what must not happen again is this page losing its layout silently.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => readFileSync(join(SRC, rel), 'utf8')

// The stylesheets main.jsx actually imports. A rule living in an unimported
// file would not ship, so only these count as "styled".
const STYLESHEETS = ['index.css', 'businessShell.css', 'dailyOps.css', 'recruitmentMail.css']
const css = STYLESHEETS.map(read).join('\n')

const panel = read('components/RecruitmentMailPanelRedesign.jsx')
const toggle = read('components/OcrToggle.jsx')

/** Every class in a className="..." or className={`...`} in the given source. */
function classesIn(source, filter) {
  const found = new Set()
  for (const m of source.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
    const raw = (m[1] || m[2] || '').replace(/\$\{[^}]*\}/g, ' ')
    for (const cls of raw.split(/\s+/)) {
      if (cls && /^[a-zA-Z][\w-]*$/.test(cls) && filter(cls)) found.add(cls)
    }
  }
  return [...found].sort()
}

const hasRule = (cls) => new RegExp(`\\.${cls}(?![\\w-])`).test(css)

describe('AI nodes pool is styled', () => {
  const classes = classesIn(panel, (c) => c.startsWith('sot-ai-node'))

  it('renders at least the section, grid and card classes', () => {
    expect(classes).toEqual(expect.arrayContaining([
      'sot-ai-nodes', 'sot-ai-node-grid', 'sot-ai-node', 'sot-ai-node-title',
    ]))
  })

  for (const cls of ['sot-ai-nodes', 'sot-ai-node-grid', 'sot-ai-node', 'sot-ai-node-title',
                     'sot-ai-node-actions', 'sot-ai-node-error', 'sot-ai-node-head',
                     'sot-ai-node-state', 'sot-ai-node-empty', 'sot-ai-node-endpoint']) {
    it(`.${cls} has a rule`, () => {
      expect(hasRule(cls)).toBe(true)
    })
  }

  // The card is built from `sot-ai-node is-${node.status}`, so the state
  // modifiers carry the offline/degraded presentation the operator reads first.
  for (const state of ['is-online', 'is-degraded', 'is-offline']) {
    it(`node state .${state} is styled`, () => {
      expect(new RegExp(`\\.sot-ai-node\\.${state}(?![\\w-])`).test(css)).toBe(true)
    })
  }

  it('styles the dl rows the health fields render into', () => {
    expect(/\.sot-ai-node dl/.test(css)).toBe(true)
    expect(/\.sot-ai-node dt/.test(css)).toBe(true)
    expect(/\.sot-ai-node dd/.test(css)).toBe(true)
  })

  it('styles the header that carries the Refresh button', () => {
    expect(/\.sot-ai-nodes > header/.test(css)).toBe(true)
  })
})

/**
 * The card previously hid every `dt` and joined the values into a single
 * nowrap line with bullet separators, so a node that had reported nothing
 * rendered as "— • — • —". These assert the readable layout that replaced it,
 * since nothing else would notice it regressing back.
 */
describe('AI node card is a readable label/value grid', () => {
  const cardCss = css.slice(css.indexOf('.sot-ai-node {'), css.indexOf('.sot-alert {'))

  it('lays the card out as a vertical stack, not a horizontal strip', () => {
    const card = /\.sot-ai-node \{([^}]*)\}/.exec(cardCss)[1]
    expect(card).toMatch(/flex-direction:\s*column/)
    expect(card).not.toMatch(/grid-template-columns/)
  })

  it('gives each reading its own labelled row', () => {
    const dl = /\.sot-ai-node dl \{([^}]*)\}/.exec(cardCss)[1]
    expect(dl).toMatch(/display:\s*grid/)
    expect(dl).not.toMatch(/white-space:\s*nowrap/)
  })

  it('shows the labels rather than hiding them from sighted users', () => {
    const dt = /\.sot-ai-node dt \{([^}]*)\}/.exec(cardCss)[1]
    expect(dt).not.toMatch(/clip:/)
    expect(dt).not.toMatch(/position:\s*absolute/)
  })

  it('draws no bullet separators between readings', () => {
    expect(cardCss).not.toMatch(/div \+ div::before/)
  })

  it('mutes missing values instead of repeating loud dashes', () => {
    expect(/\.sot-ai-node-empty \{[^}]*color:/.test(cardCss)).toBe(true)
  })

  it('lets a long endpoint wrap instead of overflowing the card', () => {
    const dd = /\.sot-ai-node dd \{([^}]*)\}/.exec(cardCss)[1]
    expect(dd).toMatch(/overflow-wrap:\s*anywhere/)
  })

  it('keeps the actions together at the bottom', () => {
    const actions = /\.sot-ai-node-actions \{([^}]*)\}/.exec(cardCss)[1]
    expect(actions).toMatch(/margin-top:\s*auto/)
    expect(actions).toMatch(/flex-wrap:\s*wrap/)
  })

  it('collapses to one column and stretches buttons on small screens', () => {
    const mq = /@media \(max-width: 760px\) \{([\s\S]*?)\n\}/.exec(cardCss)[1]
    expect(mq).toMatch(/\.sot-ai-node-grid \{\s*grid-template-columns:\s*1fr/)
    expect(mq).toMatch(/\.sot-ai-node-actions button/)
  })
})

describe('AI node card markup carries the structure the CSS styles', () => {
  const card = panel.slice(panel.indexOf('function AiNodeManager'))

  it('puts the name and the state badge in a head row', () => {
    expect(card).toContain('sot-ai-node-head')
    expect(card).toContain('sot-ai-node-state')
  })

  it('marks missing readings so they can be muted', () => {
    expect(panel).toContain('sot-ai-node-empty')
    expect(card).toMatch(/nodeValue\(/)
  })

  it('renders the actions after the error rows, so buttons sit last', () => {
    expect(card.indexOf('Connection unavailable')).toBeLessThan(
      card.indexOf('sot-ai-node-actions'),
    )
  })

  it('still labels every reading', () => {
    for (const label of ['Health', 'Required models', 'Vision model', 'Response',
                         'Endpoint', 'Acceleration', 'Ollama', 'Last success', 'Last failure']) {
      expect(card).toContain(`<dt>${label}</dt>`)
    }
  })
})

describe('OCR control is styled', () => {
  for (const cls of classesIn(toggle, (c) => c.startsWith('sot-ocr-toggle'))) {
    it(`.${cls} has a rule`, () => {
      expect(hasRule(cls)).toBe(true)
    })
  }
})

describe('the page header keeps its layout', () => {
  for (const cls of ['sot-page', 'sot-header', 'sot-header-actions', 'sot-title']) {
    it(`.${cls} has a rule`, () => {
      expect(hasRule(cls)).toBe(true)
    })
  }
})

describe('no decommissioned styling came back', () => {
  it('carries no Handler Kit rules', () => {
    expect(/\.handler-kit/.test(css)).toBe(false)
  })
})
