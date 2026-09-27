// @vitest-environment node
// (node's WebCrypto provides crypto.subtle; jsdom's crypto does not.)
import { describe, expect, it } from 'vitest'
import { canonicalRows, commitRows } from './commitment'

// Rows as the bundle serves them. The expected hash was computed with the
// Python implementation (eadip.verification.commitment.commit_rows) over the
// same rows with 400.0 as a float — the two sides must agree byte for byte.
const rows: unknown[][] = [
  ['EMEA', 'Hardware', '2026-Q1', 400, null, true],
  ['EMEA', 'Café', '2026-Q2', 180, 0.5, false],
  ['APAC', 'Software', '2026-Q1', -12.25, 3, true],
]
const PYTHON_HASH = '99ee58b59bfdae1a15d190877bf417c2c05f4f2cfef0e4adff8dfb70d6215a51'

describe('snapshot commitment', () => {
  it('canonical form: compact JSON per row, sorted', () => {
    expect(canonicalRows(rows)).toEqual([
      '["APAC","Software","2026-Q1",-12.25,3,true]',
      '["EMEA","Café","2026-Q2",180,0.5,false]',
      '["EMEA","Hardware","2026-Q1",400,null,true]',
    ])
  })
  it('matches the Python hash and ignores row order', async () => {
    expect(await commitRows(rows)).toBe(PYTHON_HASH)
    expect(await commitRows([rows[2], rows[0], rows[1]])).toBe(PYTHON_HASH)
  })
  it('changes when a value changes; empty table is sha256("")', async () => {
    const changed = rows.map((r) => [...r])
    changed[0][3] = 401
    expect(await commitRows(changed)).not.toBe(PYTHON_HASH)
    expect(await commitRows([])).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
  })
})
