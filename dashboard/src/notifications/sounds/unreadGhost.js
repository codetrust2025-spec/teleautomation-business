/**
 * 2. INBOX UNREAD > 3 — looping haunted ambience.
 *
 * Identity retained from the original implementation, which was already unique:
 * a continuous brown-noise wind bed with randomly scheduled moans, whispers,
 * wails and creaks over it. It is the only *ambience* in the app — everything
 * else is a discrete event sound — and the only one built from noise buffers
 * rather than tuned oscillators.
 *
 * Moved here unchanged from utils/notificationSound.js so that every sound
 * identity lives in one place. The scheduling probabilities, gains and
 * frequencies are exactly as they were.
 */

import { getSharedAudioContext, releaseNodes } from '../soundEngine.js'

let active = false
let timer = null
let master = null
let running = []

function track(node) {
  running.push(node)
  return node
}

/** Looping haunted wind (brown noise + swaying low-pass). */
function startGhostWind(ctx, dest) {
  const t = ctx.currentTime
  const len = Math.floor(ctx.sampleRate * 4)
  const buffer = ctx.createBuffer(1, len, ctx.sampleRate)
  const data = buffer.getChannelData(0)
  let brown = 0
  for (let i = 0; i < len; i += 1) {
    brown = brown * 0.985 + (Math.random() * 2 - 1) * 0.08
    data[i] = brown
  }
  const src = track(ctx.createBufferSource())
  src.buffer = buffer
  src.loop = true
  const filter = track(ctx.createBiquadFilter())
  filter.type = 'lowpass'
  filter.frequency.setValueAtTime(320, t)
  filter.Q.setValueAtTime(1.2, t)
  const windGain = track(ctx.createGain())
  windGain.gain.setValueAtTime(0.14, t)
  const lfo = track(ctx.createOscillator())
  lfo.type = 'sine'
  lfo.frequency.setValueAtTime(0.12 + Math.random() * 0.08, t)
  const lfoAmp = track(ctx.createGain())
  lfoAmp.gain.setValueAtTime(280, t)
  lfo.connect(lfoAmp)
  lfoAmp.connect(filter.frequency)
  src.connect(filter)
  filter.connect(windGain)
  windGain.connect(dest)
  src.start(t)
  lfo.start(t)
}

/** Low spirit moan — pitch sinks like a ghost passing by. */
function playGhostMoan(ctx, dest, start) {
  const dur = 2.2 + Math.random() * 1.4
  const osc = track(ctx.createOscillator())
  osc.type = 'sine'
  const f0 = 220 + Math.random() * 140
  osc.frequency.setValueAtTime(f0, start)
  osc.frequency.exponentialRampToValueAtTime(Math.max(55, f0 * 0.28), start + dur)
  const osc2 = track(ctx.createOscillator())
  osc2.type = 'triangle'
  osc2.frequency.setValueAtTime(f0 * 1.015, start)
  osc2.frequency.exponentialRampToValueAtTime(Math.max(60, f0 * 0.3), start + dur)
  const filter = track(ctx.createBiquadFilter())
  filter.type = 'bandpass'
  filter.frequency.setValueAtTime(450, start)
  filter.Q.setValueAtTime(6, start)
  const g = track(ctx.createGain())
  g.gain.setValueAtTime(0.001, start)
  g.gain.linearRampToValueAtTime(0.26, start + 0.55)
  g.gain.linearRampToValueAtTime(0.18, start + dur * 0.65)
  g.gain.exponentialRampToValueAtTime(0.001, start + dur)
  osc.connect(filter)
  osc2.connect(filter)
  filter.connect(g)
  g.connect(dest)
  osc.start(start)
  osc2.start(start)
  osc.stop(start + dur + 0.05)
  osc2.stop(start + dur + 0.05)
}

