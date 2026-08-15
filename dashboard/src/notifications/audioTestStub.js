/**
 * Recording Web Audio stub for notification-sound tests.
 *
 * Captures enough of what a sound actually built — waveforms, frequencies,
 * node counts, whether it used noise buffers — to assert that two sounds are
 * materially different rather than merely differently named.
 *
 * Not a `.test.js` file on purpose: it is a helper, not a suite.
 */

import { vi } from 'vitest'

/**
 * The shared AudioContext is cached for the lifetime of the module, so the
 * first stub installed in a test file is the only one that is ever wired up.
 * Installing again returns that same controller rather than a dead one whose
 * recorder nothing writes to.
 */
let installed = null

export function installRecordingAudioStub() {
  if (installed) {
    installed.reset()
    return installed
  }

  const record = {
    oscillatorTypes: [],
    frequencies: [],
    oscillators: 0,
    gains: 0,
    filters: 0,
    filterTypes: [],
    bufferSources: 0,
    starts: [],
  }

  const param = (onSet) => ({
    setValueAtTime: vi.fn((value) => onSet?.(value)),
    linearRampToValueAtTime: vi.fn((value) => onSet?.(value)),
    exponentialRampToValueAtTime: vi.fn((value) => onSet?.(value)),
    cancelScheduledValues: vi.fn(),
    value: 0,
  })

  const makeOscillator = () => {
    record.oscillators += 1
    const osc = {
      _type: 'sine',
      frequency: param((value) => record.frequencies.push(Math.round(value))),
      detune: param(),
      connect: vi.fn(),
      disconnect: vi.fn(),
      start: vi.fn((at) => record.starts.push(at)),
      stop: vi.fn(),
    }
    Object.defineProperty(osc, 'type', {
      get: () => osc._type,
      set: (value) => {
        osc._type = value
        record.oscillatorTypes.push(value)
      },
    })
    return osc
  }

  const makeGain = () => {
    record.gains += 1
    return {
      gain: param(),
      connect: vi.fn(),
      disconnect: vi.fn(),
      context: { currentTime: 0 },
    }
  }

  const makeFilter = () => {
    record.filters += 1
    const filter = {
      _type: 'lowpass',
      frequency: param(),
      Q: param(),
      connect: vi.fn(),
      disconnect: vi.fn(),
    }
    Object.defineProperty(filter, 'type', {
      get: () => filter._type,
      set: (value) => {
        filter._type = value
        record.filterTypes.push(value)
      },
    })
    return filter
  }

  const makeBufferSource = () => {
    record.bufferSources += 1
    return {
      buffer: null,
      loop: false,
      connect: vi.fn(),
      disconnect: vi.fn(),
      start: vi.fn((at) => record.starts.push(at)),
      stop: vi.fn(),
    }
  }

  window.AudioContext = vi.fn(() => ({
    state: 'running',
    currentTime: 0,
    sampleRate: 48000,
    destination: {},
    resume: () => Promise.resolve(),
    createGain: makeGain,
    createOscillator: makeOscillator,
    createBiquadFilter: makeFilter,
    createBufferSource: makeBufferSource,
    createBuffer: (channels, length) => ({
      getChannelData: () => new Float32Array(length),
    }),
  }))

  installed = {
    record,
    reset() {
      record.oscillatorTypes.length = 0
      record.frequencies.length = 0
      record.filterTypes.length = 0
      record.starts.length = 0
      record.oscillators = 0
      record.gains = 0
      record.filters = 0
      record.bufferSources = 0
    },
    /** A comparable fingerprint of whatever was just rendered. */
    signature() {
      return JSON.stringify({
        waveforms: [...new Set(record.oscillatorTypes)].sort(),
        oscillators: record.oscillators,
        filters: [...new Set(record.filterTypes)].sort(),
        noise: record.bufferSources > 0,
        // Rounded so a jitter of a hertz does not read as a different sound,
        // while a genuinely different pitch set does.
        pitches: [...new Set(record.frequencies)].sort((a, b) => a - b),
      })
    },
  }
  return installed
}
