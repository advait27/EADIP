"""Knowledge search route: governed, identity-scoped hybrid retrieval (FR-010)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from eadip.gateway.authz import require
from eadip.gateway.dependencies import AuditLogDep, ResidencyGuardDep, RetrievalServiceDep
from eadip.gateway.models import EvidenceItem, SearchRequest, SearchResponse
from eadip.observability.logging import get_logger
from eadip.retrieval.models import Query
from eadip.security.audit import make_event
from eadip.security.identity import Identity
from eadip.security.rbac import Effect

router = APIRouter(prefix="/v1/search", tags=["search"])
log = get_logger(__name__)

# Searching knowledge requires read on the knowledge domain (analyst has it).
SearcherDep = Annotated[Identity, Depends(require("knowledge", "*", Effect.READ))]


def _acl_tags_for(identity: Identity) -> tuple[str, ...]:
    # Phase 4 bridge: use the caller's roles as their allowed ACL tags. A richer
    # identity -> data-domain grant mapping replaces this later.
    return identity.roles


@router.post("", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    identity: SearcherDep,
    _residency: ResidencyGuardDep,
    service: RetrievalServiceDep,
    audit: AuditLogDep,
) -> SearchResponse:
    query = Query(
        tenant_id=identity.tenant_id,
        text=body.query,
        acl_tags=_acl_tags_for(identity),
        sources=tuple(body.sources),
        as_of=body.as_of,
        top_k=body.k or 8,
        effort=body.effort,
    )
    result = await service.retrieve(query)
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="knowledge.search",
            detail={"query_len": len(body.query), "results": len(result.passages)},
        )
    )
    log.info("search.done", tenant_id=str(identity.tenant_id), results=len(result.passages))
    return SearchResponse(
        query=body.query,
        sub_queries=result.sub_queries,
        candidates_considered=result.candidates_considered,
        evidence=[
            EvidenceItem(
                id=p.id,
                text=p.text,
                score=p.score,
                source_ref=p.source_ref,
                source_type=p.source_type,
            )
            for p in result.passages
        ],
    )
