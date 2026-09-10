// Dev-mode identity (EADIP_AUTH_MODE=dev trusts these headers). The UI lets you
// flip roles so the approval flow can be demoed: an analyst can ask, only an
// approver can authorise a side-effecting action.

export type Identity = { roles: string; tenant: string; user: string }

const KEY = 'eadip.identity'
const DEFAULT: Identity = { roles: 'analyst approver', tenant: '', user: '' }

export function loadIdentity(): Identity {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return { ...DEFAULT, ...(JSON.parse(raw) as Partial<Identity>) }
  } catch {
    /* private mode, blocked storage: fall through */
  }
  return { ...DEFAULT }
}

export function saveIdentity(identity: Identity): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(identity))
  } catch {
    /* ignore */
  }
}

export function identityHeaders(identity: Identity = loadIdentity()): Record<string, string> {
  const h: Record<string, string> = { 'X-Roles': identity.roles.trim() || 'analyst' }
  if (identity.tenant.trim()) h['X-Tenant-ID'] = identity.tenant.trim()
  if (identity.user.trim()) h['X-User-ID'] = identity.user.trim()
  return h
}
