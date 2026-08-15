/**
 * 9. GMAIL RECONNECT FAULT — descending technical error signal.
 *
 * Identity: four square-wave steps falling in equal, un-musical intervals,
 * ending in a low buzz with a noise scrape over it. Machine failure, not
 * music: no triad, no glide, no bell. An admin should hear "something is
 * broken" without deciding which candidate it concerns.
 *
 * Deliberately unlike the SLA siren (which rises and falls continuously) and
 * unlike the call ring (which is a steady two-tone cadence).
 */

import { masterGain, noiseBurst, pluck, sustain, withAudioContext } from '../soundEngine.js'

const STEPS = [700, 560, 420, 280]
const STEP_MS = 0.13

export function playGmailFault() {
  return withAudioContext((ctx, t0) => {
    const master = masterGain(ctx, 0.5, t0)

    STEPS.forEach((freq, index) => {
      pluck(ctx, master, {
        type: 'square',
        freq,
        start: t0 + index * STEP_MS,
        duration: 0.1,
        peak: 0.6,
      })
    })

    // The terminal fault: a low, flat buzz with a scrape of noise across it.
    const tailAt = t0 + STEPS.length * STEP_MS
    sustain(ctx, master, {
      type: 'square',
      freq: 140,
      start: tailAt,
      duration: 0.5,
      peak: 0.45,
      attack: 0.01,
    })
    noiseBurst(ctx, master, {
      start: tailAt,
      duration: 0.22,
      peak: 0.2,
      filterType: 'highpass',
      frequency: 2200,
      Q: 0.6,
    })

    const end = tailAt + 0.5
    master.gain.setValueAtTime(0.5, end - 0.1)
    master.gain.exponentialRampToValueAtTime(0.0001, end)
  })
}
