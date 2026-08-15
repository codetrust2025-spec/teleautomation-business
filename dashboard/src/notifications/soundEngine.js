/**
 * Generic Web Audio primitives shared by every notification sound.
 *
 * Deliberately contains no notification *identity*. Anything here is the
 * equivalent of "create an oscillator" or "open the context" — the recognisable
 * shape of a sound always lives in its own file under ./sounds, so two
 * notifications can never drift into being variants of one another.
 */

import { getSharedAudioContext } from '../utils/notificationSound.js'

export { getSharedAudioContext }

/**
 * Run `build(ctx, t0)` once the shared context is awake.
 *
 * Every sound goes through here so the suspended-context dance exists in one
 * place. Returns false when there is no audio context at all (SSR, jsdom
 * without a stub, browsers with Web Audio disabled).
 */
export function withAudioContext(build) {
  const ctx = getSharedAudioContext()
  if (!ctx) return false

  const run = () => {
    try {
      build(ctx, ctx.currentTime)
    } catch {
      /* audio is best-effort and must never break a render */
    }
  }

  if (ctx.state === 'suspended') {
    ctx.resume().then(run).catch(() => {})
  } else {
    run()
  }
  return true
}

/** A master gain wired to the speakers, so a sound can fade itself out. */
export function masterGain(ctx, level, at) {
  const master = ctx.createGain()
  master.gain.setValueAtTime(level, at)
  master.connect(ctx.destination)
  return master
}

/**
 * One oscillator with a percussive attack/decay envelope.
 *
 * `shape` chooses the waveform; `peak` the loudness. Sounds differ by how they
 * *use* this, not by owning a private copy of it.
 */
export function pluck(ctx, dest, { type = 'sine', freq, start, duration, peak = 0.8, endFreq = null }) {
  const osc = ctx.createOscillator()
  osc.type = type
  osc.frequency.setValueAtTime(freq, start)
  if (endFreq != null) {
    osc.frequency.exponentialRampToValueAtTime(Math.max(1, endFreq), start + duration)
  }

  const g = ctx.createGain()
  g.gain.setValueAtTime(0.0001, start)
  g.gain.exponentialRampToValueAtTime(peak, start + 0.012)
  g.gain.exponentialRampToValueAtTime(0.0001, start + duration)

  osc.connect(g)
  g.connect(dest)
  osc.start(start)
  osc.stop(start + duration + 0.03)
  return osc
}

/** A sustained tone with explicit attack and release — for chords and sirens. */
export function sustain(ctx, dest, { type = 'sine', freq, start, duration, peak = 0.6, attack = 0.05 }) {
  const osc = ctx.createOscillator()
  osc.type = type
  osc.frequency.setValueAtTime(freq, start)

  const g = ctx.createGain()
  g.gain.setValueAtTime(0.0001, start)
  g.gain.linearRampToValueAtTime(peak, start + attack)
  g.gain.setValueAtTime(peak, start + Math.max(attack, duration - 0.08))
  g.gain.exponentialRampToValueAtTime(0.0001, start + duration)

  osc.connect(g)
  g.connect(dest)
  osc.start(start)
  osc.stop(start + duration + 0.03)
  return osc
}

/** A short filtered noise burst — transients, ticks and wooden attacks. */
export function noiseBurst(ctx, dest, { start, duration, peak = 0.3, filterType = 'bandpass', frequency = 1200, Q = 1 }) {
  const length = Math.max(1, Math.floor(ctx.sampleRate * duration))
  const buffer = ctx.createBuffer(1, length, ctx.sampleRate)
  const data = buffer.getChannelData(0)
  for (let i = 0; i < length; i += 1) {
    // Fade across the burst so it reads as a transient, not a click.
    data[i] = (Math.random() * 2 - 1) * (1 - i / length)
  }

  const src = ctx.createBufferSource()
  src.buffer = buffer

  const filter = ctx.createBiquadFilter()
  filter.type = filterType
  filter.frequency.setValueAtTime(frequency, start)
  filter.Q.setValueAtTime(Q, start)

  const g = ctx.createGain()
  g.gain.setValueAtTime(peak, start)
  g.gain.exponentialRampToValueAtTime(0.0001, start + duration)

  src.connect(filter)
  filter.connect(g)
  g.connect(dest)
  src.start(start)
  src.stop(start + duration + 0.02)
  return src
}

/** Stop and disconnect nodes collected by a looping sound. */
export function releaseNodes(nodes, at = 0) {
  for (const node of nodes) {
    try {
      if (node.stop) node.stop(at + 0.02)
    } catch {
      /* already stopped */
    }
    try {
      node.disconnect()
    } catch {
      /* already disconnected */
    }
  }
}