/** Ethereal whisper — airy noise through a high filter. */
function playGhostWhisper(ctx, dest, start) {
  const len = Math.floor(ctx.sampleRate * (0.5 + Math.random() * 0.5))
  const buffer = ctx.createBuffer(1, len, ctx.sampleRate)
  const data = buffer.getChannelData(0)
  for (let i = 0; i < len; i += 1) {
    data[i] = (Math.random() * 2 - 1) * Math.sin((Math.PI * i) / len)
  }
  const src = track(ctx.createBufferSource())
  src.buffer = buffer
  const filter = track(ctx.createBiquadFilter())
  filter.type = 'bandpass'
  filter.frequency.setValueAtTime(1400 + Math.random() * 900, start)
  filter.Q.setValueAtTime(4, start)
  const g = track(ctx.createGain())
  g.gain.setValueAtTime(0.001, start)
  g.gain.linearRampToValueAtTime(0.1, start + 0.15)
  g.gain.exponentialRampToValueAtTime(0.001, start + len / ctx.sampleRate)
  src.connect(filter)
  filter.connect(g)
  g.connect(dest)
  src.start(start)
  src.stop(start + len / ctx.sampleRate + 0.02)
}

/** Distant spirit wail — thin detuned tones with slow tremolo. */
function playSpiritWail(ctx, dest, start) {
  const dur = 1.6 + Math.random() * 0.8
  const base = 520 + Math.random() * 280
  for (const det of [0, 7, -5]) {
    const osc = track(ctx.createOscillator())
    osc.type = 'sine'
    osc.frequency.setValueAtTime(base + det, start)
    const trem = track(ctx.createOscillator())
    trem.frequency.setValueAtTime(5 + Math.random() * 3, start)
    const tremG = track(ctx.createGain())
    tremG.gain.setValueAtTime(12, start)
    trem.connect(tremG)
    tremG.connect(osc.detune)
    const g = track(ctx.createGain())
    g.gain.setValueAtTime(0.001, start)
    g.gain.linearRampToValueAtTime(0.08, start + 0.25)
    g.gain.exponentialRampToValueAtTime(0.001, start + dur)
    osc.connect(g)
    g.connect(dest)
    osc.start(start)
    trem.start(start)
    osc.stop(start + dur + 0.05)
    trem.stop(start + dur + 0.05)
  }
}

/** Rare hollow knock / creak from the void. */
function playVoidCreak(ctx, dest, start) {
  const osc = track(ctx.createOscillator())
  osc.type = 'triangle'
  osc.frequency.setValueAtTime(180, start)
  osc.frequency.exponentialRampToValueAtTime(45, start + 0.35)
  const g = track(ctx.createGain())
  g.gain.setValueAtTime(0.16, start)
  g.gain.exponentialRampToValueAtTime(0.001, start + 0.4)
  osc.connect(g)
  g.connect(dest)
  osc.start(start)
  osc.stop(start + 0.45)
}

function scheduleAmbience(ctx) {
  if (!active || !master) return

  const now = ctx.currentTime
  const roll = Math.random()

  if (roll < 0.38) {
    playGhostMoan(ctx, master, now)
  } else if (roll < 0.62) {
    playGhostWhisper(ctx, master, now)
  } else if (roll < 0.82) {
    playSpiritWail(ctx, master, now)
  } else {
    playVoidCreak(ctx, master, now)
  }

  const delay = 480 + Math.random() * 900
  timer = window.setTimeout(() => scheduleAmbience(ctx), delay)
}

export function stopUnreadGhost() {
  active = false
  if (timer != null) {
    clearTimeout(timer)
    timer = null
  }
  const now = master?.context?.currentTime ?? 0
  releaseNodes(running, now)
  running = []
  if (master) {
    try {
      master.gain.exponentialRampToValueAtTime(0.001, now + 0.25)
      const m = master
      setTimeout(() => {
        try {
          m.disconnect()
        } catch {
          /* ignore */
        }
      }, 300)
    } catch {
      /* ignore */
    }
    master = null
  }
}

export function startUnreadGhost() {
  if (active) return true
  const ctx = getSharedAudioContext()
  if (!ctx) return false

  const begin = () => {
    stopUnreadGhost()
    active = true
    master = track(ctx.createGain())
    master.gain.setValueAtTime(0.42, ctx.currentTime)
    master.connect(ctx.destination)
    startGhostWind(ctx, master)
    scheduleAmbience(ctx)
  }

  if (ctx.state === 'suspended') {
    ctx.resume().then(begin).catch(() => {})
  } else {
    begin()
  }
  return true
}

export function isUnreadGhostActive() {
  return active
}

/** One short burst for the preview harness — the loop is stopped after 6s. */
export function playUnreadGhostPreview(durationMs = 6000) {
  startUnreadGhost()
  window.setTimeout(() => stopUnreadGhost(), durationMs)
  return true
}
