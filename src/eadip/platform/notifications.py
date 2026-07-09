"""Notification system (Phase 11, FR-056, TAD Ch 10).

`NotificationService.publish` fans a notification out to the channels configured
for its kind. Channels are a port: the in-memory channel is the dev/test default
(and doubles as the tenant-visible inbox the portal lists); the log channel
writes structured log events for ops pipelines; a webhook channel (lazy httpx)
posts JSON to an external receiver (Slack/Teams/e-mail bridges live behind it).

`notifications_from_event` maps orchestrator stream events to notifications —
the gateway taps the SSE stream it is already relaying, so notifying adds no
coupling inside the engine.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from eadip.observability.logging import get_logger
from eadip.orchestrator.models import Event
from eadip.platform.models import Notification, NotificationKind

log = get_logger(__name__)


class NotificationChannel(Protocol):
    name: str

    async def send(self, notification: Notification) -> bool: ...


class InMemoryChannel:
    """Records notifications per tenant — the dev default and the portal inbox."""

    name = "inbox"

    def __init__(self, max_per_tenant: int = 200) -> None:
        self._by_tenant: dict[UUID, list[Notification]] = {}
        self._max = max_per_tenant

    async def send(self, notification: Notification) -> bool:
        inbox = self._by_tenant.setdefault(notification.tenant_id, [])
        inbox.append(notification)
        del inbox[: max(0, len(inbox) - self._max)]  # bound the inbox
        return True

    def list(self, tenant_id: UUID) -> list[Notification]:
        return list(self._by_tenant.get(tenant_id, []))


class LogChannel:
    """Structured log events — ops pipelines (alerting) subscribe downstream."""

    name = "log"

    async def send(self, notification: Notification) -> bool:
        log.info(
            "notification",
            kind=str(notification.kind),
            severity=notification.severity,
            tenant_id=str(notification.tenant_id),
            title=notification.title,
        )
        return True


class WebhookChannel:
    """POST the notification JSON to an external receiver (prod integrations)."""

    name = "webhook"

    def __init__(self, url: str, timeout_s: float = 5.0) -> None:
        self._url = url
        self._timeout_s = timeout_s

    async def send(self, notification: Notification) -> bool:
        import httpx  # lazy: only the webhook path needs it

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(self._url, json=notification.model_dump(mode="json"))
            return resp.status_code < 300
        except Exception as exc:  # noqa: BLE001 — notification loss is non-fatal
            log.warning("notification.webhook_failed", error=str(exc))
            return False


class NotificationService:
    def __init__(
        self,
        channels: list[NotificationChannel],
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._channels = channels
        self._now = now or (lambda: 0.0)

    async def publish(self, notification: Notification) -> Notification:
        notification.created_at_s = notification.created_at_s or self._now()
        for channel in self._channels:
            if await channel.send(notification):
                notification.channel = notification.channel or channel.name
                notification.delivered = True
        return notification


def notifications_from_event(tenant_id: UUID, run_id: UUID, event: Event) -> list[Notification]:
    """Map one orchestrator stream event to zero or more notifications."""
    if event.type == "approval.required":
        action = event.data.get("action", {})
        return [
            Notification(
                tenant_id=tenant_id,
                kind=NotificationKind.APPROVAL_REQUIRED,
                severity="action_required",
                title="A run is paused awaiting your approval",
                body=str(action.get("summary", "")),
                run_id=run_id,
            )
        ]
    if event.type == "run.done":
        return [
            Notification(
                tenant_id=tenant_id,
                kind=NotificationKind.RUN_COMPLETED,
                severity="info",
                title=f"Run completed ({event.data.get('status', '')})",
                body=f"{event.data.get('findings', 0)} findings, "
                f"${event.data.get('cost_usd', 0.0)} spent",
                run_id=run_id,
            )
        ]
    if event.type == "finding.partial" and event.data.get("kind") == "anomaly":
        return [
            Notification(
                tenant_id=tenant_id,
                kind=NotificationKind.ANOMALY_DETECTED,
                severity="warning",
                title="Anomaly detected",
                body=str(event.data.get("claim", "")),
                run_id=run_id,
            )
        ]
    return []
