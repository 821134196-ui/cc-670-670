"""请求/响应结构（Pydantic v2）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ----------------------------- 认证 -----------------------------

class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    token: str
    person_id: int
    name: str
    role: str
    qualification_type: str | None = None
    qualification_valid_until: datetime | None = None


# ----------------------------- 人员 -----------------------------

class PersonOut(BaseModel):
    id: int
    username: str
    name: str
    role: str
    qualification_type: str | None = None
    qualification_valid_until: datetime | None = None
    qualification_valid: bool | None = None


# ----------------------------- 许可与清单 -----------------------------

class ChecklistItemOut(BaseModel):
    id: int
    kind: str
    content: str
    state: str
    seq: int


class PermitSummary(BaseModel):
    id: int
    permit_no: str
    title: str
    work_type: str
    location: str
    status: str
    valid_from: datetime
    valid_until: datetime
    valid: bool
    work_status: str
    current_guardian: PersonOut | None = None


# ----------------------------- 交接 -----------------------------

class PauseIn(BaseModel):
    incoming_guardian_id: int
    reason: str | None = None


class SignIn(BaseModel):
    # 客户端必须声明以谁的身份签；服务端再与登录令牌比对，令牌不符一律拒绝
    role: str = Field(pattern="^(OUTGOING|INCOMING)$")
    # item_id -> 是否逐条核对确认；风险与隔离措施必须逐条确认
    item_checks: dict[str, bool]
    nonce: str | None = None  # 幂等键，防重复提交


class AccessEventIn(BaseModel):
    """模拟闸机回执。

    event_time：实际过闸时间；delay_seconds：回执网络延迟。
    received_at 不传时取 now + delay，用于复现“回执延迟到达”。
    """
    person_id: int
    direction: str = Field(pattern="^(IN|OUT)$")
    event_time: datetime
    delay_seconds: float = 0
    note: str | None = None


class AccessEventOut(BaseModel):
    id: int
    person_id: int
    person_name: str
    direction: str
    event_time: datetime
    received_at: datetime
    delayed: bool
    note: str | None = None


class ResumeDecisionIn(BaseModel):
    approve: bool
    comment: str | None = None


class GapOut(BaseModel):
    gap_start: datetime | None
    gap_end: datetime | None
    ongoing: bool
    gap_seconds: int
    start_basis: str
    outgoing_departed_at: datetime | None
    incoming_arrived_at: datetime | None


class HandoverEventOut(BaseModel):
    id: int
    type: str
    actor_id: int | None
    actor_name: str | None
    occurred_at: datetime
    detail: str | None
    meta: dict | None = None


class ResumeRequestOut(BaseModel):
    id: int
    handover_id: int
    permit_id: int
    requested_by: int
    requested_by_name: str
    requested_at: datetime
    status: str
    decided_by: int | None
    decided_at: datetime | None
    reject_reasons: list[str] | None
    gap_seconds: int | None


class HandoverOut(BaseModel):
    id: int
    permit_id: int
    outgoing_guardian: PersonOut
    incoming_guardian: PersonOut
    status: str
    paused_at: datetime
    outgoing_signed_at: datetime | None
    incoming_signed_at: datetime | None
    resumed_at: datetime | None
    cancelled_at: datetime | None
    locked_gap_seconds: int | None
    outgoing_on_site: bool
    incoming_on_site: bool
    incoming_qualified: bool
    permit_valid: bool
    gap: GapOut | None = None


class HandoverPage(BaseModel):
    permit: PermitSummary
    items: list[ChecklistItemOut]
    open_handover: HandoverOut | None
    access_events: list[AccessEventOut]
    notifications: list[str] | None = None


class TimelineOut(BaseModel):
    handover_id: int
    events: list[HandoverEventOut]


class LeaderOverview(BaseModel):
    pending_requests: list[ResumeRequestOut]
    active_gaps: list[dict]
    permits: list[PermitSummary]
