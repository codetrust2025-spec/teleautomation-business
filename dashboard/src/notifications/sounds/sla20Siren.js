/**
 * 6. REPLY SLA — 20 MINUTES — urgent siren sweep (looping).
 *
 * Identity: one continuous sawtooth gliding up and back down through a
 * resonant filter — an unbroken 1.4 s wail with no rhythmic articulation at
 * all. That is the opposite of the 10-minute alert, which is nothing but
 * rhythm. Told apart instantly even at low volume.
 *
 * Written from scratch rather than as a parameter on the 10-minute pulse, so
 * the two tiers can never converge again.
 */

import { masterGain, withAudioContext, getSharedAudioContext, releaseNodes } from '../soundEngine.js'

const LOW = 420
const HIGH = 1250
const SWEEP = 1.4
const LOOP_MS = 2000

let timer = null
let nodes = []

function sirenSweep(ctx, t0) {
  const master = masterGain(ctx, 0.5, t0)
  nodes.push(master)

  // A resonant lowpass tracking the glide is what turns a sawtooth into a
  // siren rather than a rising buzz.
  const filter = ctx.createBiquadFilter()
  filter.type = 'lowpass'
  filter.Q.setValueAtTime(7, t0)
  filter.frequency.setValueAtTime(LOW * 3, t0)
  filter.frequency.linearRampToValueAtTime(HIGH * 3, t0 + SWEEP / 2)
  filter.frequency.linearRampToValueAtTime(LOW * 3, t0 + SWEEP)
  filter.connect(master)
  nodes.push(filter)

  const osc = ctx.createOscillator()
  osc.type = 'sawtooth'
  osc.frequency.setValueAtTime(LOW, t0)
  osc.frequency.linearRampToValueAtTime(HIGH, t0 + SWEEP / 2)
  osc.frequency.linearRampToValueAtTime(LOW, t0 + SWEEP)

  const g = ctx.createGain()
  g.gain.setValueAtTime(0.0001, t0)
  g.gain.linearRampToValueAtTime(0.7, t0 + 0.12)
  g.gain.setValueAtTime(0.7, t0 + SWEEP - 0.2)
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + SWEEP)

  osc.connect(g)
  g.connect(filter)
  osc.start(t0)
  osc.stop(t0 + SWEEP + 0.03)
  nodes.push(osc, g)
}

/** One sweep, no loop — used by the preview harness and by tests. */
export function playSla20Siren() {
  return withAudioContext((ctx, t0) => sirenSweep(ctx, t0))
}

export function startSla20Siren(intervalMs = LOOP_MS) {
  if (timer != null) return true
  const started = withAudioContext((ctx, t0) => sirenSweep(ctx, t0))
  if (!started) return false
  timer = window.setInterval(() => {
    const ctx = getSharedAudioContext()
    if (ctx && timer != null) sirenSweep(ctx, ctx.currentTime)
  }, intervalMs)
  return true
}

export function stopSla20Siren() {
  if (timer != null) {
    clearInterval(timer)
    timer = null
  }
  const ctx = getSharedAudioContext()
  releaseNodes(nodes, ctx ? ctx.currentTime : 0)
  nodes = []
}

export function isSla20Looping() {
  return timer != null
}
