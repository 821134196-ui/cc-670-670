"""门禁在场判定。

铁律：
1. 只依据“已到达”的回执（received_at <= 判定时刻）——回执迟到时，
   系统不知道这次过闸，不能假设在场，也不能假设离场；
2. 在场区间以过闸时间 event_time 为界，但绝不会把任何记录补到过闸之前；
3. 回执事后到达不改变此前已锁定的决策（见 handover_service 中的空档快照）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AccessEvent, Direction


def known_events(db: Session, person_id: int, as_of: datetime) -> list[AccessEvent]:
    """as_of 时刻系统已经收到的该人员回执，按过闸时间排序。"""
    rows = db.execute(
        select(AccessEvent)
        .where(AccessEvent.person_id == person_id, AccessEvent.received_at <= as_of)
        .order_by(AccessEvent.event_time, AccessEvent.id)
    ).scalars().all()
    return list(rows)


def is_on_site(db: Session, person_id: int, at: datetime) -> bool:
    """at 时刻是否在场：只看 at 之前已到达的回执，最后一条方向为 IN 即在場。

    迟到的 IN 回执（received_at > at）不算数——不能据此补出当时不存在的在场。
    """
    last = None
    for ev in known_events(db, person_id, at):
        if ev.event_time <= at:
            last = ev
    return last is not None and last.direction == Direction.IN


def first_in_after(
    db: Session, person_id: int, start: datetime, as_of: datetime
) -> datetime | None:
    for ev in known_events(db, person_id, as_of):
        if ev.event_time >= start and ev.direction == Direction.IN:
            return ev.event_time
    return None


def last_out_after(
    db: Session, person_id: int, start: datetime, as_of: datetime
) -> datetime | None:
    result = None
    for ev in known_events(db, person_id, as_of):
        if ev.event_time >= start and ev.direction == Direction.OUT:
            result = ev.event_time
    return result


def compute_gap(
    db: Session,
    outgoing_id: int,
    incoming_id: int,
    paused_at: datetime,
    as_of: datetime,
) -> dict:
    """计算停工以来的无人监护时段（只基于 as_of 之前已到达的回执）。

    - 原监护人已有离场回执：空档自物理离场时刻 event_time 起算；
    - 尚无离场回执、最后一条已知方向为 IN：视为仍在场，空档尚未开始；
    - 没有任何已知记录（回执全未到）：无法证明在场，保守取停工时刻起算并标注；
    - 接班人已有入场回执：空档止于物理入场时刻；否则空档持续中。
    迟到回执事后到达只影响“之后”的展示，已锁定的决策快照不被改写。
    """
    out_events = known_events(db, outgoing_id, as_of)
    out_time = None
    for ev in out_events:
        if ev.event_time >= paused_at and ev.direction == Direction.OUT:
            out_time = ev.event_time
    in_time = first_in_after(db, incoming_id, paused_at, as_of)

    last_outgoing = out_events[-1] if out_events else None
    outgoing_present = last_outgoing is not None and last_outgoing.direction == Direction.IN

    if out_time is not None:
        gap_start = out_time
        start_basis = "DEPARTURE_RECEIPT"
    elif outgoing_present:
        gap_start = None
        start_basis = "OUTGOING_STILL_ON_SITE"
    else:
        gap_start = paused_at  # 无任何可证明在场的回执，保守处理
        start_basis = "PAUSED_TIME_NO_RECEIPT"

    if gap_start is None:
        ongoing = in_time is None
        gap_end = in_time
        seconds = 0 if in_time is None else 0
    elif in_time is None:
        ongoing = True
        gap_end = None
        seconds = max(0, int((as_of - gap_start).total_seconds()))
    else:
        ongoing = False
        gap_end = in_time
        seconds = max(0, int((in_time - gap_start).total_seconds()))

    return {
        "gap_start": gap_start,
        "gap_end": gap_end,
        "ongoing": ongoing,
        "gap_seconds": seconds,
        "start_basis": start_basis,
        "outgoing_departed_at": out_time,
        "incoming_arrived_at": in_time,
        # 供前端解释当前判定依据了哪些（已到达的）回执
        "based_on_received_receipts_only": True,
    }
