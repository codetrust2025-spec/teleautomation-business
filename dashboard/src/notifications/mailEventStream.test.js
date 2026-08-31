import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  __resetMailEventStream,
  getMailAlertTrace,
  subscribeMailEvents,
} from './mailEventStream.js'

class FakeSocket {
  static OPEN = 1
  static latest = null

  constructor() {
    this.readyState = FakeSocket.OPEN
    FakeSocket.latest = this
    queueMicrotask(() => this.onopen?.())
  }

  send() {}
  close() {}
}

class FakeChannel {
  static latest = null

  constructor() { FakeChannel.latest = this }
  postMessage() {}
  close() {}
}

const response = body => Promise.resolve({ ok: true, json: () => Promise.resolve(body) })

describe('mail event stream correlation', () => {
  beforeEach(() => {
    __resetMailEventStream()
    localStorage.clear()
    vi.stubGlobal('WebSocket', FakeSocket)
    vi.stubGlobal('BroadcastChannel', FakeChannel)
    vi.stubGlobal('fetch', vi.fn(() => response({ enabled: true })))
  })

  afterEach(() => {
    __resetMailEventStream()
    FakeSocket.latest = null
    FakeChannel.latest = null
    vi.unstubAllGlobals()
  })

  it('deduplicates the same event across WebSocket and cross-tab delivery', async () => {
    const received = []
    const unsubscribe = subscribeMailEvents((payload, meta) => received.push([payload, meta]))
    await vi.waitFor(() => expect(FakeSocket.latest).not.toBeNull())
    const event = {
      event: 'notification_created', event_id: 'event-1',
      notification_id: 'notification-1', classification: 'offer_received',
    }

    FakeSocket.latest.onmessage({ data: JSON.stringify(event) })
    FakeChannel.latest.onmessage({ data: event })

    expect(received).toHaveLength(1)
    expect(received[0][1]).toEqual({ fromSocket: true })
    expect(getMailAlertTrace()).toEqual([
      expect.objectContaining({
        stage: 'browser_received', event_id: 'event-1',
        notification_id: 'notification-1', transport: 'websocket',
      }),
    ])
    unsubscribe()
  })
})
