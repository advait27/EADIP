// Verify in your browser: load the evidence bundle's rows into DuckDB-WASM,
// run every claim's provenance SQL locally, and re-derive the number with the
// ported recompute logic. The wasm (~34 MB raw, precompressed at build time)
// is fetched lazily, only when a reader asks to verify.
//
// The rows come from the same server that made the claims, so this re-checks
// arithmetic over server-supplied rows. Before re-running, each table's rows are
// hashed here (lib/commitment.ts) and compared with the hash the bundle states
// and with the snapshot committed at verification time; a mismatch marks every
// claim on that table 'tampered'. The commitment also comes from the server: it
// catches rows changing after verification, not a server lying consistently.
import type { BundleClaim, BundleTable, EvidenceBundle } from './types'
import { close, recomputeMagnitude, type Table } from './recompute'
import { commitRows } from './commitment'

export type Verdict = 'verified' | 'conflict' | 'sampled' | 'cannot_run' | 'no_magnitude' | 'tampered'
export type VerifyResult = {
  index: number
  verdict: Verdict
  browserValue: number | null
  claimed: number | null
  rows: number
  error?: string
  ms: number
}

type Db = {
  conn: {
    query: (sql: string) => Promise<{ schema: { fields: { name: string }[] }; toArray: () => Array<{ toJSON: () => Record<string, unknown> }> }>
    close: () => Promise<void>
  }
  db: { registerFileText: (name: string, text: string) => Promise<void> }
}

let dbPromise: Promise<Db> | null = null

async function openDb(onProgress?: (msg: string) => void): Promise<Db> {
  if (dbPromise) return dbPromise
  dbPromise = (async () => {
    onProgress?.('loading DuckDB-WASM…')
    const duckdb = await import('@duckdb/duckdb-wasm')
    const wasmUrl = (await import('@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url')).default
    const workerUrl = (await import('@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url')).default
    const worker = new Worker(workerUrl, { type: 'module' })
    const logger = new duckdb.VoidLogger()
    const db = new duckdb.AsyncDuckDB(logger, worker)
    await db.instantiate(wasmUrl)
    const conn = await db.connect()
    return { conn, db } as unknown as Db
  })()
  return dbPromise
}

const sqlType = (t: string) => (t === 'number' ? 'DOUBLE' : t === 'date' ? 'DATE' : 'VARCHAR')

async function loadTable(d: Db, table: BundleTable): Promise<void> {
  const objects = table.rows.map((r) => Object.fromEntries(table.columns.map((c, i) => [c.name, r[i]])))
  const file = `${table.name}.json`
  await d.db.registerFileText(file, JSON.stringify(objects))
  const cols = table.columns.map((c) => `'${c.name}': '${sqlType(c.type)}'`).join(', ')
  await d.conn.query(`DROP TABLE IF EXISTS "${table.name}"`)
  await d.conn.query(
    `CREATE TABLE "${table.name}" AS SELECT * FROM read_json('${file}', format='array', columns={${cols}})`,
  )
}

function toTable(result: { schema: { fields: { name: string }[] }; toArray: () => Array<{ toJSON: () => Record<string, unknown> }> }): Table {
  const columns = result.schema.fields.map((f) => f.name)
  const rows = result.toArray().map((row) => {
    const o = row.toJSON()
    return columns.map((c) => o[c])
  })
  return { columns, rows }
}

export async function verifyClaim(d: Db, claim: BundleClaim, bundle: EvidenceBundle): Promise<VerifyResult> {
  const t0 = performance.now()
  const sampled = bundle.tables.some((t) => claim.tables.includes(t.name) && t.truncated)
  try {
    const table = toTable(await d.conn.query(claim.sql))
    const value = recomputeMagnitude({ kind: claim.kind, detail: claim.detail }, table)
    const ms = performance.now() - t0
    if (claim.claimed_magnitude === null || value === null) {
      return { index: claim.index, verdict: 'no_magnitude', browserValue: value, claimed: claim.claimed_magnitude, rows: table.rows.length, ms }
    }
    if (sampled) {
      return { index: claim.index, verdict: 'sampled', browserValue: value, claimed: claim.claimed_magnitude, rows: table.rows.length, ms }
    }
    const ok = close(value, claim.claimed_magnitude, bundle.rel_tolerance)
    return { index: claim.index, verdict: ok ? 'verified' : 'conflict', browserValue: value, claimed: claim.claimed_magnitude, rows: table.rows.length, ms }
  } catch (e) {
    return { index: claim.index, verdict: 'cannot_run', browserValue: null, claimed: claim.claimed_magnitude, rows: 0, error: String(e), ms: performance.now() - t0 }
  }
}

/** Tables whose served rows do not hash to the stated sha256, or to the snapshot
 * committed at verification (when one was recorded), with the reason. */
export async function tamperedTables(bundle: EvidenceBundle): Promise<Map<string, string>> {
  const bad = new Map<string, string>()
  for (const t of bundle.tables) {
    const h = await commitRows(t.rows)
    const committed = bundle.snapshot_commitments?.[t.name]
    if (h !== t.sha256) bad.set(t.name, `rows hash ${h.slice(0, 12)}… ≠ stated ${String(t.sha256).slice(0, 12)}…`)
    else if (committed && h !== committed)
      bad.set(t.name, `rows hash ${h.slice(0, 12)}… ≠ committed at verification ${committed.slice(0, 12)}…`)
  }
  return bad
}

/** Load the bundle into the browser database and verify every claim. */
export async function verifyBundle(
  bundle: EvidenceBundle,
  onProgress?: (msg: string) => void,
  onResult?: (r: VerifyResult) => void,
): Promise<VerifyResult[]> {
  onProgress?.('hashing rows against the verification snapshot…')
  const tampered = await tamperedTables(bundle)
  const d = await openDb(onProgress)
  for (const t of bundle.tables) {
    onProgress?.(`loading ${t.name} (${t.row_count} rows)…`)
    await loadTable(d, t)
  }
  const out: VerifyResult[] = []
  for (const c of bundle.claims) {
    const hit = c.tables.find((name) => tampered.has(name))
    if (hit) {
      const r: VerifyResult = { index: c.index, verdict: 'tampered', browserValue: null, claimed: c.claimed_magnitude, rows: 0, error: `${hit}: ${tampered.get(hit)}`, ms: 0 }
      out.push(r)
      onResult?.(r)
      continue
    }
    onProgress?.(`re-running claim ${c.index}…`)
    const r = await verifyClaim(d, c, bundle)
    out.push(r)
    onResult?.(r)
  }
  onProgress?.('')
  return out
}
