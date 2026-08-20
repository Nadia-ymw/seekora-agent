"""行为事件队列条目与处理结果领域契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .behavior import BehaviorEvent


QueueStatus = Literal["pending", "processed", "failed"]


@dataclass(frozen=True)
class QueuedBehaviorEvent:
    event: BehaviorEvent
    late: bool
    status: QueueStatus = "pending"
    attempts: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class BehaviorIngestionResult:
    event: BehaviorEvent
    duplicate: bool
    late: bool
    queue_status: QueueStatus
    replayed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "duplicate": self.duplicate,
            "late": self.late,
            "queue_status": self.queue_status,
            "replayed": self.replayed,
            "event": self.event.as_dict(),
        }
