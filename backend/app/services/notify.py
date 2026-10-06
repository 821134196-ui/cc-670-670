"""模拟通知：落库 notifications 表，不接外部服务。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Notification
from app.services.clock import clock


def notify(db: Session, content: str, target_person_id: int | None = None,
           channel: str = "MOCK_SMS") -> Notification:
    n = Notification(
        target_person_id=target_person_id,
        channel=channel,
        content=content,
        created_at=clock.now(),
        sent=True,
    )
    db.add(n)
    db.flush()
    return n
