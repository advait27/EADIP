// Snapshot commitment: sha256 over a bundle table's rows in a canonical form.
// MUST stay identical to src/eadip/verification/commitment.py (see its
// docstring for the spec and the known portability edge cases: exponent
// notation, |n| >= 1e21, astral-plane sort order, nested cell values).

function canonicalValue(v: unknown): unknown {
  if (v === null || v === undefined) return null
  if (typeof v === 'boolean' || typeof v === 'string') return v
  // Integral numbers already stringify as integers in JS (400.0 → "400"),
  // matching Python's float → int step. Non-finite → null, as the server serialises.
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  return String(v)
}

/** Each row as its canonical compact-JSON string, sorted (row order is irrelevant). */
export function canonicalRows(rows: unknown[][]): string[] {
  return rows.map((r) => JSON.stringify(r.map(canonicalValue))).sort()
}

/** sha256 (hex) of the canonical form of `rows`. */
export async function commitRows(rows: unknown[][]): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalRows(rows).join('\n'))
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}
