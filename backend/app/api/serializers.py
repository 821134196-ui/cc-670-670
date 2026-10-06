"""ORM -> API 结构转换。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import (
    AccessEvent,
    ChecklistItem,
    Handover,
    HandoverEvent,
    Person,
    PersonRole,
    ResumeRequest,
    WorkPermit,
)
from app.services import presence
from app.services.clock import clock
from app.services.handover_service import permit_valid, qualification_valid


def person_out(p: Person, at: datetime | None = None) -> dict:
    at = at or clock.now()
    data = {
        "id": p.id,
        "username": p.username,
        "name": p.name,
        "role": p.role.value,
        "qualification_type": p.qualification_type,
        "qualification_valid_until": p.qualification_valid_until,
        "qualification_valid": None,
    }
    if p.role == PersonRole.GUARDIAN:
        data["qualification_valid"] = qualification_valid(p, at)
    return data


def checklist_out(i: ChecklistItem) -> dict:
    return {
        "id": i.id, "kind": i.kind.value, "content": i.content,
        "state": i.state.value, "seq": i.seq,
    }


def permit_out(p: WorkPermit, at: datetime | None = None) -> dict:
    at = at or clock.now()
    return {
        "id": p.id,
        "permit_no": p.permit_no,
        "title": p.title,
        "work_type": p.work_type,
        "location": p.location,
        "status": p.status.value,
        "valid_from": p.valid_from,
        "valid_until": p.valid_until,
        "valid": permit_valid(p, at),
        "work_status": p.work_status.value,
        "current_guardian": person_out(p.current_guardian, at) if p.current_guardian else None,
    }


def access_event_out(e: AccessEvent) -> dict:
    return {
        "id": e.id,
        "person_id": e.person_id,
        "person_name": e.person.name,
        "direction": e.direction.value,
        "event_time": e.event_time,
        "received_at": e.received_at,
        "delayed": e.is_delayed,
        "note": e.note,
    }


def handover_out(h: Handover, db: Session, at: datetime | None = None) -> dict:
    at = at or clock.now()
    gap = None
    if h.status.value != "RESUMED" and h.status.value != "CANCELLED":
        gap = presence.compute_gap(
            db, h.outgoing_guardian_id, h.incoming_guardian_id, h.paused_at, at
        )
        gap.pop("based_on_received_receipts_only", None)
    incoming = db.get(Person, h.incoming_guardian_id)
    permit = db.get(WorkPermit, h.permit_id)
    return {
        "id": h.id,
        "permit_id": h.permit_id,
        "outgoing_guardian": person_out(db.get(Person, h.outgoing_guardian_id), at),
        "incoming_guardian": person_out(incoming, at),
        "status": h.status.value,
        "paused_at": h.paused_at,
        "outgoing_signed_at": h.outgoing_signed_at,
        "incoming_signed_at": h.incoming_signed_at,
        "resumed_at": h.resumed_at,
        "cancelled_at": h.cancelled_at,
        "locked_gap_seconds": h.locked_gap_seconds,
        "outgoing_on_site": presence.is_on_site(db, h.outgoing_guardian_id, at),
        "incoming_on_site": presence.is_on_site(db, incoming.id, at),
        "incoming_qualified": qualification_valid(incoming, at),
        "permit_valid": permit_valid(permit, at),
        "gap": gap,
    }


def event_out(e: HandoverEvent) -> dict:
    return {
        "id": e.id,
        "type": e.type.value,
        "actor_id": e.actor_id,
        "actor_name": e.actor.name if e.actor else "系统",
        "occurred_at": e.occurred_at,
        "detail": e.detail,
        "meta": e.meta,
    }


def request_out(r: ResumeRequest, db: Session | None = None) -> dict:
    requester_name = None
    if db is not None:
        requester = db.get(Person, r.requested_by)
        requester_name = requester.name if requester else None
    return {
        "id": r.id,
        "handover_id": r.handover_id,
        "permit_id": r.permit_id,
        "requested_by": r.requested_by,
        "requested_by_name": requester_name,
        "requested_at": r.requested_at,
        "status": r.status.value,
        "decided_by": r.decided_by,
        "decided_at": r.decided_at,
        "reject_reasons": r.reject_reasons,
        "gap_seconds": r.gap_seconds,
    }
