"""交接状态机与复工硬规则。

流程：停工 PAUSED → 原监护人签认 → 接班人签认（双方逐条核对风险/隔离）
     → 申请复工（系统硬校验）→ 班组长复核 → 复工 RESUMED

硬规则（任一不满足，系统不得允许复工）：
  R1 接班人监护资质真实且在有效期内；
  R2 作业许可处于生效状态且在有效期内；
  R3 复工当下接班人确实现场在场（只看已到达的门禁回执）；
  R4 风险与隔离措施已逐条核对、无未完成事项遗留；
  R5 两次签认必须来自两个不同账号本人，不可代签、不可重复签认；
  R6 一张许可同时只能有一个未闭环交接单，重复提交不产生第二次交接；
  R7 门禁回执延迟到达不回填历史在场判断，已锁定的空档/决策不再改写。
"""
from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import BusinessError
from app.models import (
    AccessEvent,
    AuthToken,
    ChecklistItem,
    Direction,
    EventType,
    Handover,
    HandoverEvent,
    HandoverStatus,
    ItemKind,
    ItemState,
    Notification,
    Person,
    PersonRole,
    PermitStatus,
    RequestStatus,
    ResumeRequest,
    SignNonce,
    WorkPermit,
    WorkStatus,
)
from app.services import presence
from app.services.clock import clock
from app.services.notify import notify

# ----------------------------- 认证 -----------------------------

