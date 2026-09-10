import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'
import { useEffect, useMemo, useRef, useState } from 'react'
import { contentKey } from '../lib/graph'
import type { EvidenceGraph, GraphNode } from '../lib/types'

type SimNode = SimulationNodeDatum & { id: string; node: GraphNode; born: number }
type SimLink = SimulationLinkDatum<SimNode> & { relation: string }

const RADIUS: Record<string, number> = {
  question: 16,
  goal: 12,
  step: 10,
  finding: 7,
  evidence: 5,
  claim: 9,
  recommendation: 10,
}
const DIST: Record<string, number> = { asks: 60, plans: 80, produces: 55, cites: 35, verifies: 45, supports: 60 }

export function nodeColor(n: GraphNode): string {
  if (n.kind === 'claim') {
    const s = String(n.data.status)
    return s === 'verified' ? 'var(--ok)' : s === 'conflicting' ? 'var(--bad)' : 'var(--warn)'
  }
  if (n.kind === 'step') {
    const s = String(n.data.status)
    if (s === 'failed') return 'var(--bad)'
    if (s === 'skipped') return 'var(--muted)'
  }
  return `var(--k-${n.kind})`
}

export function Graph({
  graph,
  selected,
  onSelect,
}: {
  graph: EvidenceGraph
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 800, h: 600 })
  const simNodes = useRef<Map<string, SimNode>>(new Map())
  const [, force] = useState(0)
  const simRef = useRef<ReturnType<typeof forceSimulation<SimNode>> | null>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Keep positions across graph updates: by id first, then by content key (a
  // live node and its authoritative server twin may carry different ids).
  const links = useMemo(() => {
    const byKey = new Map<string, SimNode>()
    for (const n of simNodes.current.values()) byKey.set(contentKey(n.node), n)
    const next = new Map<string, SimNode>()
    const now = performance.now()
    for (const n of graph.nodes) {
      const prev = simNodes.current.get(n.id) ?? byKey.get(contentKey(n))
      if (prev) next.set(n.id, { ...prev, id: n.id, node: n })
      else {
        const anchor = simNodes.current.get('question')
        next.set(n.id, {
          id: n.id,
          node: n,
          born: now,
          x: (anchor?.x ?? size.w / 2) + (Math.random() - 0.5) * 60,
          y: (anchor?.y ?? size.h / 2) + (Math.random() - 0.5) * 60,
        })
      }
    }
    simNodes.current = next
    const ls: SimLink[] = []
    for (const e of graph.edges) {
      const s = next.get(e.source)
      const t = next.get(e.target)
      if (s && t) ls.push({ source: s, target: t, relation: e.relation })
    }
    return ls
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph])

  useEffect(() => {
    const nodes = [...simNodes.current.values()]
    if (!simRef.current) {
      simRef.current = forceSimulation<SimNode>(nodes).alphaDecay(0.035).on('tick', () => force((x) => x + 1))
    }
    const sim = simRef.current
    sim.nodes(nodes)
    sim
      .force('charge', forceManyBody<SimNode>().strength((d) => -60 - RADIUS[d.node.kind] * 14))
      .force('center', forceCenter(size.w / 2, size.h / 2).strength(0.06))
      .force('collide', forceCollide<SimNode>((d) => RADIUS[d.node.kind] + 14).strength(0.8))
      .force(
        'link',
        forceLink<SimNode, SimLink>(links)
          .id((d) => d.id)
          .distance((l) => DIST[l.relation] ?? 50)
          .strength(0.7),
      )
    sim.alpha(Math.max(sim.alpha(), 0.6)).restart()
  }, [links, size.w, size.h])

  useEffect(
    () => () => {
      simRef.current?.stop()
    },
    [],
  )

  const nodes = [...simNodes.current.values()]
  const now = performance.now()
  return (
    <div ref={ref} style={{ position: 'absolute', inset: 0 }} onClick={() => onSelect(null)}>
      <svg className="graph" viewBox={`0 0 ${size.w} ${size.h}`}>
        <g>
          {links.map((l, i) => {
            const s = l.source as SimNode
            const t = l.target as SimNode
            return <line key={i} className={`edge ${l.relation}`} x1={s.x} y1={s.y} x2={t.x} y2={t.y} />
          })}
        </g>
        <g>
          {nodes.map((n) => {
            const r = RADIUS[n.node.kind] ?? 6
            const running = n.node.kind === 'step' && n.node.data.status === 'running'
            const cls = ['node', n.id === selected ? 'selected' : '', now - n.born < 500 ? 'enter' : '', running ? 'running' : '']
            return (
              <g
                key={n.id}
                className={cls.join(' ')}
                transform={`translate(${n.x ?? 0},${n.y ?? 0})`}
                onClick={(e) => {
                  e.stopPropagation()
                  onSelect(n.id === selected ? null : n.id)
                }}
              >
                <circle r={r} fill={nodeColor(n.node)} fillOpacity={n.node.kind === 'evidence' ? 0.55 : 0.9} />
                {(r >= 9 || n.id === selected) && (
                  <text x={r + 4} y={4}>
                    {n.node.label.length > 42 ? n.node.label.slice(0, 41) + '…' : n.node.label}
                  </text>
                )}
              </g>
            )
          })}
        </g>
      </svg>
      <div className="legend">
        {(['question', 'goal', 'step', 'finding', 'evidence', 'claim', 'recommendation'] as const).map((k) => (
          <span key={k}>
            <i style={{ background: `var(--k-${k})` }} />
            {k}
          </span>
        ))}
      </div>
    </div>
  )
}
