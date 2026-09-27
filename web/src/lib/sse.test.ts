import { describe, expect, it } from 'vitest'
import { parseSse, toLoggedEvent } from './sse'

describe('parseSse', () => {
  it('parses CRLF-framed messages with id/event/data and keeps the remainder', () => {
    const body = 'id: 1\r\nevent: run.accepted\r\ndata: {"a": 1}\r\n\r\nid: 2\r\nevent: step.started\r\ndata: {"x":'
    const { messages, rest } = parseSse(body)
    expect(messages).toEqual([{ id: '1', event: 'run.accepted', data: '{"a": 1}' }])
    expect(rest).toBe('id: 2\nevent: step.started\ndata: {"x":')
  })

  it('joins multi-line data, ignores comments and empty blocks', () => {
    const { messages } = parseSse(': ping\n\nevent: e\ndata: l1\ndata: l2\n\n\n\n')
    expect(messages).toEqual([{ event: 'e', data: 'l1\nl2' }])
  })

  it('converts to LoggedEvent with numeric seq and parsed JSON', () => {
    const ev = toLoggedEvent({ id: '7', event: 'run.done', data: '{"status":"done"}' }, 123)
    expect(ev).toEqual({ seq: 7, at: 123, type: 'run.done', data: { status: 'done' } })
    expect(toLoggedEvent({ data: 'x' })).toBeNull()
    expect(toLoggedEvent({ event: 'e', data: 'not json' })?.data).toEqual({ raw: 'not json' })
  })
})
