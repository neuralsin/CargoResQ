"""
Standardized Event Schema Envelope (Phase 18.2).
Every event across Kafka and internal buses follows this shape.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import uuid
from typing import Any, Dict


@dataclass
class EventEnvelope:
    eventType: str
    payload: Dict[str, Any]
    producer: str = "core-api"
    schemaVersion: int = 2
    eventId: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventEnvelope":
        return cls(
            eventType=data["eventType"],
            payload=data.get("payload", {}),
            producer=data.get("producer", "unknown"),
            schemaVersion=data.get("schemaVersion", 2),
            eventId=data.get("eventId", f"evt_{uuid.uuid4().hex[:8]}"),
            timestamp=data.get(
                "timestamp", datetime.now(timezone.utc).isoformat()
            ),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "EventEnvelope":
        return cls.from_dict(json.loads(json_str))
