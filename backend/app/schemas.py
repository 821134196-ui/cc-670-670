"""Pydantic 响应/请求模式。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from . import timeutil


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── 人员 / 资质 ──
class PersonOut(ORMModel):
    id: int
    name: str
    login: str
    role: str
    required_cert: str | None = None
    cert_expires_at: datetime | None = None
    active: bool


class CertRenew(BaseModel):
    cert_expires_at: datetime  # ISO8601，测试资质过期/续期


# ── 许可 / 作业 ──
class PermitOut(ORMModel):
    id: int
    code: str
    title: str
    required_cert: str
    valid_from: datetime
    valid_until: datetime
    revoked: bool


class RiskOut(ORMModel):
    id: int
    content: str


class IsolationOut(ORMModel):
    id: int
    content: str
    intact: bool


class PendingOut(ORMModel):
    id: int
    content: str
    done: bool


class JobOut(ORMModel):
    id: int
    name: str
    location: str
    status: str
    guardian_id: int
    permit: PermitOut
    risks: list[RiskOut]
    isolations: list[IsolationOut]
    pending_items: list[PendingOut]


# ── 门禁 ──
class AccessIn(BaseModel):
    person_id: int
    direction: str  # in / out
    location: str
    # 真实刷卡时间；不传表示“现在”。传过去时间模拟延迟到达的回执。
    occurred_at: datetime | None = None


class AccessOut(ORMModel):
    id: int
    person_id: int
    direction: str
    location: str
    occurred_at: datetime
    received_at: datetime


# ── 交接 ──
class HandoverStart(BaseModel):
    job_id: int
    outgoing_login: str
    incoming_id: int


class SignOffIn(BaseModel):
    role: str  # outgoing / incoming
    login: str  # 必须本人账号
    risks_confirmed: bool = True
    isolations_confirmed: bool = True


class SignOffOut(ORMModel):
    id: int
    role: str
    person_id: int
    login: str
    risks_confirmed: bool
    isolations_confirmed: bool
    signed_at: datetime


class ResumeIn(BaseModel):
    login: str


class ResumeDecision(BaseModel):
    leader_login: str
    approve: bool
    reject_reason: str | None = None


class HandoverOut(ORMModel):
    id: int
    status: str
    active: int
    outgoing_id: int
    incoming_id: int
    pause_time: datetime
    complete_time: datetime | None
    close_time: datetime | None
    unattended_seconds: float | None
    signoffs: list[SignOffOut]


class TimelineOut(ORMModel):
    id: int
    kind: str
    message: str
    at: datetime
    actor_login: str | None


class ResumeRequestOut(ORMModel):
    id: int
    status: str
    requested_by_id: int
    requested_at: datetime
    decided_at: datetime | None
    reject_reason: str | None


# ── 通知 ──
class NotificationOut(ORMModel):
    id: int
    target_role: str
    title: str
    body: str
    read: bool
    created_at: datetime


class ErrorOut(BaseModel):
    code: str
    message: str
