import type { GraphNode } from '../lib/types'

export function NodeDetail({ node, onClose }: { node: GraphNode; onClose: () => void }) {
  const d = node.data
  const sql = node.kind === 'evidence' && d.evidence_kind === 'query' ? String(d.ref) : null
  const provenance = Array.isArray(d.provenance) ? (d.provenance as string[]) : []
  return (
    <div className="card detail" onClick={(e) => e.stopPropagation()}>
      <button className="close" onClick={onClose}>
        ×
      </button>
      <h3>{node.kind}</h3>
      <h4>{node.label}</h4>
      {node.kind === 'claim' && (
        <div className="row" style={{ marginBottom: 6 }}>
          <span className={`pill ${String(d.status)}`}>{String(d.status)}</span>
          <span className="muted small">
            {String(d.method)} · confidence {Math.round(Number(d.confidence) * 100)}%
          </span>
        </div>
      )}
      {node.kind === 'claim' && d.claimed_magnitude !== undefined && d.claimed_magnitude !== null && (
        <div className="small">
          claimed <code>{fmt(d.claimed_magnitude)}</code>
          {d.recomputed_magnitude !== null && d.recomputed_magnitude !== undefined && (
            <>
              {' '}· server re-derived <code>{fmt(d.recomputed_magnitude)}</code>
            </>
          )}
        </div>
      )}
      {node.kind === 'step' && (
        <div className="small muted">
          {String(d.step_kind ?? '')} · {String(d.status ?? '')}
          {d.summary ? ` · ${String(d.summary)}` : ''}
        </div>
      )}
      {node.kind === 'finding' && (
        <div className="small muted">
          {String(d.source)} · {String(d.finding_kind)}
          {d.magnitude !== null && d.magnitude !== undefined ? ` · magnitude ${fmt(d.magnitude)}` : ''}
          {d.association_only ? ' · association, not causation' : ''}
        </div>
      )}
      {node.kind === 'recommendation' && (
        <div className="small muted">
          {String(d.rationale ?? '')} · impact {fmt(d.impact)} · confidence {Math.round(Number(d.confidence) * 100)}%
        </div>
      )}
      {node.kind === 'evidence' && !sql && (
        <div className="small">
          <div className="muted">{String(d.evidence_kind)}</div>
          <code>{String(d.ref)}</code>
          {d.snippet ? <p className="small">“{String(d.snippet)}”</p> : null}
        </div>
      )}
      {sql && <pre>{sql}</pre>}
      {provenance.map((p, i) => (
        <pre key={i}>{p}</pre>
      ))}
      {String(d.note ?? '') && <p className="small muted">{String(d.note)}</p>}
    </div>
  )
}

export function fmt(v: unknown): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return Math.abs(n) >= 100 ? n.toLocaleString(undefined, { maximumFractionDigits: 0 }) : n.toLocaleString(undefined, { maximumFractionDigits: 3 })
}
