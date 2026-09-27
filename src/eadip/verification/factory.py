"""Build the verification/reporting stack from settings (Phase 7)."""

from __future__ import annotations

from eadip.config.settings import Settings
from eadip.ports.warehouse import Warehouse
from eadip.verification.recommend import HeuristicRecommender, Recommender
from eadip.verification.service import VerificationReportingService
from eadip.verification.verifier import VerificationService


def build_recommender(settings: Settings) -> Recommender:
    if settings.recommendation_backend == "llm":
        from eadip.adapters.litellm_client import LiteLLMClient
        from eadip.platform.factory import build_routing_policy
        from eadip.platform.models import TaskKind
        from eadip.verification.recommend import LLMRecommender

        client = LiteLLMClient(settings.default_model, settings.model_api_base)
        model = build_routing_policy(settings).route(TaskKind.RECOMMEND).model
        return LLMRecommender(client, model, settings.max_recommendations)
    return HeuristicRecommender(settings.max_recommendations)


def build_reporter(settings: Settings, warehouse: Warehouse) -> VerificationReportingService | None:
    """None when verification is disabled — the engine then skips the terminal
    verify+report stage."""
    if not settings.verification_enabled:
        return None
    verifier = VerificationService(
        warehouse=warehouse,
        row_cap=settings.analytics_row_cap,
        timeout_s=settings.analytics_statement_timeout_s,
        max_join_tables=settings.analytics_max_join_tables,
        rel_tolerance=settings.verification_rel_tolerance,
    )
    return VerificationReportingService(verifier=verifier, recommender=build_recommender(settings))
