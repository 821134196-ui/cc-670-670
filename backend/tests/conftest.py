"""pytest 夹具：内存 SQLite + FastAPI TestClient + 场景数据构造。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ["TESTING"] = "1"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, timeutil
from app.database import Base, get_db
from app.main import app

CERT = "受限空间作业监护证"


@pytest.fixture(autouse=True)
def clock():
    """每个用例在冻结时钟上运行，结束后复位，避免相互污染。"""
    base = datetime.now(timezone.utc).replace(microsecond=0)
    timeutil.freeze(base)
    yield
    timeutil.reset()


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def client(db):
    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── 数据构造辅助 ──

def make_person(db, name, login, role="guardian", cert=CERT, exp_in_days=300):
    exp = timeutil.now() + timedelta(days=exp_in_days) if exp_in_days is not None else None
    p = models.Person(
        name=name, login=login, role=role, required_cert=cert, cert_expires_at=exp
    )
    db.add(p)
    db.flush()
    return p


def make_permit(db, code="P-T1", valid=True, cert=CERT, hours=8):
    t = timeutil.now()
    if valid:
        frm, until = t - timedelta(hours=1), t + timedelta(hours=hours)
    else:
        frm, until = t - timedelta(days=2), t - timedelta(hours=1)
    permit = models.Permit(
        code=code, title="测试许可", required_cert=cert,
        valid_from=frm, valid_until=until,
    )
    db.add(permit)
    db.flush()
    return permit


def make_job(db, guardian, permit=None, location="A区-测试点"):
    permit = permit or make_permit(db)
    job = models.Job(
        name="测试作业", location=location, permit_id=permit.id,
        guardian_id=guardian.id, status="running",
    )
    db.add(job)
    db.flush()
    db.add_all([
        models.RiskItem(job_id=job.id, content="中毒窒息风险"),
        models.IsolationItem(job_id=job.id, content="进料阀加盲板", intact=True),
        models.PendingItem(job_id=job.id, content="下班前气体复检", done=False),
    ])
    db.commit()
    return job


def swipe(db, person, direction, location, occurred=None):
    return models.AccessEvent(
        person_id=person.id, location=location, direction=direction,
        occurred_at=occurred or timeutil.now(),
        received_at=timeutil.now(),
    )


class Api:
    """薄封装：自动带上本人账号请求头。"""

    def __init__(self, client):
        self.c = client

    def call(self, method, path, login=None, **kw):
        headers = kw.pop("headers", {})
        if login:
            headers["X-User-Login"] = login
        return getattr(self.c, method)(path, headers=headers, **kw)

    def start(self, job_id, outgoing_login, incoming_id):
        return self.call("post", "/api/handovers/start", outgoing_login,
                         json={"job_id": job_id, "outgoing_login": outgoing_login,
                               "incoming_id": incoming_id})

    def sign(self, hid, role, login, risks=True, isolations=True):
        return self.call("post", f"/api/handovers/{hid}/sign", login,
                         json={"role": role, "login": login,
                               "risks_confirmed": risks,
                               "isolations_confirmed": isolations})

    def request_resume(self, hid, login):
        return self.call("post", f"/api/handovers/{hid}/resume-request", login,
                         json={"login": login})

    def decide(self, rid, leader_login, approve=True, reason=None):
        return self.call("post", f"/api/resume-requests/{rid}/decision", leader_login,
                         json={"leader_login": leader_login, "approve": approve,
                               "reject_reason": reason})


@pytest.fixture()
def api(client):
    return Api(client)
