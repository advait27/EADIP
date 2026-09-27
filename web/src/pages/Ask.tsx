import { useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'

const EXAMPLES = [
  'Why did EMEA gross margin fall last quarter?',
  'What drove the change in AMER gross margin?',
  'Explain the APAC gross margin movement and its product-line drivers.',
  'Why did EMEA gross margin fall last quarter? Then open a ticket to remediate the top driver.',
]

export function Ask() {
  const [params] = useSearchParams()
  const [q, setQ] = useState(params.get('q') ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const submit = async (e?: FormEvent) => {
    e?.preventDefault()
    if (!q.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const run = await api.createRun(q.trim())
      navigate(`/runs/${run.id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err))
      setBusy(false)
    }
  }

  return (
    <div className="ask">
      <h1>Ask a business question. Watch it get investigated.</h1>
      <p className="lead">
        The engine plans, gathers evidence, runs the numbers, verifies every claim independently,
        and pauses for a human before it changes anything. You can re-run every number yourself,
        in this browser.
      </p>
      <form onSubmit={submit}>
        <textarea
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Why did EMEA gross margin fall last quarter?"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void submit()
          }}
        />
        <div className="row" style={{ display: 'flex', gap: 10, marginTop: 10, alignItems: 'center' }}>
          <button className="primary" type="submit" disabled={busy || !q.trim()}>
            {busy ? 'Starting…' : 'Investigate'}
          </button>
          <span className="muted small">⌘↩ to submit</span>
        </div>
      </form>
      {error && <div className="error">{error}</div>}
      <div className="chips">
        {EXAMPLES.map((ex) => (
          <span key={ex} className="chip" onClick={() => setQ(ex)}>
            {ex}
          </span>
        ))}
      </div>
    </div>
  )
}
