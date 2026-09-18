"""
Outbound emergency notification adapters.

This is the seam where an operator plugs in whatever external channel they
actually have -- an SMS gateway, their own control-room webhook, a paging
system. CargoResQ does not ship an integration with any emergency service,
and the adapter interface does not pretend otherwise: it delivers to numbers
and URLs the operator configures, nothing more.

Every attempt writes a `sos_broadcasts` row with its outcome, so an incident
review can establish what the system tried and whether it worked. "We sent it"
with no delivery record is not an answer anyone should accept after a bad
night.

Adapters are invoked off the request path. The SOS write and the in-network
fan-out must complete in well under a second; a slow SMS gateway must never
delay the people who can actually drive to the scene.
"""
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

import httpx

from shared.observability import logger


@dataclass(frozen=True)
class NotifyTarget:
    kind: str  # phone | email | webhook | company
    value: str
    company_id: Optional[str] = None


@dataclass(frozen=True)
class NotifyResult:
    status: str  # SENT | FAILED | SKIPPED
    ref: Optional[str] = None
    error: Optional[str] = None


class EmergencyNotifier(Protocol):
    name: str

    def supports(self, category: str, severity: str) -> bool: ...

    async def notify(
        self, payload: Dict[str, Any], target: NotifyTarget
    ) -> NotifyResult: ...


class LoggingNotifier:
    """Always available, never fails, no external dependency.

    The default. It records the alert in the application log, which is a
    genuine audit trail, and makes the absence of a configured channel
    visible rather than silent.
    """

    name = "log"

    def supports(self, category: str, severity: str) -> bool:
        return True

    async def notify(
        self, payload: Dict[str, Any], target: NotifyTarget
    ) -> NotifyResult:
        logger.warn(
            "sos_outbound_notification",
            adapter=self.name,
            target_kind=target.kind,
            target=target.value,
            sos_id=payload.get("sosId"),
            category=payload.get("category"),
            severity=payload.get("severity"),
        )
        return NotifyResult(status="SENT", ref=f"log:{payload.get('sosId')}")


class WebhookNotifier:
    """POST the alert to an operator-configured URL.

    Signed with HMAC-SHA256 over the body so the receiver can verify it came
    from this platform. The body is the redacted projection -- an operator's
    control room gets the operational facts, not the driver's medical history.
    """

    name = "webhook"

    def __init__(self, url: Optional[str] = None, secret: Optional[str] = None) -> None:
        self.url = url or os.getenv("SOS_WEBHOOK_URL", "")
        self.secret = secret or os.getenv("SOS_WEBHOOK_SECRET", "")

    def supports(self, category: str, severity: str) -> bool:
        return bool(self.url)

    async def notify(
        self, payload: Dict[str, Any], target: NotifyTarget
    ) -> NotifyResult:
        if not self.url:
            return NotifyResult(status="SKIPPED", error="SOS_WEBHOOK_URL is not set")

        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.secret:
            signature = hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256)
            headers["X-CargoResQ-Signature"] = f"sha256={signature.hexdigest()}"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(self.url, content=body, headers=headers)
            if 200 <= response.status_code < 300:
                return NotifyResult(status="SENT", ref=str(response.status_code))
            return NotifyResult(
                status="FAILED", error=f"HTTP {response.status_code}"
            )
        except Exception as exc:
            return NotifyResult(status="FAILED", error=str(exc)[:480])


class SmsNotifier:
    """SMS to a configured number.

    Deliberately unimplemented. Wiring a real gateway means an account,
    credentials, a sender id and, in India, DLT template registration -- none
    of which can be faked into existence. It reports SKIPPED with the reason
    rather than pretending a message was sent.
    """

    name = "sms"

    def supports(self, category: str, severity: str) -> bool:
        return bool(os.getenv("SOS_SMS_PROVIDER"))

    async def notify(
        self, payload: Dict[str, Any], target: NotifyTarget
    ) -> NotifyResult:
        return NotifyResult(
            status="SKIPPED",
            error=(
                "No SMS provider is configured. Set SOS_SMS_PROVIDER and the "
                "provider credentials, and implement send() for that gateway."
            ),
        )


_REGISTRY = {
    LoggingNotifier.name: LoggingNotifier,
    WebhookNotifier.name: WebhookNotifier,
    SmsNotifier.name: SmsNotifier,
}


def configured_notifiers() -> List[EmergencyNotifier]:
    """Build the adapter chain from SOS_NOTIFIERS.

    Defaults to the logging adapter so there is always exactly one honest
    record of every outbound attempt.
    """
    names = [
        n.strip()
        for n in os.getenv("SOS_NOTIFIERS", LoggingNotifier.name).split(",")
        if n.strip()
    ]
    adapters: List[EmergencyNotifier] = []
    for name in names:
        factory = _REGISTRY.get(name)
        if factory is None:
            logger.warn("unknown_sos_notifier", name=name, known=sorted(_REGISTRY))
            continue
        adapters.append(factory())
    return adapters or [LoggingNotifier()]
