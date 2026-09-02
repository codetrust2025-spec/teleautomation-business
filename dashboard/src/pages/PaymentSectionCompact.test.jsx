/**
 * The payment section is a field on this form, not a panel bolted onto it.
 *
 * Measured on the live page before this change: the box was 164px tall against
 * 85px for the interview-invite field directly below it, and read as a
 * separate card. Two things carried that height without earning it -- a
 * full-width "Save payment proof" button that was rendered and disabled
 * whenever there was nothing to save, which is most of the time, and a second
 * caption inside the drop zone repeating what the header already said.
 *
 * Folding Save into the header row and dropping the caption took it to 102px
 * idle, within 17px of the invite field, and the remainder is the header that
 * states the amount -- which is the one thing this section has to say that the
 * invite field does not.
 */
import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const css = fs.readFileSync(path.join(__dirname, '..', 'index.css'), 'utf8')

/** The body of the last rule with this exact selector.
 *
 * The selector arrives already regex-escaped (`\\.sbs-pay-card`); escaping it
 * again here is what made every one of these assertions read an empty string
 * and pass vacuously on the first run.
 */
function rule(selector) {
  const matches = rules(selector)
  return matches.length ? matches[matches.length - 1] : ''
}

/** Every rule body for this selector. `.sbs-input` is declared more than once
 *  -- the later ones adjust width and padding only -- so a token check has to
 *  look across all of them rather than at whichever happens to come last. */
function rules(selector) {
  return [...css.matchAll(new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`, 'g'))].map(m => m[1])
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function screenshot(label) {
  return new File([label], `${label}.jpg`, { type: 'image/jpeg' })
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const target = String(url)
    const reply = body => Promise.resolve({
      ok: true, status: 200, headers: { get: () => 'application/json' },
      json: () => Promise.resolve(body),
    })
    if (target.includes('/public/slots/payment-requirement')) {
      return reply({ status: 'ok', service_type: 'round_wise', amount_due: 5000, payment_required: true })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: [] })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
}

/** Reach the round-wise form, which is the one that asks for payment. */
async function roundWiseForm() {
  stubFetch()
  render(<SubmitSlotPage />)
  await screen.findByRole('button', { name: /Confirm booking/i })

  const serviceButton = document.querySelector('.sbs-select--custom')
  fireEvent.click(serviceButton)
  const roundWise = [...document.querySelectorAll('li')].find(li => /Round-wise/i.test(li.textContent))
  fireEvent.click(roundWise)

  const nameBox = document.querySelector('.sbs-field input')
  fireEvent.change(nameBox, { target: { value: 'Sowmya' } })
  await waitFor(() => expect(document.querySelector('.sbs-pay-card')).not.toBeNull())
  return document.querySelector('.sbs-pay-card')
}

const paymentInput = () => document.querySelector('.sbs-pay-card input[type="file"]')

describe('nothing is rendered that has nothing to do', () => {
  it('shows no Save button until a screenshot is attached', async () => {
    await roundWiseForm()
    expect(screen.queryByRole('button', { name: /save payment proof/i })).toBeNull()
  })

  it('shows Save as soon as one is', async () => {
    await roundWiseForm()
    attach(paymentInput(), [screenshot('pay-1')])
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /save payment proof/i })).toBeInTheDocument(),
    )
  })

  it('puts Save in the header row, beside the amount', async () => {
    await roundWiseForm()
    attach(paymentInput(), [screenshot('pay-1')])
    await waitFor(() => expect(document.querySelector('.sbs-pay-save')).not.toBeNull())
    // In the header, not stacked underneath it as a full-width block.
    expect(document.querySelector('.sbs-pay-head .sbs-pay-save')).not.toBeNull()
  })

  it('keeps the full name for assistive tech while showing a short label', async () => {
    await roundWiseForm()
    attach(paymentInput(), [screenshot('pay-1')])
    const save = await screen.findByRole('button', { name: /save payment proof/i })
    expect(save.textContent.trim()).toBe('Save')
  })

  it('does not repeat the section caption inside the drop zone', async () => {
    const card = await roundWiseForm()
    // The header already says what this is; a second caption was pure height.
    expect(card.querySelector('.submit-slot-field-label')).toBeNull()
  })
})

describe('the surface is the form surface', () => {
  it('uses the same background, border and radius as an ordinary input', () => {
    // Compared with whitespace normalised: the two rules were authored years
    // apart and write the same colour differently -- rgba(255,255,255,0.04)
    // against rgba(255, 255, 255, 0.04). Confirmed on the live page as well,
    // where both compute to the identical value at an 8px radius.
    const flat = text => text.replace(/\s+/g, '')
    const card = flat(rule('\\.sbs-pay-card'))
    const input = flat(rules('\\.sbs-input').join('\n'))
    for (const token of ['rgba(255,255,255,0.04)', 'rgba(148,163,184,0.15)']) {
      expect(card).toContain(token)
      expect(input).toContain(token)
    }
    expect(card).toContain('border-radius:0.5rem')
    expect(input).toContain('border-radius:0.5rem')
  })

  it('is defined exactly once, so the compaction is not overridden later', () => {
    // A previous attempt added a second .sbs-pay-card rule above the original.
    // Equal specificity, so the later one won and nothing visibly changed.
    expect((css.match(/\n\.sbs-pay-card \{/g) || []).length).toBe(1)
  })

  it('carries no amber until something is actually wrong', async () => {
    const card = await roundWiseForm()
    expect(card.className).not.toContain('sbs-pay-card--warn')
  })

  it('turns amber when the sequence reaches the payment', async () => {
    // Only when payment is the first thing missing. Everything ahead of it in
    // the order has to be satisfied first, or the form stops earlier and the
    // payment section is not what is being asked for.
    await roundWiseForm()
    fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i),
                     { target: { value: '9000066350' } })
    fireEvent.change(screen.getByPlaceholderText(/type the technology/i),
                     { target: { value: 'React JS' } })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L2' } })

    fireEvent.click(screen.getByRole('button', { name: /Confirm booking/i }))
    await waitFor(() =>
      expect(document.querySelector('.sbs-pay-card').className).toContain('sbs-pay-card--warn'),
    )
  })

  it('keeps amber reserved for the warn variant alone', () => {
    expect(rule('\\.sbs-pay-card')).not.toMatch(/251,\s*191,\s*36/)
    expect(rule('\\.sbs-pay-card--warn')).toMatch(/251,\s*191,\s*36/)
  })
})

describe('the header stays one line', () => {
  it('lays the amount out against the label', () => {
    expect(rule('\\.sbs-pay-head')).toContain('justify-content: space-between')
  })

  it('groups the amount and Save at the end of that line', () => {
    expect(rule('\\.sbs-pay-head__end')).toContain('display: flex')
  })

  it('does not let Save set the header height', () => {
    // The shared .sbs-pay-card .sbs-secondary-btn sizing would have made this
    // 36px tall and grown the row it sits in.
    expect(rule('\\.sbs-pay-card \\.sbs-pay-save')).toContain('min-height: 0')
  })

  it('keeps the drop zone at a full tap target on mobile', () => {
    // Compacting spacing must not shrink what a thumb has to hit.
    expect(rule('\\.sbs-pay-card \\.submit-slot-drop-wrap--compact \\.submit-slot-drop'))
      .toContain('min-height: 2.75rem')
  })
})
