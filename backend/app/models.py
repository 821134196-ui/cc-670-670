"""SQLAlchemy 数据模型。

约定：
- 时间一律以 UTC 时区感知时间存储（SQLite 以 ISO 文本保存）。
- 每个作业人员有独立账号（login 唯一），签名记录 login，禁止共用/代签。
- 交接全过程产生一条 Handover，其下挂两个 SignOff（原监护人 / 接班人）、
  一条复工申请 ResumeRequest 与若干 TimelineEvent。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .timeutil import now


class Base(DeclarativeBase):
    pass


def _ts() -> datetime:
    return now()


# ─────────────────────────── 人员与资质 ───────────────────────────

class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # 个人专属账号，唯一；禁止两人共用。
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # guardian / leader / worker
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 资质要求：required_cert 指明该监护人需持有的证书名称。
    required_cert: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cert_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def qualified(self, cert_name: str, at: datetime | None = None) -> bool:
        """是否在指定时刻持有目标证书且未过期。"""
        at = at or now()
        if self.required_cert != cert_name:
            return False
        if self.cert_expires_at is None:
            return False
        exp = self.cert_expires_at
        if exp.tzinfo is None:
            from datetime import timezone

            exp = exp.replace(tzinfo=timezone.utc)
        return exp > at


# ─────────────────────────── 许可与作业 ───────────────────────────

class Permit(Base):
    __tablename__ = "permits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    required_cert: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    jobs: Mapped[list["Job"]] = relationship(back_populates="permit")

    def valid_at(self, at: datetime | None = None) -> bool:
        at = at or now()
        lo, hi = self.valid_from, self.valid_until
        from datetime import timezone

        if lo.tzinfo is None:
            lo = lo.replace(tzinfo=timezone.utc)
        if hi.tzinfo is None:
            hi = hi.replace(tzinfo=timezone.utc)
        return (not self.revoked) and lo <= at <= hi


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    permit_id: Mapped[int] = mapped_column(ForeignKey("permits.id"), nullable=False)
    # 作业开始时的原监护人。
    guardian_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    # 状态：running（进行中） / paused（停工交接） / resumed（复工） / closed（关闭）
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)

    permit: Mapped[Permit] = relationship(back_populates="jobs")
    guardian: Mapped[Person] = relationship(foreign_keys=[guardian_id])

    risks: Mapped[list["RiskItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    isolations: Mapped[list["IsolationItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    pending_items: Mapped[list["PendingItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class RiskItem(Base):
    __tablename__ = "risk_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    job: Mapped[Job] = relationship(back_populates="risks")


class IsolationItem(Base):
    __tablename__ = "isolation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 隔离是否仍然有效（交接时须确认）。
    intact: Mapped[bool] = mapped_column(Boolean, default=True)
    job: Mapped[Job] = relationship(back_populates="isolations")


class PendingItem(Base):
    """未完成事项（Outstanding）。"""

    __tablename__ = "pending_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    job: Mapped[Job] = relationship(back_populates="pending_items")


# ─────────────────────────── 门禁（本地模拟） ───────────────────────────

class AccessEvent(Base):
    """门禁出入记录。

    occurred_at：刷卡真实发生时间（权威在场依据）。
    received_at：回执到达系统的时间。延迟到达时 received_at 晚于 occurred_at，
    但系统只能按 occurred_at 判定在场，且绝不臆造未发生的事件。
    """

    __tablename__ = "access_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False, index=True)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # in / out
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_ts)


# ─────────────────────────── 交接与签认 ───────────────────────────

class Handover(Base):
    __tablename__ = "handovers"
    __table_args__ = (
        # 每个作业任意时刻最多只有一条“进行中（未复工）”的交接，
        # 重复提交不能产生第二次交接。部分唯一索引：仅约束 active=1。
        Index(
            "uq_handover_active",
            "job_id",
            unique=True,
            sqlite_where=text("active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    outgoing_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    incoming_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)

    pause_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 接班人进入现场、交接完成（双方签认）时刻。
    complete_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 状态：signing（签认中） / completed（双方已签、待复工） / resumed（已复工） / rejected / cancelled
    status: Mapped[str] = mapped_column(String(16), default="signing", nullable=False)
    # active=1 表示该作业当前生效的唯一交接；完成复工后置 0。
    active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # 交接时记录的无人监护空档（秒），用于班组长复核留痕。
    unattended_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    job: Mapped[Job] = relationship()
    outgoing: Mapped[Person] = relationship(foreign_keys=[outgoing_id])
    incoming: Mapped[Person] = relationship(foreign_keys=[incoming_id])
    signoffs: Mapped[list["SignOff"]] = relationship(
        back_populates="handover", cascade="all, delete-orphan"
    )
    resume: Mapped["ResumeRequest | None"] = relationship(
        back_populates="handover", cascade="all, delete-orphan", uselist=False
    )


class SignOff(Base):
    __tablename__ = "signoffs"
    __table_args__ = (
        # 同一交接中，同一角色只能签一次——重复提交幂等，不产生第二条。
        UniqueConstraint("handover_id", "role", name="uq_signoff_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    handover_id: Mapped[int] = mapped_column(ForeignKey("handovers.id"), nullable=False)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    # 冗余保存 login，强调“本人账号”签认，便于审计比对。
    login: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # outgoing / incoming
    # 对风险与隔离措施的核对结论。
    risks_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    isolations_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_ts)

    handover: Mapped[Handover] = relationship(back_populates="signoffs")
    person: Mapped[Person] = relationship()


class ResumeRequest(Base):
    """复工申请（接班人发起，班组长审批）。"""

    __tablename__ = "resume_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    handover_id: Mapped[int] = mapped_column(ForeignKey("handovers.id"), nullable=False)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_ts)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # pending / approved / rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    handover: Mapped[Handover] = relationship(back_populates="resume")
    requested_by: Mapped[Person] = relationship(foreign_keys=[requested_by_id])


class TimelineEvent(Base):
    """交接历史时间线：暂停 / 离场 / 接班（到场+签认） / 申请复工 / 复工。"""

    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    handover_id: Mapped[int | None] = mapped_column(ForeignKey("handovers.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_ts)
    actor_login: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Notification(Base):
    """本地模拟的通知（班组长/监护人）。"""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), default="local")
    target_role: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_ts)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
