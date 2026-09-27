import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Approvals } from '../components/Approvals'
import { BriefCard } from '../components/BriefCard'
import { EventStrip } from '../components/EventStrip'
import { Graph } from '../components/Graph'
import { NodeDetail } from '../components/NodeDetail'
import { VerifyPanel } from '../components/VerifyPanel'
import { api, ApiError } from '../lib/api'
import { applyEvent, applyEvents, emptyGraph, TERMINAL } from '../lib/graph'
import { identityHeaders } from '../lib/identity'
import { streamEvents } from '../lib/sse'
import type { ApprovalItem, Brief, EvidenceGraph, LoggedEvent, RunStatus } from '../lib/types'

export function RunPage() {
  const { id = '' } = useParams()
  const [status, setStatus] = useState<RunStatus | null>(null)
  const [graph, setGraph] = useState<EvidenceGraph>(() => emptyGraph(''))
  const [events, setEvents] = useState<LoggedEvent[]>([])
  const [brief, setBrief] = useState<Brief | null>(null)
  const [approvals, setApprovals] = useState<ApprovalItem[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [share, setShare] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [live, setLive] = useState(false)
  const lastSeq = useRef(0)
  const abort = useRef<AbortController | null>(null)

  const refreshStatus = useCallback(async () => {
    const s = await api.run(id)
    setStatus(s)
    return s
  }, [id])

  const finalize = useCallback(async () => {
    const s = await refreshStatus()
    if (s.has_brief) {
      const [rep, g] = await Promise.all([api.report(id), api.graph(id)])
      setBrief(rep.brief)
      setGraph(g) // authoritative graph replaces the live one (positions carry over)
    }
    if (s.pending_approvals > 0) setApprovals(await api.approvals(id))
    else setApprovals([])
  }, [id, refreshStatus])

  const openStream = useCallback(async () => {
    abort.current?.abort()
    const ctl = new AbortController()
    abort.current = ctl
    setLive(true)
    try {
      await streamEvents(`/v1/runs/${id}/events`, {
        headers: identityHeaders(),
        lastEventId: lastSeq.current,
        signal: ctl.signal,
        onEvent: (ev) => {
          if (ev.seq <= lastSeq.current) return
          lastSeq.current = ev.seq
          setEvents((prev) => [...prev, ev])
          setGraph((prev) => applyEvent(prev, ev))
          if (TERMINAL.has(ev.type)) void finalize()
        },
      })
    } catch (e) {
      if (!ctl.signal.aborted) setError(String(e))
    } finally {
      if (abort.current === ctl) setLive(false)
    }
  }, [id, finalize])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [s, tl] = await Promise.all([api.run(id), api.timeline(id)])
        if (cancelled) return
        setStatus(s)
        setEvents(tl.events)
        lastSeq.current = tl.events.length ? tl.events[tl.events.length - 1].seq : 0
        setGraph(applyEvents(emptyGraph(s.question, s.status), tl.events))
        if (s.running) await openStream()
        else await finalize()
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.detail : String(e))
      }
    })()
    return () => {
      cancelled = true
      abort.current?.abort()
    }
  }, [id, openStream, finalize])

  const onDecided = async () => {
    setApprovals([])
    await openStream() // the executor resumed; tail from where we were
  }

  const makeShare = async () => {
    const s = await api.share(id)
    setShare(s.url)
    try {
      await navigator.clipboard.writeText(s.url)
    } catch {
      /* clipboard blocked */
    }
  }

  const selectedNode = graph.nodes.find((n) => n.id === selected) ?? null
  const selectClaimByText = (claim: string) => {
    const n = graph.nodes.find((x) => x.kind === 'claim' && x.data.claim === claim)
    if (n) setSelected(n.id)
  }
  const st = status?.status ?? graph.nodes[0]?.data.status ?? 'queued'

  return (
    <div className="run">
      <div className="canvas">
        <Graph graph={graph} selected={selected} onSelect={setSelected} />
        <div className="overlay">
          <span className={`pill ${String(st)}`}>{String(st).replace('_', ' ')}</span>
          {live && <span className="pill running">live</span>}
          {error && <span className="error small">{error}</span>}
        </div>
        {selectedNode && <NodeDetail node={selectedNode} onClose={() => setSelected(null)} />}
      </div>
      <div className="side">
        <div className="card">
          <p className="question">{status?.question ?? '…'}</p>
          <div className="stats">
            <div className="stat">
              <b>{status?.findings ?? 0}</b>
              <span>findings</span>
            </div>
            <div className="stat">
              <b style={{ color: 'var(--ok)' }}>{status?.claims.verified ?? 0}</b>
              <span>verified</span>
            </div>
            <div className="stat">
              <b style={{ color: 'var(--bad)' }}>{status?.claims.conflicting ?? 0}</b>
              <span>conflicting</span>
            </div>
            <div className="stat">
              <b>${status ? status.cost_usd.toFixed(4) : '0'}</b>
              <span>cost</span>
            </div>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button onClick={makeShare} disabled={!status?.has_brief && st !== 'done' && st !== 'failed'}>
              Share replay link
            </button>
            {share && (
              <div className="sharebox" style={{ flex: 1 }}>
                <input readOnly value={share} onFocus={(e) => e.currentTarget.select()} />
              </div>
            )}
          </div>
        </div>
        <Approvals runId={id} items={approvals} onDecided={onDecided} />
        {brief && <BriefCard brief={brief} selectedClaim={selectedNode?.kind === 'claim' ? String(selectedNode.data.claim) : null} onSelectClaim={selectClaimByText} />}
        {status?.has_brief && (
          <VerifyPanel loadBundle={() => api.bundle(id)} onSelectClaim={(i) => setSelected(`claim:${i}`)} />
        )}
        <EventStrip events={events} />
      </div>
    </div>
  )
}
