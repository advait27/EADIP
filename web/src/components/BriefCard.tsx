import type { Brief } from '../lib/types'
import { fmt } from './NodeDetail'

export function BriefCard({
  brief,
  selectedClaim,
  onSelectClaim,
}: {
  brief: Brief
  selectedClaim: string | null
  onSelectClaim: (claim: string) => void
}) {
  return (
    <div className="card">
      <h3>Executive brief · confidence {Math.round(brief.overall_confidence * 100)}%</h3>
      <p className="question">{brief.headline}</p>
      <div className="claims">
        {brief.key_findings.map((c, i) => (
          <div
            key={i}
            className={`claim ${selectedClaim === c.claim ? 'selected' : ''}`}
            onClick={() => onSelectClaim(c.claim)}
          >
            <div className="t">
              <span>{c.claim}</span>
              <span className={`pill ${c.status}`}>{c.status}</span>
            </div>
            <div className="c">
              {c.method} · {Math.round(c.confidence * 100)}%
              {c.claimed_magnitude !== null ? ` · ${fmt(c.claimed_magnitude)}` : ''}
              {c.association_only ? ' · association only' : ''}
            </div>
          </div>
        ))}
      </div>
      {brief.recommendations.length > 0 && (
        <>
          <h3 style={{ marginTop: 12 }}>Recommendations</h3>
          {brief.recommendations.map((r, i) => (
            <div className="rec" key={i}>
              <b>{r.action}</b>
              <span>
                {r.rationale} · impact {fmt(r.impact)} · confidence {Math.round(r.confidence * 100)}%
              </span>
            </div>
          ))}
        </>
      )}
      {brief.limitations.length > 0 && (
        <>
          <h3 style={{ marginTop: 12 }}>Limitations</h3>
          <ul className="plain">
            {brief.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </>
      )}
      <h3 style={{ marginTop: 12 }}>Assumptions</h3>
      <ul className="plain">
        {brief.assumptions.map((l, i) => (
          <li key={i}>{l}</li>
        ))}
      </ul>
    </div>
  )
}
