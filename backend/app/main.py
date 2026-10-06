"""FastAPI 入口：监护人替换与现场复核系统。

鉴权简化为请求头 X-User-Login（本地模拟 SSO/工牌登录）。每个账号唯一对应
一名员工，因此“谁登录就是谁操作”，从入口处杜绝共用账号与代签。
"""
from __future__ import annotations

import os
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas, services, timeutil
from .database import get_db, init_db
from .seed import seed

app = FastAPI(title="危险作业监护人替换与现场复核系统", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    if os.getenv("TESTING"):
        return  # 测试使用内存库与依赖覆盖，不初始化/播种文件库。
    init_db()
    if not os.getenv("DISABLE_SEED"):
        seed()


@app.exception_handler(services.BusinessError)
def _biz_error_handler(_request, exc: services.BusinessError):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=exc.status,
        content={"code": exc.code, "message": exc.message},
    )


# ─────────────────────────── 鉴权 ───────────────────────────

def current_user(
    x_user_login: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> models.Person:
    if not x_user_login:
        raise HTTPException(401, {"code": "no_login", "message": "缺少 X-User-Login 请求头，请使用本人账号登录。"})
    person = db.scalar(select(models.Person).where(models.Person.login == x_user_login))
    if person is None or not person.active:
        raise HTTPException(401, {"code": "bad_login", "message": "账号不存在或已停用。"})
    return person


# ─────────────────────────── 人员 / 资质 ───────────────────────────

@app.get("/api/persons", response_model=list[schemas.PersonOut], tags=["人员"])
def list_persons(db: Session = Depends(get_db)):
    return db.scalars(select(models.Person).order_by(models.Person.id)).all()


@app.post("/api/persons/{person_id}/cert", response_model=schemas.PersonOut, tags=["人员"])
def renew_cert(
    person_id: int,
    body: schemas.CertRenew,
    db: Session = Depends(get_db),
    user: models.Person = Depends(current_user),
):
    """资质续期：用于把“已过期”接班人改为合格，验证通过/不通过两种结果。"""
    person = db.get(models.Person, person_id)
    if person is None:
        raise HTTPException(404, "人员不存在")
    person.cert_expires_at = body.cert_expires_at
    db.commit()
    db.refresh(person)
    return person


# ─────────────────────────── 许可 / 作业 ───────────────────────────

@app.get("/api/jobs", response_model=list[schemas.JobOut], tags=["作业"])
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.scalars(select(models.Job).order_by(models.Job.id)).all()
    return jobs


@app.get("/api/jobs/{job_id}", response_model=schemas.JobOut, tags=["作业"])
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(models.Job, job_id)
    if job is None:
        raise HTTPException(404, "作业不存在")
    return job


@app.get("/api/jobs/{job_id}/timeline", response_model=list[schemas.TimelineOut], tags=["交接"])
def job_timeline(job_id: int, db: Session = Depends(get_db)):
    """交接历史：按时间看清 暂停→离场→到场→签认→申请复工→复工 的顺序。"""
    return db.scalars(
        select(models.TimelineEvent)
        .where(models.TimelineEvent.job_id == job_id)
        .order_by(models.TimelineEvent.at, models.TimelineEvent.id)
    ).all()


# ─────────────────────────── 门禁（本地模拟） ───────────────────────────

@app.post("/api/access", response_model=schemas.AccessOut, tags=["门禁"])
def swipe(body: schemas.AccessIn, db: Session = Depends(get_db)):
    """模拟刷卡。回执可延迟：occurred_at 为真实刷卡时刻，到达时间另记。"""
    person = db.get(models.Person, body.person_id)
    if person is None:
        raise HTTPException(404, "人员不存在")
    ev = services.record_access(db, person, body.direction, body.location, body.occurred_at)
    return ev


# ─────────────────────────── 交接 ───────────────────────────

@app.get("/api/jobs/{job_id}/handover", tags=["交接"])
def active_handover(job_id: int, db: Session = Depends(get_db)):
    """带出进行中的交接、双方签认、无人监护空档与复工预检结果。"""
    h = services.get_active_handover(db, job_id)
    if h is None:
        return {"active": None}
    gap = services.unattended_gap(db, h)
    blockers = services.resume_blockers(db, h) if h.status == "completed" else []
    return {
        "active": schemas.HandoverOut.model_validate(h).model_dump(mode="json"),
        "gap": {
            "start": timeutil.iso(gap.start),
            "end": timeutil.iso(gap.end),
            "seconds": gap.seconds,
            "open": gap.open,
        },
        "resume_blockers": blockers,
        "resume": schemas.ResumeRequestOut.model_validate(h.resume).model_dump(mode="json")
        if h.resume else None,
    }


@app.post("/api/handovers/start", response_model=schemas.HandoverOut, tags=["交接"])
def start_handover(
    body: schemas.HandoverStart,
    db: Session = Depends(get_db),
    user: models.Person = Depends(current_user),
):
    """停工并发起交接。必须由当前监护人本人账号发起。"""
    if user.login != body.outgoing_login:
        raise services.BusinessError(
            "login_mismatch", "登录账号与交班人不一致，不能替他人发起交接。", 403
        )
    job = db.get(models.Job, body.job_id)
    if job is None:
        raise HTTPException(404, "作业不存在")
    incoming = db.get(models.Person, body.incoming_id)
    if incoming is None:
        raise HTTPException(404, "接班人不存在")
    return services.start_handover(db, job, user, incoming)


@app.post("/api/handovers/{handover_id}/sign", response_model=schemas.SignOffOut, tags=["交接"])
def sign(
    handover_id: int,
    body: schemas.SignOffIn,
    db: Session = Depends(get_db),
    user: models.Person = Depends(current_user),
):
    """本人账号签认。重复提交幂等；替他人签（代签/共用账号）被拒绝。"""
    if user.login != body.login:
        raise services.BusinessError(
            "login_mismatch", "登录账号与签认账号不一致，禁止共用账号或代签。", 403
        )
    h = db.get(models.Handover, handover_id)
    if h is None:
        raise HTTPException(404, "交接不存在")
    return services.sign_off(
        db, h, body.role, user, body.risks_confirmed, body.isolations_confirmed
    )


@app.post("/api/handovers/{handover_id}/resume-request",
          response_model=schemas.ResumeRequestOut, tags=["复工"])
def request_resume(
    handover_id: int,
    body: schemas.ResumeIn,
    db: Session = Depends(get_db),
    user: models.Person = Depends(current_user),
):
    if user.login != body.login:
        raise services.BusinessError("login_mismatch", "登录账号与申请人不一致。", 403)
    h = db.get(models.Handover, handover_id)
    if h is None:
        raise HTTPException(404, "交接不存在")
    return services.request_resume(db, h, user)


@app.post("/api/resume-requests/{req_id}/decision",
          response_model=schemas.ResumeRequestOut, tags=["复工"])
def decide_resume(
    req_id: int,
    body: schemas.ResumeDecision,
    db: Session = Depends(get_db),
    user: models.Person = Depends(current_user),
):
    """班组长审批。硬条件不满足时，系统直接拒绝，班组长也无法放行。"""
    if user.login != body.leader_login:
        raise services.BusinessError("login_mismatch", "登录账号与审批人不一致。", 403)
    req = db.get(models.ResumeRequest, req_id)
    if req is None:
        raise HTTPException(404, "复工申请不存在")
    return services.decide_resume(db, req, user, body.approve, body.reject_reason)


@app.get("/api/resume-requests", tags=["复工"])
def list_resume_requests(
    status: str | None = None,
    db: Session = Depends(get_db),
    user: models.Person = Depends(current_user),
):
    """班组长视图：复工申请及其无人监护空档。"""
    stmt = select(models.ResumeRequest).join(models.Handover)
    if status:
        stmt = stmt.where(models.ResumeRequest.status == status)
    reqs = db.scalars(stmt.order_by(models.ResumeRequest.requested_at.desc())).all()
    out = []
    for r in reqs:
        gap = services.unattended_gap(db, r.handover)
        out.append({
            "request": schemas.ResumeRequestOut.model_validate(r).model_dump(mode="json"),
            "job_id": r.handover.job_id,
            "job_name": r.handover.job.name,
            "handover_id": r.handover.id,
            "outgoing": r.handover.outgoing.name,
            "incoming": r.handover.incoming.name,
            "gap_seconds": gap.seconds,
            "blockers": services.resume_blockers(db, r.handover),
        })
    return out


@app.get("/api/leader/overview", tags=["复工"])
def leader_overview(db: Session = Depends(get_db), user: models.Person = Depends(current_user)):
    """班组长总览：各作业的无人监护时间段与复工申请。"""
    handovers = db.scalars(
        select(models.Handover).where(models.Handover.active == 1)
    ).all()
    rows = []
    for h in handovers:
        gap = services.unattended_gap(db, h)
        rows.append({
            "job_id": h.job_id,
            "job_name": h.job.name,
            "location": h.job.location,
            "status": h.status,
            "outgoing": h.outgoing.name,
            "incoming": h.incoming.name,
            "gap": {
                "start": timeutil.iso(gap.start),
                "end": timeutil.iso(gap.end),
                "seconds": gap.seconds,
                "open": gap.open,
            },
        })
    pending = db.scalars(
        select(models.ResumeRequest).where(models.ResumeRequest.status == "pending")
    ).all()
    return {
        "unattended_windows": rows,
        "pending_count": len(pending),
    }


# ─────────────────────────── 通知（本地模拟） ───────────────────────────

@app.get("/api/notifications", response_model=list[schemas.NotificationOut], tags=["通知"])
def list_notifications(
    target_role: str | None = None,
    db: Session = Depends(get_db),
    user: models.Person = Depends(current_user),
):
    stmt = select(models.Notification)
    role = target_role or user.role
    stmt = stmt.where(models.Notification.target_role == role)
    return db.scalars(stmt.order_by(models.Notification.created_at.desc())).all()


@app.get("/api/health", tags=["系统"])
def health():
    return {"status": "ok", "time": timeutil.iso(datetime.now())}


# 前端构建产物存在时，由后端在同一端口托管（API 路由已先注册，优先匹配）。
# 开发时也可用 Vite 5173（其 /api 代理到本服务）。
_DIST = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(_DIST):
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")),
              name="assets")

    @app.get("/", include_in_schema=False)
    def _index():
        from fastapi.responses import FileResponse

        return FileResponse(os.path.join(_DIST, "index.html"))
