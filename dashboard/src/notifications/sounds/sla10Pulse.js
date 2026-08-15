/**
 * 5. REPLY SLA — 10 MINUTES — electronic double-pulse (looping).
 *
 * Identity: two flat, identical square blips 90 ms apart, then silence. No
 * glide, no chord, no sweep — the information is entirely in the *rhythm*.
 * A listener recognises it by counting two, which is why the 20-minute alert
 * was deliberately given a continuous glide instead of more pulses.
 *
 * This file shares no sound-generating code with sla20Siren.js. The previous
 * implementation passed an `aggressive` boolean into one function, which made
 * the two tiers sound like the same alert played harder.
 */

import { masterGain, pluck, withAudioContext, getSharedAudioContext } from '../soundEngine.js'

const PITCH = 1174.66 // D6
const GAP = 0.09
const LOOP_MS = 3000

let timer = null

function doublePulse(ctx, t0) {
  const master = masterGain(ctx, 0.45, t0)

  // Square at a fixed pitch: deliberately un-musical and machine-like.
  pluck(ctx, master, { type: 'square', freq: PITCH, start: t0, duration: 0.055, peak: 0.8 })
  pluck(ctx, master, { type: 'square', freq: PITCH, start: t0 + GAP, duration: 0.055, peak: 0.8 })

  master.gain.setValueAtTime(0.45, t0 + GAP + 0.06)
  master.gain.exponentialRampToValueAtTime(0.0001, t0 + GAP + 0.12)
}

/** One double-pulse, no loop — used by the preview harness and by tests. */
export function playSla10Pulse() {
  return withAudioContext((ctx, t0) => doublePulse(ctx, t0))
}

export function startSla10Pulse(intervalMs = LOOP_MS) {
  if (timer != null) return true
  const started = withAudioContext((ctx, t0) => doublePulse(ctx, t0))
  if (!started) return false
  timer = window.setInterval(() => {
    const ctx = getSharedAudioContext()
    if (ctx && timer != null) doublePulse(ctx, ctx.currentTime)
  }, intervalMs)
  return true
}

export function stopSla10Pulse() {
  if (timer != null) {
    clearInterval(timer)
    timer = null
  }
}

export function isSla10Looping() {
  return timer != null
}