def login(db: Session, username: str, password: str) -> AuthToken:
    person = db.execute(
        select(Person).where(Person.username == username)
    ).scalar_one_or_none()
    if person is None or person.password != password or not person.is_active:
        raise BusinessError("BAD_CREDENTIALS", "用户名或密码错误", 401)
    token = AuthToken(
        token=secrets.token_hex(24), person_id=person.id, created_at=clock.now()
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token


def qualification_valid(person: Person, at: datetime) -> bool:
    return (
        person.role == PersonRole.GUARDIAN
        and bool(person.qualification_type)
        and person.qualification_valid_until is not None
        and person.qualification_valid_until > at
    )


def permit_valid(permit: WorkPermit, at: datetime) -> bool:
    return (
        permit.status == PermitStatus.ACTIVE
        and permit.valid_from <= at < permit.valid_until
    )


def _add_event(
    db: Session,
    handover: Handover,
    etype: EventType,
    actor: Person | None,
    detail: str | None = None,
    meta: dict | None = None,
    occurred_at: datetime | None = None,
) -> HandoverEvent:
    ev = HandoverEvent(
        handover_id=handover.id,
        permit_id=handover.permit_id,
        type=etype,
        actor_id=actor.id if actor else None,
        occurred_at=occurred_at or clock.now(),
        detail=detail,
        meta=meta,
    )
    db.add(ev)
    db.flush()
    return ev


# ----------------------------- 停工 / 发起交接 -----------------------------

def pause_for_handover(
    db: Session, permit_id: int, incoming_guardian_id: int, actor: Person
) -> Handover:
    permit = db.get(WorkPermit, permit_id)
    if permit is None:
        raise BusinessError("PERMIT_NOT_FOUND", "作业许可不存在", 404)

    existing = db.execute(
        select(Handover).where(
            Handover.permit_id == permit.id,
            Handover.status.in_(
                [HandoverStatus.PENDING,
                 HandoverStatus.OUTGOING_SIGNED,
                 HandoverStatus.FULLY_SIGNED]
            ),
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise BusinessError(
            "HANDOVER_ALREADY_OPEN", "该作业已有进行中的交接单，不能重复发起", 409
        )

    if permit.work_status != WorkStatus.IN_PROGRESS:
        raise BusinessError(
            "WORK_NOT_IN_PROGRESS",
            f"作业当前状态为 {permit.work_status.value}，必须处于作业中才能停工交接",
        )
    if permit.current_guardian_id is None:
        raise BusinessError("NO_CURRENT_GUARDIAN", "该作业没有在岗监护人，无法交接")

    incoming = db.get(Person, incoming_guardian_id)
    if incoming is None or not incoming.is_active or incoming.role != PersonRole.GUARDIAN:
        raise BusinessError("INCOMING_NOT_GUARDIAN", "接班人必须是在职监护人")
    if incoming.id == permit.current_guardian_id:
        raise BusinessError("SAME_PERSON", "接班人不能与当前监护人为同一人")

    outgoing = db.get(Person, permit.current_guardian_id)
    now = clock.now()
    handover = Handover(
        permit_id=permit.id,
        outgoing_guardian_id=outgoing.id,
        incoming_guardian_id=incoming.id,
        status=HandoverStatus.PENDING,
        paused_at=now,
    )
    db.add(handover)
    try:
        db.flush()  # 触发 uq_open_handover_per_permit
    except IntegrityError:
        db.rollback()
        raise BusinessError(
            "HANDOVER_ALREADY_OPEN", "该作业已有进行中的交接单，不能重复发起", 409
        )

    permit.work_status = WorkStatus.PAUSED
    _add_event(
        db, handover, EventType.WORK_PAUSED, actor,
        detail=f"作业停工，开始监护人交接：{outgoing.name} → {incoming.name}",
    )
    notify(db, f"许可 {permit.permit_no} 已停工，等待监护人交接签认", target_person_id=outgoing.id)
    notify(db, f"您被指定为许可 {permit.permit_no} 的接班监护人，请到场核对并签认",
           target_person_id=incoming.id)
    db.commit()
    db.refresh(handover)
    return handover


# ----------------------------- 门禁回执 -----------------------------

def ingest_access_event(
    db: Session,
    person_id: int,
    direction: Direction,
    event_time: datetime,
    delay_seconds: float,
    note: str | None,
) -> AccessEvent:
    person = db.get(Person, person_id)
    if person is None:
        raise BusinessError("PERSON_NOT_FOUND", "人员不存在", 404)
    if event_time > clock.now():
        raise BusinessError("FUTURE_EVENT", "过闸时间不能晚于当前时间")

    received_at = clock.now()
    delayed = delay_seconds > 0
    if delayed:
        # event_time 是物理过闸时刻（可在过去），此刻回执才到达系统
        note = (note + "；" if note else "") + f"回执延迟约 {int(delay_seconds)} 秒到达"

    dup = db.execute(
        select(AccessEvent).where(
            AccessEvent.person_id == person_id,
            AccessEvent.event_time == event_time,
            AccessEvent.direction == direction,
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise BusinessError("DUPLICATE_ACCESS_EVENT", "相同门禁回执已存在，请勿重复上报", 409)

    ev = AccessEvent(
        person_id=person_id,
        direction=direction,
        event_time=event_time,
        received_at=received_at,
        is_delayed=delayed,
        note=note,
    )
    db.add(ev)
    db.flush()

    # 自动挂到相关交接单时间线上（occurred_at 取物理过闸时间，标注回执迟到事实）
    if person.role == PersonRole.GUARDIAN:
        ho = db.execute(
            select(Handover)
            .where(Handover.paused_at <= event_time)
            .order_by(Handover.paused_at.desc())
        ).scalars().first()
        if ho is not None and person_id in (ho.outgoing_guardian_id, ho.incoming_guardian_id):
            etype = (
                EventType.GUARDIAN_DEPARTED
                if direction == Direction.OUT else EventType.GUARDIAN_ARRIVED
            )
            already = db.execute(
                select(HandoverEvent).where(
                    HandoverEvent.handover_id == ho.id,
                    HandoverEvent.type == etype,
                    HandoverEvent.actor_id == person_id,
                )
            ).scalar_one_or_none()
            if already is None:
                _add_event(
                    db, ho, etype, person,
                    occurred_at=event_time,
                    detail=("原监护人离场" if direction == Direction.OUT else "接班人到场")
                    + ("（门禁回执延迟到达）" if delayed else ""),
                    meta={"received_at": received_at.isoformat(),
                          "delayed": delayed},
                )
    db.commit()
    db.refresh(ev)
    return ev


# ----------------------------- 签认 -----------------------------

def _required_items(db: Session, permit_id: int) -> list[ChecklistItem]:
    return list(db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.permit_id == permit_id)
        .order_by(ChecklistItem.seq, ChecklistItem.id)
    ).scalars().all())


def _verify_item_checks(items: list[ChecklistItem], checks: dict[str, bool]) -> None:
    must_confirms = [i for i in items if i.kind in (ItemKind.RISK, ItemKind.ISOLATION)]
    missing = [str(i.id) for i in must_confirms if not checks.get(str(i.id))]
    if missing:
        raise BusinessError(
            "ITEMS_NOT_CONFIRMED",
            "风险与隔离措施必须逐条核对确认后才能签认",
            details={"unconfirmed_item_ids": missing},
        )
    unknown = [k for k in checks if k not in {str(i.id) for i in items}]
    if unknown:
        raise BusinessError("UNKNOWN_ITEM", f"未知核对项：{unknown}")


def sign_handover(
    db: Session,
    handover_id: int,
    signer: Person,
    role: str,
    item_checks: dict[str, bool],
    nonce: str | None,
) -> Handover:
    handover = db.get(Handover, handover_id)
    if handover is None:
        raise BusinessError("HANDOVER_NOT_FOUND", "交接单不存在", 404)

    # 幂等：同一 nonce 重放，直接返回首次结果，绝不产生第二条签认
    if nonce:
        seen = db.get(SignNonce, nonce)
        if seen is not None:
            return db.get(Handover, seen.response_handover_id)

    expected_id = (
        handover.outgoing_guardian_id if role == "OUTGOING"
        else handover.incoming_guardian_id
    )
    # R5：令牌本人必须就是交接单上的该方——不同人、不同账号，禁止代签
    if signer.id != expected_id:
        raise BusinessError(
            "NOT_SIGNING_PARTY",
            "只能由交接单上的本人账号签认，禁止共用账号或代签",
            403,
        )

    now = clock.now()

    if role == "OUTGOING":
        if handover.outgoing_signed_at is not None:
            raise BusinessError("ALREADY_SIGNED", "原监护人已完成签认，请勿重复提交", 409)
        if handover.status != HandoverStatus.PENDING:
            raise BusinessError("INVALID_HANDOVER_STATE", "交接单状态不允许原监护人签认", 409)
        if not presence.is_on_site(db, signer.id, now):
            raise BusinessError(
                "SIGNER_NOT_ON_SITE", "原监护人签认时必须在作业现场（门禁无入场记录）", 403
            )
        items = _required_items(db, handover.permit_id)
        _verify_item_checks(items, item_checks)
        handover.outgoing_signed_at = now
        handover.outgoing_item_checks = item_checks
        handover.status = HandoverStatus.OUTGOING_SIGNED
        _add_event(db, handover, EventType.OUTGOING_SIGNED, signer,
                   detail="原监护人已核对风险与隔离措施并签认")
        notify(db, "原监护人已完成交接签认，请接班人核对签认",
               target_person_id=handover.incoming_guardian_id)

    else:  # INCOMING
        if handover.incoming_signed_at is not None:
            raise BusinessError("ALREADY_SIGNED", "接班人已完成签认，请勿重复提交", 409)
        if handover.status != HandoverStatus.OUTGOING_SIGNED:
            raise BusinessError(
                "INVALID_HANDOVER_STATE",
                "需由原监护人先签认，接班人才能签认", 409,
            )
        if signer.id == handover.outgoing_guardian_id:
            # 数据库约束 + 交接单已保证不同人，双保险
            raise BusinessError("SAME_PERSON", "不能由同一人完成双方签认", 403)
        if not presence.is_on_site(db, signer.id, now):
            raise BusinessError(
                "SIGNER_NOT_ON_SITE", "接班人签认时必须在作业现场（门禁无入场记录）", 403
            )
        items = _required_items(db, handover.permit_id)
        _verify_item_checks(items, item_checks)
        handover.incoming_signed_at = now
        handover.incoming_item_checks = item_checks
        handover.status = HandoverStatus.FULLY_SIGNED
        _add_event(db, handover, EventType.INCOMING_SIGNED, signer,
                   detail="接班人已到场核对风险与隔离措施并签认")
        notify(db, "双方已完成交接签认，可申请复工",
               target_person_id=handover.outgoing_guardian_id)

    if nonce:
        db.add(SignNonce(
            nonce=nonce, handover_id=handover.id, role=role,
            created_at=now, response_handover_id=handover.id,
        ))
    db.commit()
    db.refresh(handover)
    return handover


# ----------------------------- 复工 -----------------------------

def _resume_checks(db: Session, handover: Handover, at: datetime) -> list[str]:
    """返回所有不通过原因；空列表表示可以复工。"""
    reasons: list[str] = []
    incoming = db.get(Person, handover.incoming_guardian_id)
    permit = db.get(WorkPermit, handover.permit_id)

    if not qualification_valid(incoming, at):
        if incoming.qualification_valid_until is None:
            reasons.append("接班人无监护资质记录")
        elif incoming.qualification_valid_until <= at:
            reasons.append(
                f"接班人监护资质已于 {incoming.qualification_valid_until:%Y-%m-%d %H:%M} 过期"
            )
        else:
            reasons.append("接班人资质不符")
    if not permit_valid(permit, at):
        reasons.append(f"作业许可 {permit.permit_no} 已过期或已关闭")
    if not presence.is_on_site(db, incoming.id, at):
        reasons.append("现场无监护人在场（门禁无接班人有效入场记录）")
    open_items = db.execute(
        select(ChecklistItem).where(
            ChecklistItem.permit_id == permit.id,
            ChecklistItem.state == ItemState.OPEN,
        )
    ).scalars().all()
    if open_items:
        reasons.append("存在未完成事项：" + "、".join(i.content for i in open_items))
    return reasons


def request_resume(db: Session, handover_id: int, requester: Person) -> ResumeRequest:
    handover = db.get(Handover, handover_id)
    if handover is None:
        raise BusinessError("HANDOVER_NOT_FOUND", "交接单不存在", 404)
    if handover.status != HandoverStatus.FULLY_SIGNED:
        raise BusinessError(
            "NOT_FULLY_SIGNED", "须双方签认完成后才能申请复工", 409
        )
    now = clock.now()
    gap = presence.compute_gap(
        db, handover.outgoing_guardian_id, handover.incoming_guardian_id,
        handover.paused_at, now,
    )
    reasons = _resume_checks(db, handover, now)

    req = ResumeRequest(
        handover_id=handover.id,
        permit_id=handover.permit_id,
        requested_by=requester.id,
        requested_at=now,
        gap_seconds=gap["gap_seconds"],
        reject_reasons=reasons or None,
        status=RequestStatus.REJECTED if reasons else RequestStatus.PENDING,
    )
    db.add(req)
    db.flush()
    _add_event(
        db, handover, EventType.RESUME_REQUESTED, requester,
        detail=f"申请复工，无人监护时段已持续 {gap['gap_seconds']} 秒"
               + ("（空档仍在持续）" if gap["ongoing"] else ""),
        meta={"gap": {k: (v.isoformat() if isinstance(v, datetime) else v)
                       for k, v in gap.items() if k != "based_on_received_receipts_only"}},
    )
    if reasons:
        _add_event(
            db, handover, EventType.RESUME_REJECTED, None,
            detail="系统复核不通过：" + "；".join(reasons),
            meta={"auto": True},
        )
        notify(db, f"复工申请被系统拦截：{'；'.join(reasons)}",
               target_person_id=handover.incoming_guardian_id)
    else:
        leaders = db.execute(
            select(Person).where(Person.role == PersonRole.LEADER, Person.is_active)
        ).scalars().all()
        for leader in leaders:
            notify(db, f"许可复工申请待审批，无人监护时段 {gap['gap_seconds']} 秒",
                   target_person_id=leader.id)
    db.commit()
    db.refresh(req)
    return req


def decide_resume(
    db: Session, request_id: int, leader: Person, approve: bool, comment: str | None
) -> ResumeRequest:
    if leader.role != PersonRole.LEADER:
        raise BusinessError("FORBIDDEN", "只有班组长可以审批复工", 403)
    req = db.get(ResumeRequest, request_id)
    if req is None:
        raise BusinessError("REQUEST_NOT_FOUND", "复工申请不存在", 404)
    if req.status != RequestStatus.PENDING:
        raise BusinessError("REQUEST_DECIDED", "该复工申请已有结论，不可重复审批", 409)

    handover = db.get(Handover, req.handover_id)
    now = clock.now()

    if approve:
        # 班组长批准瞬间再次硬校验：申请之后可能许可到期、接班人离场
        reasons = _resume_checks(db, handover, now)
        if reasons:
            req.status = RequestStatus.REJECTED
            req.reject_reasons = reasons
            req.decided_by = leader.id
            req.decided_at = now
            _add_event(
                db, handover, EventType.RESUME_REJECTED, leader,
                detail="批准时系统复核仍不通过：" + "；".join(reasons)
                       + (f"（{comment}）" if comment else ""),
            )
            db.commit()
            db.refresh(req)
            return req

        permit = db.get(WorkPermit, handover.permit_id)
        gap = presence.compute_gap(
            db, handover.outgoing_guardian_id, handover.incoming_guardian_id,
            handover.paused_at, now,
        )
        # R7：锁定空档快照，之后迟到的门禁回执不得改写
        handover.locked_gap_seconds = gap["gap_seconds"]
        handover.status = HandoverStatus.RESUMED
        handover.resumed_at = now
        permit.work_status = WorkStatus.IN_PROGRESS
        permit.current_guardian_id = handover.incoming_guardian_id
        req.status = RequestStatus.APPROVED
        req.decided_by = leader.id
        req.decided_at = now
        req.gap_seconds = gap["gap_seconds"]
        _add_event(
            db, handover, EventType.WORK_RESUMED, leader,
            detail=f"班组长批准复工，接班人 {db.get(Person, handover.incoming_guardian_id).name} 在岗；"
                   f"本次无人监护时段 {gap['gap_seconds']} 秒"
                   + (f"（{comment}）" if comment else ""),
            meta={"locked_gap_seconds": gap["gap_seconds"]},
        )
        notify(db, f"许可 {permit.permit_no} 已复工", target_person_id=handover.incoming_guardian_id)
    else:
        req.status = RequestStatus.REJECTED
        req.decided_by = leader.id
        req.decided_at = now
        reason_list = [comment] if comment else ["班组长驳回"]
        req.reject_reasons = reason_list
        _add_event(db, handover, EventType.RESUME_REJECTED, leader,
                   detail="班组长驳回复工：" + (comment or "无说明"))

    db.commit()
    db.refresh(req)
    return req


def cancel_handover(db: Session, handover_id: int, leader: Person) -> Handover:
    """班组长作废交接单（如选错接班人），作业回到作业中需另发交接。"""
    if leader.role != PersonRole.LEADER:
        raise BusinessError("FORBIDDEN", "只有班组长可以作废交接单", 403)
    handover = db.get(Handover, handover_id)
    if handover is None:
        raise BusinessError("HANDOVER_NOT_FOUND", "交接单不存在", 404)
    if handover.status == HandoverStatus.RESUMED:
        raise BusinessError("ALREADY_RESUMED", "已复工的交接单不能作废", 409)
    handover.status = HandoverStatus.CANCELLED
    handover.cancelled_at = clock.now()
    permit = db.get(WorkPermit, handover.permit_id)
    permit.work_status = WorkStatus.IN_PROGRESS
    db.commit()
    db.refresh(handover)
    return handover
