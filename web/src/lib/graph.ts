// Live evidence graph: fold the SSE events into nodes/edges as they arrive,
// with the same ids and relations the server's build_evidence_graph uses, so
// the authoritative /graph payload can replace it without the picture jumping.
import type { EvidenceGraph, GraphEdge, GraphNode, Json, LoggedEvent, NodeKind } from './types'

export const TERMINAL = new Set(['run.done', 'run.failed', 'approval.required'])

export function emptyGraph(question: string, status = 'queued'): EvidenceGraph {
  return {
    nodes: [{ id: 'question', kind: 'question', label: clip(question), data: { question, status } }],
    edges: [],
  }
}

export function clip(text: string, n = 90): string {
  const t = String(text ?? '').replace(/\s+/g, ' ').trim()
  return t.length <= n ? t : t.slice(0, n - 1) + '…'
}

function hash(s: string): string {
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0
  return (h >>> 0).toString(16).padStart(8, '0')
}

/** Content key used to match a live node to its server twin (ids may differ). */
export function contentKey(n: GraphNode): string {
  switch (n.kind) {
    case 'finding':
      return `finding|${n.data.claim}|${n.data.first_ref ?? ''}`
    case 'evidence':
      return `evidence|${n.data.evidence_kind}|${n.data.ref}`
    case 'claim':
      return `claim|${n.data.claim}`
    case 'recommendation':
      return `rec|${n.data.action}`
    default:
      return `${n.kind}|${n.id}`
  }
}

function upsert(g: EvidenceGraph, node: GraphNode): void {
  const i = g.nodes.findIndex((n) => n.id === node.id)
  if (i === -1) g.nodes.push(node)
  else g.nodes[i] = { ...g.nodes[i], ...node, data: { ...g.nodes[i].data, ...node.data } }
}

function link(g: EvidenceGraph, source: string, target: string, relation: string): void {
  if (!g.edges.some((e) => e.source === source && e.target === target && e.relation === relation))
    g.edges.push({ source, target, relation })
}

function setQuestionStatus(g: EvidenceGraph, status: string): void {
  const q = g.nodes.find((n) => n.id === 'question')
  if (q) q.data = { ...q.data, status }
}

/** Fold one event into a COPY of the graph. */
export function applyEvent(prev: EvidenceGraph, ev: LoggedEvent): EvidenceGraph {
  const g: EvidenceGraph = {
    nodes: prev.nodes.map((n) => ({ ...n, data: { ...n.data } })),
    edges: prev.edges.map((e) => ({ ...e })),
  }
  const d = ev.data as Json
  switch (ev.type) {
    case 'run.accepted':
      setQuestionStatus(g, 'planning')
      break
    case 'goal.interpreted':
      upsert(g, { id: 'goal', kind: 'goal', label: clip(String(d.objective ?? '')), data: d })
      link(g, 'question', 'goal', 'asks')
      break
    case 'plan.created':
    case 'plan.reused': {
      const steps = (d.steps as { id: string; kind: string; description: string }[]) ?? []
      for (const s of steps) {
        upsert(g, {
          id: `step:${s.id}`,
          kind: 'step',
          label: clip(s.description),
          data: { step_id: s.id, step_kind: s.kind, status: 'pending', completed: false },
        })
        if (g.nodes.some((n) => n.id === 'goal')) link(g, 'goal', `step:${s.id}`, 'plans')
      }
      setQuestionStatus(g, 'executing')
      break
    }
    case 'step.started':
      upsert(g, {
        id: `step:${d.id}`,
        kind: 'step',
        label: clip(String(d.description ?? d.id)),
        data: { step_id: d.id, step_kind: d.kind, status: 'running' },
      })
      break
    case 'step.completed':
      upsert(g, {
        id: `step:${d.id}`,
        kind: 'step',
        label: clip(String(d.summary ?? d.id)),
        data: { step_id: d.id, status: d.ok ? 'done' : 'failed', completed: true, summary: d.summary },
      })
      break
    case 'step.skipped':
      upsert(g, {
        id: `step:${d.id}`,
        kind: 'step',
        label: clip(String(d.summary ?? d.id)),
        data: { step_id: d.id, status: 'skipped', completed: true },
      })
      break
    case 'finding.partial': {
      const n = g.nodes.filter((x) => x.kind === 'finding').length
      const evidence = (d.evidence as { kind: string; ref: string; snippet?: string }[]) ?? []
      const first = evidence[0]?.ref ?? ''
      const fid = `finding:${n}`
      upsert(g, {
        id: fid,
        kind: 'finding',
        label: clip(String(d.claim ?? '')),
        data: {
          claim: d.claim,
          source: d.source,
          finding_kind: d.kind,
          magnitude: d.magnitude ?? null,
          association_only: d.association_only ?? false,
          step_id: d.step_id,
          first_ref: first,
        },
      })
      if (d.step_id && g.nodes.some((x) => x.id === `step:${d.step_id}`))
        link(g, `step:${d.step_id}`, fid, 'produces')
      for (const e of evidence) {
        const eid = `evidence:${hash(`${e.kind}:${e.ref}`)}`
        upsert(g, {
          id: eid,
          kind: 'evidence',
          label: clip(e.snippet || e.ref, 60),
          data: { evidence_kind: e.kind, ref: e.ref, snippet: e.snippet ?? '' },
        })
        link(g, fid, eid, 'cites')
      }
      break
    }
    case 'reflection':
    case 'replan':
      break
    case 'claim.verified': {
      const n = g.nodes.filter((x) => x.kind === 'claim').length
      const cid = `claim:${n}`
      upsert(g, {
        id: cid,
        kind: 'claim',
        label: clip(String(d.claim ?? '')),
        data: { claim: d.claim, index: n, status: d.status, method: d.method, confidence: d.confidence },
      })
      const target = g.nodes.find((x) => x.kind === 'finding' && x.data.claim === d.claim)
      if (target) link(g, cid, target.id, 'verifies')
      setQuestionStatus(g, 'verifying')
      break
    }
    case 'recommendation': {
      const n = g.nodes.filter((x) => x.kind === 'recommendation').length
      const rid = `rec:${n}`
      upsert(g, { id: rid, kind: 'recommendation', label: clip(String(d.action ?? '')), data: d })
      for (const based of (d.based_on as string[]) ?? []) {
        const c = g.nodes.find((x) => x.kind === 'claim' && x.data.claim === based)
        if (c) link(g, rid, c.id, 'supports')
      }
      break
    }
    case 'approval.required':
      setQuestionStatus(g, 'awaiting_approval')
      break
    case 'run.done':
      setQuestionStatus(g, String(d.status ?? 'done'))
      break
    case 'run.failed':
      setQuestionStatus(g, 'failed')
      break
    default:
      break
  }
  return g
}

export function applyEvents(g: EvidenceGraph, events: LoggedEvent[]): EvidenceGraph {
  return events.reduce(applyEvent, g)
}

export const KIND_ORDER: NodeKind[] = [
  'question',
  'goal',
  'step',
  'finding',
  'evidence',
  'claim',
  'recommendation',
]

export function edgesFor(g: EvidenceGraph, id: string): GraphEdge[] {
  return g.edges.filter((e) => e.source === id || e.target === id)
}
