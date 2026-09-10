// A fetch-based Server-Sent Events reader. `EventSource` cannot send the dev
// identity headers or `Last-Event-ID` on demand, so we read the stream by hand.
import type { LoggedEvent } from './types'

export type SseMessage = { id?: string; event?: string; data: string }

/** Parse complete SSE messages out of `buffer`; return them and the unparsed rest. */
export function parseSse(buffer: string): { messages: SseMessage[]; rest: string } {
  const text = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const messages: SseMessage[] = []
  let start = 0
  for (;;) {
    const end = text.indexOf('\n\n', start)
    if (end === -1) break
    const block = text.slice(start, end)
    start = end + 2
    const msg: SseMessage = { data: '' }
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue
      const colon = line.indexOf(':')
      const field = colon === -1 ? line : line.slice(0, colon)
      let value = colon === -1 ? '' : line.slice(colon + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'id') msg.id = value
      else if (field === 'event') msg.event = value
      else if (field === 'data') dataLines.push(value)
    }
    msg.data = dataLines.join('\n')
    if (msg.event || msg.data) messages.push(msg)
  }
  return { messages, rest: text.slice(start) }
}

export function toLoggedEvent(msg: SseMessage, at: number = Date.now() / 1000): LoggedEvent | null {
  if (!msg.event) return null
  let data: Record<string, unknown> = {}
  if (msg.data) {
    try {
      data = JSON.parse(msg.data) as Record<string, unknown>
    } catch {
      data = { raw: msg.data }
    }
  }
  return { seq: msg.id ? Number(msg.id) : 0, at, type: msg.event, data }
}

export type StreamOptions = {
  headers?: Record<string, string>
  lastEventId?: number
  signal?: AbortSignal
  onEvent: (event: LoggedEvent) => void
}

/** Read `url` as SSE until the server closes it. Resolves when the stream ends. */
export async function streamEvents(url: string, opts: StreamOptions): Promise<void> {
  const headers: Record<string, string> = { accept: 'text/event-stream', ...(opts.headers ?? {}) }
  if (opts.lastEventId) headers['Last-Event-ID'] = String(opts.lastEventId)
  const r = await fetch(url, { headers, signal: opts.signal })
  if (!r.ok || !r.body) throw new Error(`stream failed: ${r.status}`)
  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const { messages, rest } = parseSse(buffer)
    buffer = rest
    for (const m of messages) {
      const ev = toLoggedEvent(m)
      if (ev) opts.onEvent(ev)
    }
  }
}
