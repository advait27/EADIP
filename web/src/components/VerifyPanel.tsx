import { useState } from 'react'
import { verifyBundle, type VerifyResult } from '../lib/duck'
import type { EvidenceBundle } from '../lib/types'
import { fmt } from './NodeDetail'

const LABEL: Record<VerifyResult['verdict'], string> = {
  verified: 'verified in your browser',
  conflict: 'conflict',
  sampled: 'sampled rows · cannot verify',
  cannot_run: 'cannot run',
  no_magnitude: 'reproduced · no number to compare',
}

export function VerifyPanel({
  loadBundle,
  onSelectClaim,
}: {
  loadBundle: () => Promise<EvidenceBundle>
  onSelectClaim: (index: number) => void
}) {
  const [bundle, setBundle] = useState<EvidenceBundle | null>(null)
  const [results, setResults] = useState<VerifyResult[]>([])
  const [progress, setProgress] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<number | null>(null)

  const run = async () => {
    setBusy(true)
    setError(null)
    setResults([])
    try {
      setProgress('fetching evidence bundle…')
      const b = await loadBundle()
      setBundle(b)
      await verifyBundle(b, setProgress, (r) => setResults((prev) => [...prev, r]))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const verified = results.filter((r) => r.verdict === 'verified').length
  return (
    <div className="card verify">
      <h3>Verify it yourself</h3>
      <p className="small muted" style={{ margin: '0 0 8px' }}>
        Your browser downloads the tenant's rows and re-runs every claim's SQL in DuckDB-WASM. The
        number is re-derived here, not trusted from the server.
      </p>
      <div className="row">
        <button className="primary" onClick={run} disabled={busy}>
          {busy ? 'Verifying…' : results.length ? 'Verify again' : 'Verify in this browser'}
        </button>
        {results.length > 0 && (
          <span className="small">
            <b>{verified}</b> / {results.length} verified
          </span>
        )}
      </div>
      <div className="progress">{progress}</div>
      {error && <div className="error small">{error}</div>}
      {bundle && bundle.tables.some((t) => t.truncated) && (
        <div className="small" style={{ color: 'var(--warn)' }}>
          A table hit the row cap ({bundle.row_cap}); those claims are marked sampled.
        </div>
      )}
      {results.map((r) => {
        const claim = bundle?.claims.find((c) => c.index === r.index)
        return (
          <div key={r.index}>
            <div
              className="verdict"
              onClick={() => {
                setOpen(open === r.index ? null : r.index)
                onSelectClaim(r.index)
              }}
            >
              <span className="small">{claim?.claim}</span>
              <span className="n">
                {r.browserValue !== null ? fmt(r.browserValue) : '—'}
                {r.claimed !== null ? ` vs ${fmt(r.claimed)}` : ''}
              </span>
              <span className={`pill ${r.verdict}`}>{LABEL[r.verdict]}</span>
            </div>
            {open === r.index && claim && (
              <div>
                <pre>{claim.sql}</pre>
                <div className="small muted">
                  {r.rows} rows · {r.ms.toFixed(0)} ms · tolerance {bundle?.rel_tolerance}
                  {r.error ? ` · ${r.error}` : ''}
                </div>
              </div>
            )}
          </div>
        )
      })}
      {bundle && bundle.skipped.length > 0 && (
        <div className="small muted" style={{ marginTop: 6 }}>
          {bundle.skipped.length} claim(s) have no runnable SQL (retrieval/tool provenance).
        </div>
      )}
    </div>
  )
}
