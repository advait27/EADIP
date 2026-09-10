import { describe, expect, it } from 'vitest'
import { close, forecastNext, pearson, recomputeMagnitude } from './recompute'

// The driver query shape: dimension, period, metric (as the server renders it).
const drivers = {
  columns: ['product_line', 'period', 'gross_margin'],
  rows: [
    ['Hardware', '2026-Q1', 400],
    ['Hardware', '2026-Q2', 180],
    ['Software', '2026-Q1', 600],
    ['Software', '2026-Q2', 610],
    ['Services', '2026-Q1', 250],
    ['Services', '2026-Q2', 230],
  ],
}

describe('recomputeMagnitude', () => {
  it('headline = total change between the first and last period', () => {
    expect(recomputeMagnitude({ kind: 'headline', detail: {} }, drivers)).toBe(180 + 610 + 230 - (400 + 600 + 250))
  })
  it('driver = the member change', () => {
    expect(recomputeMagnitude({ kind: 'driver', detail: { member: 'Hardware' } }, drivers)).toBe(-220)
    expect(recomputeMagnitude({ kind: 'driver', detail: { member: 'Nope' } }, drivers)).toBeNull()
    expect(recomputeMagnitude({ kind: 'driver', detail: {} }, drivers)).toBeNull()
  })
  it('anomaly = the flagged period value; forecast = OLS next point', () => {
    const trend = { columns: ['period', 'm'], rows: [['a', 1], ['b', 2], ['c', 3], ['d', 40]] }
    expect(recomputeMagnitude({ kind: 'anomaly', detail: { period: 'd' } }, trend)).toBe(40)
    expect(recomputeMagnitude({ kind: 'anomaly', detail: { period: 'z' } }, trend)).toBeNull()
    expect(forecastNext([1, 2, 3])).toBeCloseTo(4)
    expect(recomputeMagnitude({ kind: 'forecast', detail: {} }, { columns: ['p', 'm'], rows: [['a', 1], ['b', 2], ['c', 3]] })).toBeCloseTo(4)
    expect(forecastNext([1, 2])).toBeNull()
  })
  it('correlation = pearson over member series', () => {
    const r = recomputeMagnitude({ kind: 'correlation', detail: { x: 'Hardware', y: 'Services' } }, drivers)
    expect(r).toBeCloseTo(1)
    expect(pearson([1, 2, 3], [3, 2, 1])).toBeCloseTo(-1)
    expect(pearson([1, 1], [1, 2])).toBe(0)
  })
  it('handles bigint/strings from DuckDB and unknown kinds', () => {
    const t = { columns: ['p', 'm'], rows: [['a', 10n], ['b', '12.5']] }
    expect(recomputeMagnitude({ kind: 'headline', detail: {} }, t)).toBe(2.5)
    expect(recomputeMagnitude({ kind: 'passage', detail: {} }, t)).toBeNull()
    expect(recomputeMagnitude({ kind: 'headline', detail: {} }, { columns: ['p'], rows: [['a']] })).toBeNull()
  })
  it('prefers the declared period column when cardinalities tie', () => {
    const tie = {
      columns: ['channel', 'quarter', 'sales'],
      rows: [
        ['Online', '2026-Q1', 100],
        ['Retail', '2026-Q1', 80],
        ['Online', '2026-Q2', 70],
        ['Retail', '2026-Q2', 90],
      ],
    }
    expect(recomputeMagnitude({ kind: 'headline', detail: { period_column: 'quarter' } }, tie)).toBe(-20)
    expect(recomputeMagnitude({ kind: 'driver', detail: { member: 'Online', period_column: 'quarter' } }, tie)).toBe(-30)
  })
  it('tolerance matches the server rule', () => {
    expect(close(100, 101, 0.02)).toBe(true)
    expect(close(100, 103, 0.02)).toBe(false)
    expect(close(0.005, 0, 0.02)).toBe(true)
  })
})
