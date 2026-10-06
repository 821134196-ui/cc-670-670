"""ORM 模型：人员资质、作业许可、风险与隔离措施、门禁记录、交接与复工。"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ----------------------------- 枚举 -----------------------------

class PersonRole(str, enum.Enum):
    GUARDIAN = "GUARDIAN"   # 监护人
    LEADER = "LEADER"       # 班组长
    WORKER = "WORKER"       # 作业人员


class WorkStatus(str, enum.Enum):
    IN_PROGRESS = "IN_PROGRESS"  # 作业中
    PAUSED = "PAUSED"            # 停工（交接中）
    COMPLETED = "COMPLETED"      # 作业完成


class PermitStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"     # 许可生效（是否在有效期内仍看 valid_until）
    CLOSED = "CLOSED"     # 已关闭


class ItemKind(str, enum.Enum):
    RISK = "RISK"                 # 风险
    ISOLATION = "ISOLATION"       # 隔离措施


class ItemState(str, enum.Enum):
    OPEN = "OPEN"          # 未完成事项
    CONFIRMED = "CONFIRMED"  # 已落实/已确认


class Direction(str, enum.Enum):
    IN = "IN"    # 入场
    OUT = "OUT"  # 离场


class HandoverStatus(str, enum.Enum):
    PENDING = "PENDING"                # 已停工，等待双方签认
    OUTGOING_SIGNED = "OUTGOING_SIGNED"  # 原监护人已签，待接班人
    FULLY_SIGNED = "FULLY_SIGNED"      # 双方签认完成
    RESUMED = "RESUMED"                # 已复工（交接闭环）
    CANCELLED = "CANCELLED"            # 作废


class EventType(str, enum.Enum):
    WORK_PAUSED = "WORK_PAUSED"                 # 停工
    GUARDIAN_DEPARTED = "GUARDIAN_DEPARTED"     # 原监护人离场
    GUARDIAN_ARRIVED = "GUARDIAN_ARRIVED"       # 接班人到场
    OUTGOING_SIGNED = "OUTGOING_SIGNED"         # 原监护人签认
    INCOMING_SIGNED = "INCOMING_SIGNED"         # 接班人签认
    RESUME_REQUESTED = "RESUME_REQUESTED"       # 申请复工
    RESUME_REJECTED = "RESUME_REJECTED"         # 驳回复工
    WORK_RESUMED = "WORK_RESUMED"               # 复工


class RequestStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# ----------------------------- 模型 -----------------------------

class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password: Mapped[str] = mapped_column(String(128))  # 本地模拟，明文仅用于演示
    name: Mapped[str] = mapped_column(String(64))
    role: Mapped[PersonRole] = mapped_column(Enum(PersonRole))
    # 监护人资质
    qualification_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qualification_valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    tokens: Mapped[list["AuthToken"]] = relationship(back_populates="person")


class AuthToken(Base):
    """登录令牌：一人一账号，签认接口凭令牌确认本人身份，防止共用账号/代签。"""

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)

    person: Mapped[Person] = relationship(back_populates="tokens")


class WorkPermit(Base):
    """作业许可及其当前作业状态。"""

    __tablename__ = "work_permits"

    id: Mapped[int] = mapped_column(primary_key=True)
    permit_no: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    work_type: Mapped[str] = mapped_column(String(64))
    location: Mapped[str] = mapped_column(String(200))
    status: Mapped[PermitStatus] = mapped_column(Enum(PermitStatus), default=PermitStatus.ACTIVE)
    valid_from: Mapped[datetime] = mapped_column(DateTime)
    valid_until: Mapped[datetime] = mapped_column(DateTime)

    work_status: Mapped[WorkStatus] = mapped_column(
        Enum(WorkStatus), default=WorkStatus.IN_PROGRESS
    )
    current_guardian_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime)

    current_guardian: Mapped[Person | None] = relationship(foreign_keys=[current_guardian_id])
    risks: Mapped[list["ChecklistItem"]] = relationship(
        back_populates="permit", cascade="all, delete-orphan"
    )
    handovers: Mapped[list["Handover"]] = relationship(
        back_populates="permit", cascade="all, delete-orphan"
    )


class ChecklistItem(Base):
    """风险 / 隔离措施 / 未完成事项，交接时逐条核对。"""

    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    permit_id: Mapped[int] = mapped_column(ForeignKey("work_permits.id"))
    kind: Mapped[ItemKind] = mapped_column(Enum(ItemKind))
    content: Mapped[str] = mapped_column(Text)
    state: Mapped[ItemState] = mapped_column(Enum(ItemState), default=ItemState.CONFIRMED)
    seq: Mapped[int] = mapped_column(Integer, default=0)

    permit: Mapped[WorkPermit] = relationship(back_populates="risks")


class AccessEvent(Base):
    """门禁出入记录。

    event_time：闸机实际过闸时间（物理事实）
    received_at：回执到达本系统的时间（网络可能延迟）
    在场判断只依据 event_time，且不会把在场时间前推到过闸之前。
    """

    __tablename__ = "access_events"
    __table_args__ = (
        UniqueConstraint("person_id", "event_time", "direction", name="uq_access_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    event_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    is_delayed: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    person: Mapped[Person] = relationship()


class Handover(Base):
    """一次监护人交接（交接单）。

    一张许可同时只允许存在一个未闭环的交接单（见唯一索引 uq_open_handover），
    重复提交签认不会产生第二次交接。
    """

    __tablename__ = "handovers"
    __table_args__ = (
        # 同一许可最多一个未闭环交接单（PENDING/OUTGOING_SIGNED/FULLY_SIGNED），
        # 数据库层保证重复提交不会产生第二次交接。
        Index(
            "uq_open_handover_per_permit",
            "permit_id",
            unique=True,
            sqlite_where=text(
                "status IN ('PENDING','OUTGOING_SIGNED','FULLY_SIGNED')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    permit_id: Mapped[int] = mapped_column(ForeignKey("work_permits.id"), index=True)
    outgoing_guardian_id: Mapped[int] = mapped_column(ForeignKey("persons.id"))
    incoming_guardian_id: Mapped[int] = mapped_column(ForeignKey("persons.id"))

    status: Mapped[HandoverStatus] = mapped_column(
        Enum(HandoverStatus), default=HandoverStatus.PENDING
    )

    paused_at: Mapped[datetime] = mapped_column(DateTime)
    outgoing_signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    incoming_signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 接班人签认时对每条风险/隔离措施的核对快照
    incoming_item_checks: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outgoing_item_checks: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 复工时锁定的无人监护时段（秒），历史可追溯，事后到达的门禁回执不改写
    locked_gap_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    permit: Mapped[WorkPermit] = relationship(back_populates="handovers")
    outgoing_guardian: Mapped[Person] = relationship(foreign_keys=[outgoing_guardian_id])
    incoming_guardian: Mapped[Person] = relationship(foreign_keys=[incoming_guardian_id])
    events: Mapped[list["HandoverEvent"]] = relationship(
        back_populates="handover", cascade="all, delete-orphan",
        order_by="HandoverEvent.occurred_at",
    )
    resume_requests: Mapped[list["ResumeRequest"]] = relationship(
        back_populates="handover", cascade="all, delete-orphan",
        order_by="ResumeRequest.requested_at",
    )


class HandoverEvent(Base):
    """交接历史事件：暂停 → 离场 → 到场 → 签认 → 申请复工 → 复工，严格按时间排序。"""

    __tablename__ = "handover_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    handover_id: Mapped[int] = mapped_column(ForeignKey("handovers.id"), index=True)
    permit_id: Mapped[int] = mapped_column(ForeignKey("work_permits.id"), index=True)
    type: Mapped[EventType] = mapped_column(Enum(EventType))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    handover: Mapped[Handover] = relationship(back_populates="events")
    actor: Mapped[Person | None] = relationship()


class ResumeRequest(Base):
    """复工申请（班组长复核）。"""

    __tablename__ = "resume_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    handover_id: Mapped[int] = mapped_column(ForeignKey("handovers.id"), index=True)
    permit_id: Mapped[int] = mapped_column(ForeignKey("work_permits.id"), index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("persons.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus), default=RequestStatus.PENDING
    )
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    gap_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    handover: Mapped[Handover] = relationship(back_populates="resume_requests")


class SignNonce(Base):
    """签认请求幂等键：同一客户端请求重放只返回首次结果，不产生第二次签认/交接。"""

    __tablename__ = "sign_nonces"

    nonce: Mapped[str] = mapped_column(String(128), primary_key=True)
    handover_id: Mapped[int] = mapped_column(ForeignKey("handovers.id"))
    role: Mapped[str] = mapped_column(String(16))  # OUTGOING / INCOMING
    created_at: Mapped[datetime] = mapped_column(DateTime)
    response_handover_id: Mapped[int] = mapped_column(ForeignKey("handovers.id"))


class Notification(Base):
    """模拟通知（企业微信/短信），仅本地落库。"""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(32), default="MOCK_SMS")
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
