"""核心业务规则：在场判定、交接、签认、复工校验。

设计要点
--------
1. 在场时间只能由门禁事件的“真实发生时间(occurred_at)”推导，回执到达时间
   (received_at) 不参与，也绝不臆造未发生的出入事件——延迟回执不能回填在场。
2. 每次作业同时只允许一条生效交接；同一角色重复签认幂等返回，不产生第二条。
3. 复工三不原则：接班人资质不符 / 许可过期 / 现场无人监护，一律拒绝复工。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, timeutil


class BusinessError(Exception):
    """业务规则拒绝（HTTP 层映射为 4xx）。"""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ─────────────────────────── 在场判定 ───────────────────────────

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def presence_intervals(
    db: Session, person_id: int, location: str
) -> list[tuple[datetime, datetime | None]]:
    """依据门禁事件还原在场区间 [(进入, 离开|None 表示仍在现场), ...]。

    规则：
    - 严格按 occurred_at 排序，与 received_at 无关（延迟回执不改变事实）。
    - 只有 out 没有匹配的 in：不臆造进入时间，该 out 之前一律视为不在场。
    - 连续重复 in：以最早的 in 为区间起点；重复 out 结束当前区间。
    """
    events = db.scalars(
        select(models.AccessEvent)
        .where(
            models.AccessEvent.person_id == person_id,
            models.AccessEvent.location == location,
        )
        .order_by(models.AccessEvent.occurred_at, models.AccessEvent.id)
    ).all()

    intervals: list[tuple[datetime, datetime | None]] = []
    start: datetime | None = None
    for ev in events:
        at = _aware(ev.occurred_at)
        if ev.direction == "in":
            if start is None:
                start = at
            # 已在区间内的重复 in 不重置（保留最早在场时间）。
        elif ev.direction == "out":
            if start is not None:
                intervals.append((start, at))
                start = None
            # 孤立的 out：忽略，绝不据此补造一段在场。
    if start is not None:
        intervals.append((start, None))
    return intervals


def is_present(db: Session, person_id: int, location: str, at: datetime | None = None) -> bool:
    """指定时刻人是否在现场（闭区间；仍在现场的区间延伸到未来判定点）。"""
    at = _aware(at or timeutil.now())
    for lo, hi in presence_intervals(db, person_id, location):
        if lo <= at and (hi is None or at <= hi):
            return True
    return False


# ─────────────────────────── 交接空档 ───────────────────────────

@dataclass
class Gap:
    start: datetime | None
    end: datetime | None
    seconds: float
    open: bool  # 空档是否仍在持续（接班人尚未到场）


def unattended_gap(db: Session, h: models.Handover, at: datetime | None = None) -> Gap:
    """计算本次交接的无人监护时间段。

    自停工(pause_time)起：
    - 原监护人刷离(out)视为离场；接班人刷入(in)视为到场。
    - 离场早于到场 → 中间即无人监护空档；接班人尚未到场则空档持续到当前。
    - 原监护人始终未离场（与接班人当面交接、重叠在场）→ 无空档。
    """
    at = _aware(at or timeutil.now())
    loc = h.job.location
    pause = _aware(h.pause_time)

    dep = db.scalar(
        select(models.AccessEvent.occurred_at)
        .where(
            models.AccessEvent.person_id == h.outgoing_id,
            models.AccessEvent.location == loc,
            models.AccessEvent.direction == "out",
            models.AccessEvent.occurred_at >= pause,
        )
        .order_by(models.AccessEvent.occurred_at)
        .limit(1)
    )
    if dep is not None:
        dep = _aware(dep)

    arr = db.scalar(
        select(models.AccessEvent.occurred_at)
        .where(
            models.AccessEvent.person_id == h.incoming_id,
            models.AccessEvent.location == loc,
            models.AccessEvent.direction == "in",
            models.AccessEvent.occurred_at >= pause,
        )
        .order_by(models.AccessEvent.occurred_at)
        .limit(1)
    )
    if arr is not None:
        arr = _aware(arr)

    if dep is None:
        return Gap(None, None, 0.0, False)

    end = arr or at
    if arr is not None and arr <= dep:
        return Gap(None, None, 0.0, False)  # 接班人先到/重叠在场，无空档

    seconds = max(0.0, (end - dep).total_seconds())
    return Gap(dep, arr, seconds, open=arr is None and at >= dep)


# ─────────────────────────── 交接流程 ───────────────────────────

def get_active_handover(db: Session, job_id: int) -> models.Handover | None:
    return db.scalar(
        select(models.Handover).where(
            models.Handover.job_id == job_id, models.Handover.active == 1
        )
    )


def _log(db, job_id, kind, message, actor_login=None, handover_id=None, at=None):
    db.add(
        models.TimelineEvent(
            job_id=job_id,
            handover_id=handover_id,
            kind=kind,
            message=message,
            actor_login=actor_login,
            at=at or timeutil.now(),
        )
    )


def _notify(db, target_role, title, body):
    db.add(models.Notification(target_role=target_role, title=title, body=body))


def start_handover(
    db: Session, job: models.Job, outgoing: models.Person, incoming: models.Person
) -> models.Handover:
    # 先判断是否已有生效交接：重复提交直接冲突，不产生第二条。
    if get_active_handover(db, job.id) is not None:
        raise BusinessError(
            "handover_exists", "该作业已有进行中的交接，重复提交不会产生第二次交接。", 409
        )
    if job.status not in ("running", "resumed"):
        raise BusinessError("job_not_running", "作业当前不在进行中，无法发起交接停工。")
    if outgoing.id != job.guardian_id:
        raise BusinessError("not_guardian", "只有当前监护人可以作为交班人发起交接。", 403)
    if incoming.id == outgoing.id:
        raise BusinessError(
            "same_person", "交班人与接班人不能为同一人，不得共用账号或自行接班。", 403
        )
    if incoming.role != "guardian":
        raise BusinessError("incoming_role", "接班人必须是具备监护岗位的人员。")

    h = models.Handover(
        job_id=job.id,
        outgoing_id=outgoing.id,
        incoming_id=incoming.id,
        pause_time=timeutil.now(),
        status="signing",
        active=1,
    )
    db.add(h)
    db.flush()
    job.status = "paused"
    _log(
        db,
        job.id,
        "paused",
        f"作业停工，开始监护人交接：{outgoing.name} → {incoming.name}。",
        actor_login=outgoing.login,
        handover_id=h.id,
    )
    _notify(db, "leader", "作业停工交接", f"作业「{job.name}」已停工，等待交接与复工申请。")
    db.commit()
    db.refresh(h)
    return h


def sign_off(
    db: Session,
    h: models.Handover,
    role: str,
    signer: models.Person,
    risks_confirmed: bool,
    isolations_confirmed: bool,
) -> models.SignOff:
    if role not in ("outgoing", "incoming"):
        raise BusinessError("bad_role", "签认角色必须是 outgoing 或 incoming。")
    if h.status in ("resumed", "rejected", "cancelled"):
        raise BusinessError("handover_closed", "该交接已结束，不能再签认。")

    expected_id = h.outgoing_id if role == "outgoing" else h.incoming_id
    if signer.id != expected_id:
        # 含代签：用 A 的账号替 B 签、或同一账号试图签两个角色。
        raise BusinessError(
            "signer_mismatch",
            "签认人身份与角色不符：必须由本人账号签认，禁止共用账号或代签。",
            403,
        )
    if not risks_confirmed or not isolations_confirmed:
        raise BusinessError("not_confirmed", "必须核对并确认风险与隔离措施后才能签认。")

    loc = h.job.location
    if not is_present(db, signer.id, loc):
        raise BusinessError(
            "signer_absent",
            f"{signer.name} 当前不在作业现场（{loc}），现场刷卡记录不支持签认。",
            403,
        )

    existing = db.scalar(
        select(models.SignOff).where(
            models.SignOff.handover_id == h.id, models.SignOff.role == role
        )
    )
    if existing is not None:
        if existing.person_id != signer.id:
            raise BusinessError("role_taken", "该角色已由他人签认，不能顶替。", 409)
        # 幂等：本人重复提交直接返回原记录，保留首次签认时间。
        return existing

    s = models.SignOff(
        handover_id=h.id,
        person_id=signer.id,
        login=signer.login,
        role=role,
        risks_confirmed=True,
        isolations_confirmed=True,
    )
    db.add(s)
    label = "原监护人" if role == "outgoing" else "接班人"
    _log(
        db, h.job_id, "signed",
        f"{label}{signer.name} 已核对风险与隔离措施并签认。",
        actor_login=signer.login, handover_id=h.id,
    )
    db.flush()

    roles = set(db.scalars(
        select(models.SignOff.role).where(models.SignOff.handover_id == h.id)
    ).all())
    if {"outgoing", "incoming"} <= roles:
        h.status = "completed"
        h.complete_time = timeutil.now()
        gap = unattended_gap(db, h)
        h.unattended_seconds = gap.seconds
        _log(db, h.job_id, "completed",
             "双方签认完成，交接成立，可申请复工。", handover_id=h.id)
        _notify(db, "leader", "交接待复工",
                f"作业「{h.job.name}」双方已签认，请复核空档与复工申请。")
    db.commit()
    db.refresh(s)
    return s


def record_access(
    db: Session, person: models.Person, direction: str, location: str,
    occurred_at: datetime | None = None,
) -> models.AccessEvent:
    if direction not in ("in", "out"):
        raise BusinessError("bad_direction", "门禁方向只能是 in 或 out。")
    occurred_at = _aware(occurred_at or timeutil.now())
    # received_at 恒为服务器当前时间；延迟回执时 occurred_at 早于它。
    ev = models.AccessEvent(
        person_id=person.id,
        location=location,
        direction=direction,
        occurred_at=occurred_at,
        received_at=timeutil.now(),
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    # 依据真实刷卡时间为相关活跃交接补离场/到场时间线。
    annotate_handover_access(db, ev)
    return ev


def annotate_handover_access(db: Session, ev: models.AccessEvent) -> None:
    """根据一条门禁事件，给相关活跃交接补时间线（离场/到场）。"""
    h = db.scalar(
        select(models.Handover)
        .join(models.Job, models.Job.id == models.Handover.job_id)
        .where(
            models.Handover.active == 1,
            models.Job.location == ev.location,
        )
    )
    if h is None:
        return
    person = db.get(models.Person, ev.person_id)
    at = _aware(ev.occurred_at)
    late = (_aware(ev.received_at) - at).total_seconds() > 1
    lag_note = f"（门禁回执延迟 {int((_aware(ev.received_at) - at).total_seconds())} 秒到达，以实际刷卡时间为准）" if late else ""
    if ev.direction == "out" and person.id == h.outgoing_id:
        _log(db, h.job_id, "outgoing_left",
             f"原监护人 {person.name} 离场。{lag_note}", actor_login=person.login,
             handover_id=h.id, at=at)
    elif ev.direction == "in" and person.id == h.incoming_id:
        _log(db, h.job_id, "incoming_arrived",
             f"接班人 {person.name} 到场接班。{lag_note}", actor_login=person.login,
             handover_id=h.id, at=at)
    db.commit()


# ─────────────────────────── 复工 ───────────────────────────

def resume_blockers(db: Session, h: models.Handover, at: datetime | None = None) -> list[dict]:
    """复工硬条件校验，返回阻断原因列表；空列表表示允许复工。"""
    at = _aware(at or timeutil.now())
    blockers: list[dict] = []
    permit = h.job.permit
    incoming = h.incoming

    if not incoming.qualified(permit.required_cert, at):
        exp = incoming.cert_expires_at
        reason = "未持有" if incoming.required_cert != permit.required_cert else "已过期"
        blockers.append({
            "code": "incoming_unqualified",
            "message": f"接班人 {incoming.name} 的资质「{permit.required_cert}」{reason}，不能复工。",
        })
    if not permit.valid_at(at):
        blockers.append({
            "code": "permit_invalid",
            "message": f"许可 {permit.code} 当前无效（过期或已撤销），不能复工。",
        })
    if not is_present(db, incoming.id, h.job.location, at):
        blockers.append({
            "code": "no_guardian_on_site",
            "message": f"接班人 {incoming.name} 不在作业现场，现场无人监护，不能复工。",
        })
    if not db.scalar(
        select(models.SignOff).where(
            models.SignOff.handover_id == h.id, models.SignOff.role == "outgoing"
        )
    ) or not db.scalar(
        select(models.SignOff).where(
            models.SignOff.handover_id == h.id, models.SignOff.role == "incoming"
        )
    ):
        blockers.append({"code": "signoff_incomplete", "message": "双方尚未完成签认，不能复工。"})
    return blockers


def request_resume(db: Session, h: models.Handover, requester: models.Person) -> models.ResumeRequest:
    if requester.id != h.incoming_id:
        raise BusinessError("not_incoming", "只有接班人可以申请复工。", 403)
    if h.status != "completed":
        raise BusinessError("handover_not_ready", "双方签认未完成，还不能申请复工。")

    existing = h.resume
    if existing is not None:
        if existing.status == "pending":
            return existing  # 重复申请幂等
        if existing.status == "approved":
            raise BusinessError("already_resumed", "已经复工，无需再次申请。", 409)

    req = models.ResumeRequest(handover_id=h.id, requested_by_id=requester.id)
    db.add(req)
    db.flush()
    _log(db, h.job_id, "resume_requested",
         f"接班人 {requester.name} 提交复工申请。", actor_login=requester.login,
         handover_id=h.id)
    gap = unattended_gap(db, h)
    gap_txt = f" 本次交接存在 {int(gap.seconds)} 秒无人监护空档。" if gap.seconds else ""
    _notify(db, "leader", "复工申请待审批",
            f"作业「{h.job.name}」申请复工。{gap_txt}")
    db.commit()
    db.refresh(req)
    return req


def decide_resume(
    db: Session, req: models.ResumeRequest, leader: models.Person,
    approve: bool, reject_reason: str | None = None,
) -> models.ResumeRequest:
    if leader.role != "leader":
        raise BusinessError("not_leader", "只有班组长可以审批复工。", 403)
    if req.status != "pending":
        raise BusinessError("request_decided", "该复工申请已处理，不能重复审批。", 409)

    h = req.handover
    if approve:
        blockers = resume_blockers(db, h)
        if blockers:
            # 关键：任一硬条件不满足，系统直接拒绝，班组长也不能放行。
            raise BusinessError(
                "resume_blocked",
                "复工条件不满足：" + "；".join(b["message"] for b in blockers),
                422,
            )
        req.status = "approved"
        req.decided_by_id = leader.id
        req.decided_at = timeutil.now()
        h.status = "resumed"
        h.active = 0
        h.close_time = timeutil.now()
        job = h.job
        job.guardian_id = h.incoming_id
        job.status = "running"
        gap = unattended_gap(db, h)
        h.unattended_seconds = gap.seconds
        _log(db, job.id, "resumed",
             f"班组长 {leader.name} 批准复工，监护人变更为 {h.incoming.name}，作业恢复。",
             actor_login=leader.login, handover_id=h.id)
        _notify(db, "guardian", "复工已批准", f"作业「{job.name}」已复工。")
    else:
        if not reject_reason:
            raise BusinessError("reason_required", "驳回必须填写原因。")
        req.status = "rejected"
        req.reject_reason = reject_reason
        req.decided_by_id = leader.id
        req.decided_at = timeutil.now()
        _log(db, h.job_id, "resume_rejected",
             f"班组长 {leader.name} 驳回复工：{reject_reason}", actor_login=leader.login,
             handover_id=h.id)
    db.commit()
    db.refresh(req)
    return req
