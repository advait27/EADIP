// A faithful port of src/eadip/verification/recompute.py: re-derive a claim's
// magnitude from a FRESH query result, inferring column roles the same way the
// server does (the one all-numeric column is the metric; the rest are text).
// The verify panel runs the claim's SQL in DuckDB-WASM and feeds the result
// here, so the browser's number is computed independently of the server's.

export type Table = { columns: string[]; rows: unknown[][] }
export type ClaimLike = { kind: string; detail: Record<string, unknown> }

function isNumber(v: unknown): boolean {
  if (typeof v === 'boolean' || v === null || v === undefined) return false
  if (typeof v === 'number') return Number.isFinite(v)
  if (typeof v === 'bigint') return true
  const n = Number(v)
  return typeof v === 'string' && v.trim() !== '' && Number.isFinite(n)
}

const num = (v: unknown): number => (typeof v === 'bigint' ? Number(v) : Number(v))
const str = (v: unknown): string => (v === null || v === undefined ? 'None' : String(v))

function numericCol(t: Table): number | null {
  for (let i = 0; i < t.columns.length; i++) {
    const vals = t.rows.map((r) => r[i])
    if (vals.length && vals.every(isNumber)) return i
  }
  return null
}

const textCols = (t: Table, ni: number) => t.columns.map((_, i) => i).filter((i) => i !== ni)
const distinct = (t: Table, c: number) => [...new Set(t.rows.map((r) => str(r[c])))].sort()

function colContaining(t: Table, cols: number[], value: string): number | null {
  for (const c of cols) if (t.rows.some((r) => str(r[c]) === value)) return c
  return null
}

function seriesByPeriod(t: Table, periodCol: number, metricCol: number): number[] {
  return [...t.rows]
    .sort((a, b) => (str(a[periodCol]) < str(b[periodCol]) ? -1 : str(a[periodCol]) > str(b[periodCol]) ? 1 : 0))
    .map((r) => num(r[metricCol]))
}

function seriesForMember(
  t: Table,
  dimCol: number,
  periodCol: number,
  metricCol: number,
  periods: string[],
  member: string,
): number[] {
  const by = new Map<string, number>()
  for (const r of t.rows) if (str(r[dimCol]) === member) by.set(str(r[periodCol]), num(r[metricCol]))
  return periods.map((p) => by.get(p) ?? 0)
}

export function pearson(x: number[], y: number[]): number {
  const n = x.length
  if (n !== y.length || n < 2) return 0
  const mx = x.reduce((a, b) => a + b, 0) / n
  const my = y.reduce((a, b) => a + b, 0) / n
  let sxy = 0
  let sxx = 0
  let syy = 0
  for (let i = 0; i < n; i++) {
    sxy += (x[i] - mx) * (y[i] - my)
    sxx += (x[i] - mx) ** 2
    syy += (y[i] - my) ** 2
  }
  if (sxx <= 0 || syy <= 0) return 0
  return sxy / Math.sqrt(sxx * syy)
}

/** OLS next-point forecast (the server's linear_forecast().next_value). */
export function forecastNext(values: number[]): number | null {
  const n = values.length
  if (n < 3) return null
  const xs = values.map((_, i) => i)
  const mx = xs.reduce((a, b) => a + b, 0) / n
  const my = values.reduce((a, b) => a + b, 0) / n
  let sxx = 0
  let sxy = 0
  for (let i = 0; i < n; i++) {
    sxx += (xs[i] - mx) ** 2
    sxy += (xs[i] - mx) * (values[i] - my)
  }
  if (sxx === 0) return null
  const slope = sxy / sxx
  const intercept = my - slope * mx
  return intercept + slope * n
}

const sumWhere = (t: Table, ni: number, pred: (r: unknown[]) => boolean) =>
  t.rows.filter(pred).reduce((acc, r) => acc + num(r[ni]), 0)

function declaredPeriodCol(d: Record<string, unknown>, t: Table, tcols: number[]): number | null {
  const name = String(d.period_column ?? '').toLowerCase()
  if (!name) return null
  const idx = t.columns.map((c) => c.toLowerCase()).indexOf(name)
  return idx !== -1 && tcols.includes(idx) ? idx : null
}

export function recomputeMagnitude(claim: ClaimLike, t: Table): number | null {
  const ni = numericCol(t)
  if (ni === null) return null
  const tcols = textCols(t, ni)
  const d = claim.detail ?? {}
  const declared = declaredPeriodCol(d, t, tcols)

  if (claim.kind === 'headline') {
    if (!tcols.length) return null
    const periodCol =
      declared ?? tcols.reduce((best, c) => (distinct(t, c).length < distinct(t, best).length ? c : best))
    const periods = distinct(t, periodCol)
    if (periods.length < 2) return null
    const [base, cur] = [periods[0], periods[periods.length - 1]]
    return sumWhere(t, ni, (r) => str(r[periodCol]) === cur) - sumWhere(t, ni, (r) => str(r[periodCol]) === base)
  }

  if (claim.kind === 'driver') {
    const member = d.member
    if (member === undefined || member === null || tcols.length < 2) return null
    const dimCol = colContaining(t, tcols.filter((c) => c !== declared), str(member))
    if (dimCol === null) return null
    const periodCol = declared ?? tcols.find((c) => c !== dimCol)!
    const periods = distinct(t, periodCol)
    if (periods.length < 2) return null
    const [base, cur] = [periods[0], periods[periods.length - 1]]
    const m = str(member)
    return (
      sumWhere(t, ni, (r) => str(r[dimCol]) === m && str(r[periodCol]) === cur) -
      sumWhere(t, ni, (r) => str(r[dimCol]) === m && str(r[periodCol]) === base)
    )
  }

  if (claim.kind === 'forecast') {
    if (tcols.length !== 1) return null
    return forecastNext(seriesByPeriod(t, tcols[0], ni))
  }

  if (claim.kind === 'anomaly') {
    if (tcols.length !== 1) return null
    const period = str(d.period ?? '')
    const match = t.rows.find((r) => str(r[tcols[0]]) === period)
    return match ? num(match[ni]) : null
  }

  if (claim.kind === 'correlation') {
    const x = d.x
    const y = d.y
    if (!x || !y || tcols.length < 2) return null
    const dimCol = colContaining(t, tcols.filter((c) => c !== declared), str(x))
    if (dimCol === null) return null
    const periodCol = declared ?? tcols.find((c) => c !== dimCol)!
    const periods = distinct(t, periodCol)
    return pearson(
      seriesForMember(t, dimCol, periodCol, ni, periods, str(x)),
      seriesForMember(t, dimCol, periodCol, ni, periods, str(y)),
    )
  }
  return null
}

/** The server's tolerance rule: |a - b| <= tol * max(1, |b|). */
export function close(a: number, b: number, tol: number): boolean {
  return Math.abs(a - b) <= tol * Math.max(1, Math.abs(b))
}
