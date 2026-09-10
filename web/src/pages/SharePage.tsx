import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { BriefCard } from '../components/BriefCard'
import { EventStrip } from '../components/EventStrip'
import { Graph } from '../components/Graph'
import { NodeDetail } from '../components/NodeDetail'
import { VerifyPanel } from '../components/VerifyPanel'
import { api, ApiError } from '../lib/api'
import { applyEvent, emptyGraph } from '../lib/graph'
import type { EvidenceGraph, SharePayload } from '../lib/types'

const MAX_GAP_MS = 1400

export function SharePage() {
  const { token = '' } = useParams()
  const [payload, setPayload] = useState<SharePayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [cursor, setCursor] = useState(0) // events applied so far
  const [playing, setPlaying] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    api
      .shareView(token)
      .then(setPayload)
      .catch((e) => setError(e instanceof ApiError ? e.detail : String(e)))
  }, [token])

  const events = payload?.timeline ?? []
  const done = cursor >= events.length

  useEffect(() => {
    if (!payload || !playing || done) return
    const prev = events[cursor - 1]?.at
    const next = events[cursor]?.at
    const gap = prev && next ? Math.min(MAX_GAP_MS, Math.max(60, (next - prev) * 1000)) : 60
    timer.current = window.setTimeout(() => setCursor((c) => c + 1), gap)
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [payload, playing, cursor, done, events])

  const graph = useMemo<EvidenceGraph>(() => {
    if (!payload) return emptyGraph('')
    if (done) return payload.graph
    let g = emptyGraph(payload.question)
    for (let i = 0; i < cursor; i++) g = applyEvent(g, events[i])
    return g
  }, [payload, cursor, done, events])

  if (error) return <div className="ask error">{error}</div>
  if (!payload) return <div className="ask muted">loading replay…</div>
  const selectedNode = graph.nodes.find((n) => n.id === selected) ?? null
  const pct = events.length ? Math.round((cursor / events.length) * 100) : 100
  return (
    <div className="run">
      <div className="canvas">
        <Graph graph={graph} selected={selected} onSelect={setSelected} />
        <div className="overlay">
          <span className="pill">replay</span>
          <span className="small muted">shared read-only · expires {new Date(payload.expires_at).toLocaleDateString()}</span>
        </div>
        {selectedNode && <NodeDetail node={selectedNode} onClose={() => setSelected(null)} />}
      </div>
      <div className="side">
        <div className="card">
          <p className="question">{payload.question}</p>
          <div className="replay-controls">
            <button onClick={() => setPlaying((p) => !p)} disabled={done}>
              {done ? 'Finished' : playing ? 'Pause' : 'Play'}
            </button>
            <button
              onClick={() => {
                setCursor(0)
                setPlaying(true)
              }}
            >
              Restart
            </button>
            <button onClick={() => setCursor(events.length)} disabled={done}>
              Instant
            </button>
            <div className="bar">
              <i style={{ width: `${pct}%` }} />
            </div>
            <span className="small muted">
              {cursor}/{events.length}
            </span>
          </div>
        </div>
        {done && payload.brief && (
          <BriefCard
            brief={payload.brief}
            selectedClaim={selectedNode?.kind === 'claim' ? String(selectedNode.data.claim) : null}
            onSelectClaim={(claim) => {
              const n = graph.nodes.find((x) => x.kind === 'claim' && x.data.claim === claim)
              if (n) setSelected(n.id)
            }}
          />
        )}
        {done && payload.brief && (
          <VerifyPanel loadBundle={() => api.shareBundle(token)} onSelectClaim={(i) => setSelected(`claim:${i}`)} />
        )}
        <EventStrip events={events.slice(0, cursor)} />
      </div>
    </div>
  )
}
