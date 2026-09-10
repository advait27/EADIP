import { useEffect, useRef } from 'react'
import type { LoggedEvent } from '../lib/types'

function summary(e: LoggedEvent): string {
  const d = e.data
  switch (e.type) {
    case 'goal.interpreted':
      return `${String(d.complexity)} · metrics ${JSON.stringify(d.metrics)} · entities ${JSON.stringify(d.entities)}`
    case 'plan.created':
    case 'plan.reused':
      return `${(d.steps as unknown[])?.length ?? 0} steps · ${String(d.rationale ?? '')}`
    case 'step.started':
    case 'step.completed':
    case 'step.skipped':
      return `${String(d.id)} ${d.summary ? '· ' + String(d.summary) : ''}`
    case 'finding.partial':
      return String(d.claim ?? '')
    case 'claim.verified':
      return `${String(d.status)} · ${String(d.claim ?? '')}`
    case 'verification.summary':
      return `${String(d.verified)} verified · ${String(d.unverified)} unverified · ${String(d.conflicting)} conflicting`
    case 'recommendation':
      return String(d.action ?? '')
    case 'reflection':
      return d.sufficient ? 'evidence sufficient' : `gaps: ${JSON.stringify(d.gaps)}`
    case 'approval.required':
      return String((d.action as { summary?: string })?.summary ?? '')
    case 'run.done':
      return `${String(d.status)} · ${String(d.findings)} findings · $${String(d.cost_usd)} · ${String(d.elapsed_s)}s`
    case 'run.failed':
      return String(d.error ?? '')
    case 'bound.stop':
      return String(d.reason ?? '')
    default:
      return ''
  }
}

export function EventStrip({ events }: { events: LoggedEvent[] }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [events.length])
  return (
    <div className="card">
      <h3>Events · {events.length}</h3>
      <div className="events" ref={ref}>
        {events.map((e) => (
          <div key={`${e.seq}-${e.type}`}>
            <span className="seq">{e.seq || ''}</span>
            <span className="type">{e.type}</span>
            <span className="summary" title={summary(e)}>
              {summary(e)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
