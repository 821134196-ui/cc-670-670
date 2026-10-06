"""HTTP 路由：认证、人员、许可、交接、签认、门禁、复工、时间线、班组长视图。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as ser
from app.api.deps import current_person, require_leader
from app.database import get_db
from app.models import (
    AccessEvent,
    ChecklistItem,
    Direction,
    Handover,
    HandoverEvent,
    HandoverStatus,
    Notification,
    Person,
    PersonRole,
    RequestStatus,
    ResumeRequest,
    WorkPermit,
    WorkStatus,
)
from app.schemas import (
    AccessEventIn,
    HandoverPage,
    LeaderOverview,
    LoginIn,
    PauseIn,
    ResumeDecisionIn,
    SignIn,
    TimelineOut,
)
from app.services import handover_service as svc
from app.services import presence
from app.services.clock import clock

router = APIRouter(prefix="/api")


# ----------------------------- 认证 / 人员 -----------------------------

@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    token = svc.login(db, body.username, body.password)
    p = token.person
    return {
        "token": token.token,
        "person_id": p.id,
        "name": p.name,
        "role": p.role.value,
        "qualification_type": p.qualification_type,
        "qualification_valid_until": p.qualification_valid_until,
    }


@router.get("/persons")
def list_persons(db: Session = Depends(get_db), _: Person = Depends(current_person)):
    rows = db.execute(
        select(Person).where(Person.is_active).order_by(Person.role, Person.id)
    ).scalars().all()
    return [ser.person_out(p) for p in rows]


@router.get("/persons/guardians")
def list_guardians(db: Session = Depends(get_db), _: Person = Depends(current_person)):
    rows = db.execute(
        select(Person).where(
            Person.is_active, Person.role == PersonRole.GUARDIAN
        ).order_by(Person.id)
    ).scalars().all()
    return [ser.person_out(p) for p in rows]


# ----------------------------- 许可 -----------------------------

@router.get("/permits")
def list_permits(db: Session = Depends(get_db), _: Person = Depends(current_person)):
    rows = db.execute(select(WorkPermit).order_by(WorkPermit.id)).scalars().all()
    return [ser.permit_out(p) for p in rows]


@router.get("/permits/{permit_id}/handover-page", response_model=HandoverPage)
def handover_page(
    permit_id: int, db: Session = Depends(get_db), me: Person = Depends(current_person)
):
    permit = db.get(WorkPermit, permit_id)
    if permit is None:
        from app.errors import BusinessError
        raise BusinessError("PERMIT_NOT_FOUND", "作业许可不存在", 404)

    items = db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.permit_id == permit_id)
        .order_by(ChecklistItem.seq, ChecklistItem.id)
    ).scalars().all()

    open_ho = db.execute(
        select(Handover)
        .where(
            Handover.permit_id == permit_id,
            Handover.status.in_([
                HandoverStatus.PENDING,
                HandoverStatus.OUTGOING_SIGNED,
                HandoverStatus.FULLY_SIGNED,
            ]),
        )
        .order_by(Handover.id.desc())
    ).scalars().first()

    # 与该许可两位监护人相关的近期门禁回执（含迟到回执，页面可显式看到延迟）
    guard_ids = [permit.current_guardian_id] if permit.current_guardian_id else []
    if open_ho is not None:
        guard_ids += [open_ho.outgoing_guardian_id, open_ho.incoming_guardian_id]
    guard_ids = [g for g in dict.fromkeys(guard_ids) if g]
    events: list[AccessEvent] = []
    if guard_ids:
        events = list(db.execute(
            select(AccessEvent)
            .where(AccessEvent.person_id.in_(guard_ids))
            .order_by(AccessEvent.event_time.desc())
            .limit(30)
        ).scalars().all())

    recent_notes = [
        n.content for n in db.execute(
            select(Notification).order_by(Notification.id.desc()).limit(10)
        ).scalars().all()
    ]

    return {
        "permit": ser.permit_out(permit),
        "items": [ser.checklist_out(i) for i in items],
        "open_handover": ser.handover_out(open_ho, db) if open_ho else None,
        "access_events": [ser.access_event_out(e) for e in events],
        "notifications": recent_notes,
    }


# ----------------------------- 停工 / 交接 / 签认 -----------------------------

@router.post("/permits/{permit_id}/pause", status_code=201)
def pause(
    permit_id: int, body: PauseIn,
    db: Session = Depends(get_db), me: Person = Depends(current_person),
):
    ho = svc.pause_for_handover(db, permit_id, body.incoming_guardian_id, me)
    return ser.handover_out(ho, db)


@router.get("/handovers/{handover_id}")
def get_handover(
    handover_id: int, db: Session = Depends(get_db), _: Person = Depends(current_person)
):
    ho = db.get(Handover, handover_id)
    if ho is None:
        from app.errors import BusinessError
        raise BusinessError("HANDOVER_NOT_FOUND", "交接单不存在", 404)
    return ser.handover_out(ho, db)


@router.post("/handovers/{handover_id}/sign")
def sign(
    handover_id: int, body: SignIn,
    db: Session = Depends(get_db), me: Person = Depends(current_person),
):
    ho = svc.sign_handover(
        db, handover_id, me, body.role, body.item_checks, body.nonce
    )
    return ser.handover_out(ho, db)


# ----------------------------- 清单事项 -----------------------------

@router.post("/checklist-items/{item_id}/confirm")
def confirm_item(
    item_id: int,
    db: Session = Depends(get_db),
    me: Person = Depends(current_person),
):
    from app.models import ItemState
    item = db.get(ChecklistItem, item_id)
    if item is None:
        from app.errors import BusinessError
        raise BusinessError("ITEM_NOT_FOUND", "核对项不存在", 404)
    item.state = ItemState.CONFIRMED
    db.commit()
    return ser.checklist_out(item)


# ----------------------------- 门禁模拟 -----------------------------

@router.post("/access-events", status_code=201)
def post_access_event(
    body: AccessEventIn,
    db: Session = Depends(get_db),
    _: Person = Depends(current_person),
):
    ev = svc.ingest_access_event(
        db, body.person_id, Direction(body.direction),
        body.event_time, body.delay_seconds, body.note,
    )
    return ser.access_event_out(ev)


@router.get("/access-events")
def list_access_events(
    person_id: int | None = None,
    db: Session = Depends(get_db),
    _: Person = Depends(current_person),
):
    stmt = select(AccessEvent).order_by(AccessEvent.event_time.desc()).limit(100)
    if person_id is not None:
        stmt = (
            select(AccessEvent)
            .where(AccessEvent.person_id == person_id)
            .order_by(AccessEvent.event_time.desc())
            .limit(100)
        )
    return [ser.access_event_out(e) for e in db.execute(stmt).scalars().all()]


# ----------------------------- 复工 -----------------------------

@router.post("/handovers/{handover_id}/request-resume", status_code=201)
def request_resume(
    handover_id: int,
    db: Session = Depends(get_db),
    me: Person = Depends(current_person),
):
    req = svc.request_resume(db, handover_id, me)
    return ser.request_out(req, db)


@router.post("/resume-requests/{request_id}/decision")
def decide(
    request_id: int, body: ResumeDecisionIn,
    db: Session = Depends(get_db), leader: Person = Depends(require_leader),
):
    req = svc.decide_resume(db, request_id, leader, body.approve, body.comment)
    return ser.request_out(req, db)


# ----------------------------- 时间线 -----------------------------

@router.get("/handovers/{handover_id}/timeline", response_model=TimelineOut)
def timeline(
    handover_id: int, db: Session = Depends(get_db), _: Person = Depends(current_person)
):
    ho = db.get(Handover, handover_id)
    if ho is None:
        from app.errors import BusinessError
        raise BusinessError("HANDOVER_NOT_FOUND", "交接单不存在", 404)
    events = db.execute(
        select(HandoverEvent)
        .where(HandoverEvent.handover_id == handover_id)
        .order_by(HandoverEvent.occurred_at, HandoverEvent.id)
    ).scalars().all()
    return {"handover_id": handover_id, "events": [ser.event_out(e) for e in events]}


@router.get("/permits/{permit_id}/handovers")
def permit_handovers(
    permit_id: int, db: Session = Depends(get_db), _: Person = Depends(current_person)
):
    rows = db.execute(
        select(Handover)
        .where(Handover.permit_id == permit_id)
        .order_by(Handover.id)
    ).scalars().all()
    return [
        {
            "handover": ser.handover_out(h, db),
            "timeline": [
                ser.event_out(e) for e in sorted(h.events, key=lambda x: (x.occurred_at, x.id))
            ],
            "requests": [ser.request_out(r, db) for r in h.resume_requests],
        }
        for h in rows
    ]


# ----------------------------- 班组长视图 -----------------------------

@router.get("/leader/overview", response_model=LeaderOverview)
def leader_overview(db: Session = Depends(get_db), _: Person = Depends(require_leader)):
    now = clock.now()
    pending = db.execute(
        select(ResumeRequest)
        .where(ResumeRequest.status == RequestStatus.PENDING)
        .order_by(ResumeRequest.requested_at)
    ).scalars().all()

    # 所有未闭环交接单的实时空档
    open_hos = db.execute(
        select(Handover).where(
            Handover.status.in_([
                HandoverStatus.PENDING,
                HandoverStatus.OUTGOING_SIGNED,
                HandoverStatus.FULLY_SIGNED,
            ])
        )
    ).scalars().all()
    active_gaps = []
    for h in open_hos:
        gap = presence.compute_gap(
            db, h.outgoing_guardian_id, h.incoming_guardian_id, h.paused_at, now
        )
        active_gaps.append({
            "permit_id": h.permit_id,
            "handover_id": h.id,
            "outgoing_name": db.get(Person, h.outgoing_guardian_id).name,
            "incoming_name": db.get(Person, h.incoming_guardian_id).name,
            "status": h.status.value,
            **{k: v for k, v in gap.items() if k != "based_on_received_receipts_only"},
        })

    permits = db.execute(select(WorkPermit).order_by(WorkPermit.id)).scalars().all()
    return {
        "pending_requests": [ser.request_out(r, db) for r in pending],
        "active_gaps": active_gaps,
        "permits": [ser.permit_out(p) for p in permits],
    }


@router.get("/notifications")
def list_notifications(db: Session = Depends(get_db), _: Person = Depends(current_person)):
    rows = db.execute(
        select(Notification).order_by(Notification.id.desc()).limit(50)
    ).scalars().all()
    return [
        {
            "id": n.id,
            "target_person_id": n.target_person_id,
            "channel": n.channel,
            "content": n.content,
            "created_at": n.created_at,
            "sent": n.sent,
        }
        for n in rows
    ]
