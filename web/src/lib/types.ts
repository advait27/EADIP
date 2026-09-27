// Mirrors of the gateway's API-boundary models (src/eadip/gateway/models.py,
// orchestrator/graph.py, verification/bundle.py, ports/events.py).

export type Json = Record<string, unknown>

export type LoggedEvent = { seq: number; at: number; type: string; data: Json }

export type GraphNode = { id: string; kind: NodeKind; label: string; data: Json }
export type GraphEdge = { source: string; target: string; relation: string }
export type EvidenceGraph = { nodes: GraphNode[]; edges: GraphEdge[] }
export type NodeKind =
  | 'question'
  | 'goal'
  | 'step'
  | 'finding'
  | 'claim'
  | 'evidence'
  | 'recommendation'

export type ClaimCounts = { verified: number; unverified: number; conflicting: number }

export type RunStatus = {
  id: string
  question: string
  status: string
  running: boolean
  iterations: number
  cost_usd: number
  elapsed_s: number
  stop_reason: string | null
  findings: number
  claims: ClaimCounts
  pending_approvals: number
  has_brief: boolean
  last_seq: number
}

export type RunCreated = { id: string; status: string; question: string; events_url: string }

export type VerifiedClaim = {
  claim: string
  source: string
  kind: string
  status: 'verified' | 'unverified' | 'conflicting'
  method: string
  confidence: number
  association_only: boolean
  claimed_magnitude: number | null
  recomputed_magnitude: number | null
  corroborating_sources: number
  provenance: string[]
  note: string
  detail: Json
}

export type Recommendation = {
  action: string
  rationale: string
  impact: number
  confidence: number
  based_on: string[]
}

export type Brief = {
  question: string
  headline: string
  overall_confidence: number
  key_findings: VerifiedClaim[]
  recommendations: Recommendation[]
  assumptions: string[]
  limitations: string[]
  drill_down: { claims?: VerifiedClaim[]; queries?: string[] }
}

export type Report = {
  run_id: string
  status: string
  verified: number
  unverified: number
  conflicting: number
  brief: Brief
}

export type BundleColumn = { name: string; type: 'text' | 'number' | 'date' | string }
export type BundleTable = {
  name: string
  columns: BundleColumn[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
  sha256: string // canonical hash of `rows` as served (lib/commitment.ts)
  matches_commitment: boolean | null // server's own comparison; null = none recorded
}
export type BundleClaim = {
  index: number
  claim: string
  kind: string
  status: string
  sql: string
  tables: string[]
  claimed_magnitude: number | null
  recomputed_magnitude: number | null
  detail: Json
}
export type EvidenceBundle = {
  run_id: string
  rel_tolerance: number
  row_cap: number
  claims: BundleClaim[]
  tables: BundleTable[]
  skipped: { index: number; reason: string }[]
  snapshot_commitments: Record<string, string> // table → sha256 recorded at verification
}

export type Timeline = { run_id: string; question: string; status: string; events: LoggedEvent[] }

export type ShareResponse = { token: string; url: string; api_url: string; expires_at: string }
export type SharePayload = {
  run_id: string
  question: string
  status: string
  expires_at: string
  timeline: LoggedEvent[]
  graph: EvidenceGraph
  brief: Brief | null
  evidence_bundle_url: string
}

export type ApprovalItem = {
  id: string
  status: string
  step_id: string
  kind: string
  effect: string
  server: string
  tool: string
  summary: string
  payload: Json
}

export type Dataset = {
  name: string
  table: string
  columns: BundleColumn[]
  row_count: number
  period_column: string | null
}
