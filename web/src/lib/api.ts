import { identityHeaders } from './identity'
import type {
  ApprovalItem,
  Dataset,
  EvidenceBundle,
  EvidenceGraph,
  Report,
  RunCreated,
  RunStatus,
  SharePayload,
  ShareResponse,
  Timeline,
} from './types'

export class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`)
    this.status = status
    this.detail = detail
  }
}

async function req<T>(method: string, path: string, body?: unknown, auth = true): Promise<T> {
  const headers: Record<string, string> = auth ? identityHeaders() : {}
  const init: RequestInit = { method, headers }
  if (body instanceof FormData) {
    init.body = body
  } else if (body !== undefined) {
    headers['content-type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  const r = await fetch(path, init)
  if (!r.ok) {
    let detail = r.statusText
    try {
      const j = (await r.json()) as { detail?: unknown }
      if (j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* no body */
    }
    throw new ApiError(r.status, detail)
  }
  return (await r.json()) as T
}

export const api = {
  createRun: (question: string) => req<RunCreated>('POST', '/v1/runs', { question }),
  run: (id: string) => req<RunStatus>('GET', `/v1/runs/${id}`),
  timeline: (id: string) => req<Timeline>('GET', `/v1/runs/${id}/timeline`),
  graph: (id: string) => req<EvidenceGraph>('GET', `/v1/runs/${id}/graph`),
  report: (id: string) => req<Report>('GET', `/v1/runs/${id}/report`),
  bundle: (id: string) => req<EvidenceBundle>('GET', `/v1/runs/${id}/evidence-bundle`),
  share: (id: string) => req<ShareResponse>('POST', `/v1/runs/${id}/share`),
  approvals: (id: string) => req<ApprovalItem[]>('GET', `/v1/runs/${id}/approvals`),
  decide: (id: string, approvalId: string, decision: 'approve' | 'reject', reason = '') =>
    req<{ status: string }>('POST', `/v1/runs/${id}/approvals/${approvalId}`, { decision, reason }),
  shareView: (token: string) => req<SharePayload>('GET', `/v1/share/${token}`, undefined, false),
  shareBundle: (token: string) =>
    req<EvidenceBundle>('GET', `/v1/share/${token}/evidence-bundle`, undefined, false),
  datasets: () => req<Dataset[]>('GET', '/v1/datasets'),
  uploadDataset: (file: File, name?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (name) form.append('name', name)
    return req<Dataset>('POST', '/v1/datasets', form)
  },
}
