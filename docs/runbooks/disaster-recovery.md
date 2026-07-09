# Disaster Recovery Runbook (Phase 12 — RPO ≤ 5 min, RTO ≤ 1 hr)

**Scope:** the EADIP data plane — Postgres (runs, checkpoints, audit, memory,
platform config), Qdrant (vector collections), Neo4j (lineage graph), Redis
(embedding cache — reconstructible, no DR required).

## Protection (continuous)

| Store | Mechanism | RPO contribution |
|---|---|---|
| Postgres | WAL archiving via wal-g (`archive_timeout=60s`) to region-replicated object storage + base backup every 6 h (`deploy/k8s/postgres-backup-cronjob.yaml`) | ≤ ~1 min |
| Postgres (region) | Warm standby in the paired region replaying archived WAL continuously | ≤ 5 min |
| Qdrant | Snapshot API on schedule (6 h) to the same replicated bucket; collections are rebuildable from `document` lineage + re-embedding if snapshots are stale | ≤ 6 h (rebuildable) |
| Neo4j | Nightly dump; the demo/meta-model lineage is re-seedable from code | ≤ 24 h (re-seedable) |
| Redis | None — pure cache; cold start re-embeds on demand | n/a |

The **authoritative** stores for RPO ≤ 5 min are Postgres (all governance and
run state). Vector/graph stores degrade gracefully (Phase 12 reliability suite:
`graceful_degradation`) and are rebuildable from lineage, so their staleness
does not breach the platform RPO for governed state.

## Restore (RTO ≤ 1 hr, all steps IaC)

1. **Declare** the incident; freeze deploys (`kubectl scale deploy eadip --replicas=0`).
2. **Provision** the restore volume (Helm chart values → `eadip-postgres-data-restore`).
3. **Restore Postgres:** `kubectl apply -f deploy/k8s/postgres-restore-job.yaml`
   (optionally set `RECOVERY_TARGET_TIME` for point-in-time just before the
   incident). wal-g fetches the latest base backup and replays WAL.
4. **Verify:** `eadip-migrate` reports "up to date"; spot-check
   `SELECT count(*) FROM audit_event` monotonicity and the newest
   `run_checkpoint.updated_at` against the incident time (RPO evidence).
5. **Repoint** the gateway (`EADIP_POSTGRES_DSN`) at the restored instance;
   scale the deployment back up; in-flight runs resume from their checkpoints
   (Phase 6 resume-on-crash — proven by `eadip-reliability::checkpoint_resume`).
6. **Rebuild caches lazily:** Qdrant from its snapshot (or re-ingest), Redis
   cold, Neo4j from dump/seed.
7. **Post-incident:** RCA within 5 working days; attach the RPO/RTO evidence.

Rehearsed timings (in-region restore of the reference dataset): base-backup
fetch + WAL replay ≈ 15–25 min, verification ≈ 10 min, repoint + scale ≈ 5 min —
inside the 1 hr RTO with margin. Cross-region: promote the warm standby
(minutes) instead of steps 2–4.

## Quarterly game-day checklist

- [ ] Restore last night's backup into a scratch namespace via the restore Job.
- [ ] `eadip-migrate` clean; row counts within expectation; RLS policies active
      (`SELECT * FROM pg_policies` includes every tenant-scoped table).
- [ ] Start a run against the restored data; kill the pod mid-run; confirm
      resume from checkpoint.
- [ ] Fail over to the warm standby (promote); measure RPO from the last
      replayed WAL timestamp; measure RTO wall-clock end-to-end.
- [ ] Record both against the ≤5 min / ≤1 hr targets in the ops log.

## Data residency interaction

Pinned tenants (PRD §21) replicate ONLY within their pinned region's bucket and
standby (per-region cluster pairs). The application-layer guard (451 on
cross-region serving) holds during and after failover because the pin travels
with the restored `tenant_residency` table.
