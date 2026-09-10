import { useEffect, useState, type DragEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import type { Dataset } from '../lib/types'

export function Datasets() {
  const [items, setItems] = useState<Dataset[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)

  const load = () =>
    api
      .datasets()
      .then(setItems)
      .catch((e) => setError(e instanceof ApiError ? e.detail : String(e)))
  useEffect(() => {
    void load()
  }, [])

  const upload = async (file: File) => {
    setBusy(true)
    setError(null)
    try {
      await api.uploadDataset(file)
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e))
    } finally {
      setBusy(false)
    }
  }
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    const f = e.dataTransfer.files[0]
    if (f) void upload(f)
  }

  return (
    <div className="datasets">
      <div>
        <h1 style={{ margin: 0 }}>Bring your own data</h1>
        <p className="muted">
          Drop a CSV. Numeric columns become metrics, low-cardinality text columns become
          driver dimensions, a period/quarter/month/date column becomes the time axis. Then ask
          about it by name.
        </p>
      </div>
      <label
        className={`drop ${over ? 'over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        {busy ? 'uploading…' : 'drop a .csv here, or click to choose'}
        <input
          type="file"
          accept=".csv,text/csv"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void upload(f)
          }}
        />
      </label>
      {error && <div className="error">{error}</div>}
      <div className="card">
        <h3>Your tables</h3>
        {items.length === 0 ? (
          <div className="muted small">No datasets yet. The demo finance table is always available.</div>
        ) : (
          <table className="ds">
            <thead>
              <tr>
                <th>name</th>
                <th>rows</th>
                <th>period</th>
                <th>columns</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => {
                const metric = d.columns.find((c) => c.type === 'number')?.name ?? 'value'
                return (
                  <tr key={d.name}>
                    <td>
                      <code>{d.name}</code>
                    </td>
                    <td>{d.row_count}</td>
                    <td>{d.period_column ?? '—'}</td>
                    <td className="small muted">
                      {d.columns
                        .filter((c) => c.name !== 'tenant_id')
                        .map((c) => `${c.name}:${c.type}`)
                        .join(', ')}
                    </td>
                    <td>
                      <Link to={`/?q=${encodeURIComponent(`Why did ${metric} change in ${d.name}?`)}`}>ask →</Link>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
