/**
 * 8. SELECTION / OFFER MAIL — celebratory major fanfare.
 *
 * Identity: a brass-like arpeggio that lands on a sustained C major triad —
 * several notes sounding *at once*, which nothing else in the app does. This is
 * the best news the product delivers, and it is the only sound built from a
 * chord.
 *
 * Shares no generator with interviewBookingBell.js: that one is a single
 * melodic line of struck bells, this one is sustained sawtooth brass.
 */

import { masterGain, sustain, withAudioContext } from '../soundEngine.js'

const ARPEGGIO = [523.25, 659.25, 783.99] // C5 E5 G5
const CHORD = [523.25, 659.25, 783.99, 1046.5] // C major + octave
const STEP = 0.11

export function playSelectionFanfare() {
  return withAudioContext((ctx, t0) => {
    const master = masterGain(ctx, 0.42, t0)

    // Brass needs its top end tamed or the sawtooths turn into a buzz.
    const body = ctx.createBiquadFilter()
    body.type = 'lowpass'
    body.frequency.setValueAtTime(3200, t0)
    body.Q.setValueAtTime(0.8, t0)
    body.connect(master)

    // Run up the triad...
    ARPEGGIO.forEach((freq, index) => {
      sustain(ctx, body, {
        type: 'sawtooth',
        freq,
        start: t0 + index * STEP,
        duration: 0.22,
        peak: 0.4,
        attack: 0.03,
      })
    })

    // ...then hold the whole chord together.
    const chordAt = t0 + ARPEGGIO.length * STEP
    for (const freq of CHORD) {
      sustain(ctx, body, {
        type: 'sawtooth',
        freq,
        start: chordAt,
        duration: 1.0,
        peak: 0.34,
        attack: 0.04,
      })
    }

    const end = chordAt + 1.0
    master.gain.setValueAtTime(0.42, end - 0.2)
    master.gain.exponentialRampToValueAtTime(0.0001, end)
  })
}
