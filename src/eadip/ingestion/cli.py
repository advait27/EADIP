"""`eadip-ingest` — ingest a local file into a tenant's knowledge base.

Example:
    eadip-ingest <tenant-uuid> ./report.html --acl finance,emea --source-ref report#1

With the default in-memory backend the store is process-local (the run still
exercises the full pipeline and prints the result); point the settings at Qdrant
to persist.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from uuid import UUID

from eadip.config.settings import get_settings
from eadip.ingestion.factory import build_pipeline
from eadip.ingestion.models import IngestRequest
from eadip.ingestion.parsing import guess_content_type


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="eadip-ingest", description="Ingest a document.")
    parser.add_argument("tenant_id", type=UUID)
    parser.add_argument("path", type=Path)
    parser.add_argument("--content-type", default=None)
    parser.add_argument("--source-ref", default=None)
    parser.add_argument("--source-type", default="document")
    parser.add_argument("--acl", default="", help="comma-separated ACL tags")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    raw = args.path.read_bytes()
    content_type = args.content_type or guess_content_type(str(args.path))
    request = IngestRequest(
        tenant_id=args.tenant_id,
        raw=raw,
        content_type=content_type,
        source_ref=args.source_ref or args.path.name,
        source_type=args.source_type,
        acl_tags=tuple(t.strip() for t in args.acl.split(",") if t.strip()),
        filename=args.path.name,
    )
    pipeline = build_pipeline(get_settings())
    result = await pipeline.ingest(request)
    print(
        f"ingested document {result.document_id} into {result.collection}: "
        f"{result.chunk_count} chunks, {result.cache_hits} cache hits, "
        f"pii={list(result.pii_types_found) or 'none'}"
    )


def main() -> None:
    asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    main()
