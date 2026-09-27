import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { ApprovalItem } from '../lib/types'

export function Approvals({
  runId,
  items,
  onDecided,
}: {
  runId: string
  items: ApprovalItem[]
  onDecided: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const decide = async (a: ApprovalItem, decision: 'approve' | 'reject') => {
    setBusy(true)
    setError(null)
    try {
      await api.decide(runId, a.id, decision, decision === 'reject' ? 'rejected from Glass Box' : '')
      onDecided()
    } catch (e) {
      setError(e instanceof ApiError ? (e.status === 403 ? 'Only an approver may authorise this action. Switch roles above.' : e.detail) : String(e))
    } finally {
      setBusy(false)
    }
  }
  if (!items.length) return null
  return (
    <div className="card approval">
      <h3>Human approval required</h3>
      {items.map((a) => (
        <div key={a.id}>
          <p className="question" style={{ marginBottom: 2 }}>
            {a.summary}
          </p>
          <div className="small muted">
            {a.server}.{a.tool} · effect {a.effect} · step {a.step_id}
          </div>
          <pre>{JSON.stringify(a.payload, null, 2)}</pre>
          <div className="row">
            <button className="danger" disabled={busy} onClick={() => decide(a, 'reject')}>
              Reject
            </button>
            <button className="primary" disabled={busy} onClick={() => decide(a, 'approve')}>
              Approve &amp; resume
            </button>
          </div>
        </div>
      ))}
      {error && <div className="error small">{error}</div>}
    </div>
  )
}
