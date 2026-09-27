import { describe, expect, it } from 'vitest'
import { applyEvents, contentKey, emptyGraph } from './graph'
import type { LoggedEvent } from './types'

const ev = (seq: number, type: string, data: Record<string, unknown> = {}): LoggedEvent => ({
  seq,
  at: seq,
  type,
  data,
})

const SQL = "SELECT 1 FROM finance_metrics WHERE tenant_id = 't'"

const script: LoggedEvent[] = [
  ev(1, 'run.accepted', { question: 'why?' }),
  ev(2, 'goal.interpreted', { objective: 'why margin fell', metrics: ['margin'] }),
  ev(3, 'plan.created', {
    steps: [
      { id: 'retrieve-context', kind: 'retrieve', description: 'get docs' },
      { id: 'analyze-metric', kind: 'analyze', description: 'numbers' },
    ],
  }),
  ev(4, 'step.started', { id: 'retrieve-context', kind: 'retrieve', description: 'get docs' }),
  ev(5, 'step.completed', { id: 'retrieve-context', ok: true, summary: 'retrieved 2' }),
  ev(6, 'finding.partial', {
    claim: 'memo says cogs spiked',
    source: 'retrieval',
    kind: 'passage',
    step_id: 'retrieve-context',
    evidence: [{ kind: 'passage', ref: 'doc://memo', snippet: 'cogs spiked' }],
  }),
  ev(7, 'finding.partial', {
    claim: 'margin fell by 230',
    source: 'analytics',
    kind: 'headline',
    magnitude: -230,
    step_id: 'analyze-metric',
    evidence: [{ kind: 'query', ref: SQL }],
  }),
  ev(8, 'finding.partial', {
    claim: 'hardware -220',
    source: 'analytics',
    kind: 'driver',
    step_id: 'analyze-metric',
    evidence: [{ kind: 'query', ref: SQL }],
  }),
  ev(9, 'claim.verified', { claim: 'margin fell by 230', status: 'verified', confidence: 0.9, method: 'recompute' }),
  ev(10, 'recommendation', { action: 'fix hardware', based_on: ['margin fell by 230'] }),
  ev(11, 'run.done', { status: 'done' }),
]

describe('live graph reducer', () => {
  it('builds every kind and relation from the event stream', () => {
    const g = applyEvents(emptyGraph('why?'), script)
    expect(new Set(g.nodes.map((n) => n.kind))).toEqual(
      new Set(['question', 'goal', 'step', 'finding', 'evidence', 'claim', 'recommendation']),
    )
    expect(new Set(g.edges.map((e) => e.relation))).toEqual(
      new Set(['asks', 'plans', 'produces', 'cites', 'verifies', 'supports']),
    )
    const ids = new Set(g.nodes.map((n) => n.id))
    for (const e of g.edges) {
      expect(ids.has(e.source)).toBe(true)
      expect(ids.has(e.target)).toBe(true)
    }
  })

  it('shares one evidence node between findings citing the same SQL', () => {
    const g = applyEvents(emptyGraph('why?'), script)
    expect(g.nodes.filter((n) => n.kind === 'evidence')).toHaveLength(2)
    expect(g.edges.filter((e) => e.relation === 'cites')).toHaveLength(3)
  })

  it('tracks step status and the question status', () => {
    const g = applyEvents(emptyGraph('why?'), script)
    expect(g.nodes.find((n) => n.id === 'step:retrieve-context')?.data.status).toBe('done')
    expect(g.nodes.find((n) => n.id === 'step:analyze-metric')?.data.status).toBe('pending')
    expect(g.nodes.find((n) => n.id === 'question')?.data.status).toBe('done')
    const paused = applyEvents(emptyGraph('q'), [script[0], ev(2, 'approval.required', {})])
    expect(paused.nodes[0].data.status).toBe('awaiting_approval')
  })

  it('does not mutate the previous graph', () => {
    const g0 = emptyGraph('why?')
    const g1 = applyEvents(g0, script.slice(0, 3))
    expect(g0.nodes).toHaveLength(1)
    expect(g1.nodes.length).toBeGreaterThan(1)
  })

  it('content keys let server and live nodes be matched despite different ids', () => {
    const g = applyEvents(emptyGraph('why?'), script)
    const finding = g.nodes.find((n) => n.kind === 'finding' && n.data.claim === 'margin fell by 230')!
    expect(contentKey(finding)).toBe(`finding|margin fell by 230|${SQL}`)
    const claim = g.nodes.find((n) => n.kind === 'claim')!
    expect(contentKey(claim)).toBe('claim|margin fell by 230')
  })
})
