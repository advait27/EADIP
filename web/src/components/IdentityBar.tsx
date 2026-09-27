import { useState } from 'react'
import { loadIdentity, saveIdentity, type Identity } from '../lib/identity'

const ROLE_SETS = [
  ['analyst approver', 'analyst + approver'],
  ['analyst', 'analyst only'],
  ['viewer', 'viewer'],
  ['admin', 'admin'],
]

export function IdentityBar() {
  const [id, setId] = useState<Identity>(loadIdentity)
  const update = (patch: Partial<Identity>) => {
    const next = { ...id, ...patch }
    setId(next)
    saveIdentity(next)
  }
  return (
    <div className="identity" title="Dev-mode identity: EADIP_AUTH_MODE=dev trusts these headers">
      <span>acting as</span>
      <select value={id.roles} onChange={(e) => update({ roles: e.target.value })}>
        {ROLE_SETS.map(([v, label]) => (
          <option key={v} value={v}>
            {label}
          </option>
        ))}
      </select>
      <input
        placeholder="tenant (default)"
        value={id.tenant}
        onChange={(e) => update({ tenant: e.target.value })}
        style={{ width: 150 }}
      />
    </div>
  )
}
