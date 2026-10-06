"""HTTP 端到端测试：账号头、门禁模拟、交接/签认/复工接口联动。"""
from __future__ import annotations

from datetime import timedelta

from app import timeutil
from tests.conftest import make_job, make_person


def _setup(db, incoming_exp=300):
    li = make_person(db, "李", "guardian_li")
    zhao = make_person(db, "赵", "guardian_zhao", exp_in_days=incoming_exp)
    leader = make_person(db, "王", "leader_wang", role="leader",
                         cert=None, exp_in_days=None)
    job = make_job(db, li)
    return li, zhao, leader, job


def test_full_flow_over_http(api, client, db):
    li, zhao, leader, job = _setup(db)
    loc = job.location

    # 原监护人 1 小时前已在场。
    client.post("/api/access", json={
        "person_id": li.id, "direction": "in", "location": loc,
        "occurred_at": timeutil.iso(timeutil.now() - timedelta(hours=1))})

    hid = api.start(job.id, "guardian_li", zhao.id).json()["id"]

    # 原监护人离场前先用本人账号签认。
    assert api.sign(hid, "outgoing", "guardian_li").status_code == 200
    client.post("/api/access", json={"person_id": li.id, "direction": "out",
                                     "location": loc})
    # 接班人到场后签认。
    client.post("/api/access", json={"person_id": zhao.id, "direction": "in",
                                     "location": loc})
    assert api.sign(hid, "incoming", "guardian_zhao").status_code == 200

    # 重复签认幂等：仍 200，签认总数不增加。
    assert api.sign(hid, "incoming", "guardian_zhao").status_code == 200
    detail = client.get(f"/api/jobs/{job.id}/handover").json()
    assert detail["active"]["status"] == "completed"
    assert len(detail["active"]["signoffs"]) == 2
    assert detail["resume_blockers"] == []

    rid = api.request_resume(hid, "guardian_zhao").json()["id"]
    d = api.decide(rid, "leader_wang", approve=True)
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "approved"
    assert client.get(f"/api/jobs/{job.id}").json()["status"] == "running"


def test_shared_account_forbidden(api, client, db):
    li, zhao, leader, job = _setup(db)
    loc = job.location
    client.post("/api/access", json={"person_id": li.id, "direction": "in",
                                     "location": loc})
    client.post("/api/access", json={"person_id": zhao.id, "direction": "in",
                                     "location": loc})
    hid = api.start(job.id, "guardian_li", zhao.id).json()["id"]

    # 用李的登录态，body 却声称是赵 → 拒绝代签。
    r = client.post(f"/api/handovers/{hid}/sign",
                    headers={"X-User-Login": "guardian_li"},
                    json={"role": "incoming", "login": "guardian_zhao",
                          "risks_confirmed": True, "isolations_confirmed": True})
    assert r.status_code == 403 and r.json()["code"] == "login_mismatch"

    # 无登录头 → 401。
    r = client.post(f"/api/handovers/{hid}/sign",
                    json={"role": "outgoing", "login": "guardian_li",
                          "risks_confirmed": True, "isolations_confirmed": True})
    assert r.status_code == 401


def test_expired_cert_resume_rejected_over_http(api, client, db):
    li, zhao, leader, job = _setup(db, incoming_exp=-1)  # 接班人资质过期
    loc = job.location
    client.post("/api/access", json={"person_id": li.id, "direction": "in",
                                     "location": loc})
    hid = api.start(job.id, "guardian_li", zhao.id).json()["id"]
    api.sign(hid, "outgoing", "guardian_li")
    client.post("/api/access", json={"person_id": li.id, "direction": "out",
                                     "location": loc})
    client.post("/api/access", json={"person_id": zhao.id, "direction": "in",
                                     "location": loc})
    api.sign(hid, "incoming", "guardian_zhao")
    rid = api.request_resume(hid, "guardian_zhao").json()["id"]

    r = api.decide(rid, "leader_wang", approve=True)
    assert r.status_code == 422 and r.json()["code"] == "resume_blocked"

    ov = client.get("/api/resume-requests", headers={"X-User-Login": "leader_wang"})
    assert {b["code"] for b in ov.json()[0]["blockers"]} >= {"incoming_unqualified"}


def test_late_access_receipt_cannot_create_attendance(api, client, db):
    """只补来一条迟到的 out、从无 in：不得据此认为接班人在场。"""
    li, zhao, leader, job = _setup(db)
    loc = job.location
    client.post("/api/access", json={"person_id": li.id, "direction": "in",
                                     "location": loc})
    hid = api.start(job.id, "guardian_li", zhao.id).json()["id"]

    client.post("/api/access", json={
        "person_id": zhao.id, "direction": "out", "location": loc,
        "occurred_at": timeutil.iso(timeutil.now() - timedelta(minutes=5))})
    r = api.sign(hid, "incoming", "guardian_zhao")
    assert r.status_code == 403 and r.json()["code"] == "signer_absent"


def test_duplicate_handover_http(api, client, db):
    li, zhao, leader, job = _setup(db)
    assert api.start(job.id, "guardian_li", zhao.id).status_code == 200
    r = api.start(job.id, "guardian_li", zhao.id)
    assert r.status_code == 409 and r.json()["code"] == "handover_exists"


def test_timeline_endpoint_order(api, client, db):
    li, zhao, leader, job = _setup(db)
    loc = job.location
    client.post("/api/access", json={"person_id": li.id, "direction": "in",
                                     "location": loc})
    hid = api.start(job.id, "guardian_li", zhao.id).json()["id"]
    api.sign(hid, "outgoing", "guardian_li")
    client.post("/api/access", json={"person_id": li.id, "direction": "out",
                                     "location": loc})
    client.post("/api/access", json={"person_id": zhao.id, "direction": "in",
                                     "location": loc})
    api.sign(hid, "incoming", "guardian_zhao")
    rid = api.request_resume(hid, "guardian_zhao").json()["id"]
    api.decide(rid, "leader_wang", approve=True)

    kinds = [e["kind"] for e in client.get(f"/api/jobs/{job.id}/timeline").json()]
    assert kinds[0] == "paused" and kinds[-1] == "resumed"
    assert kinds.index("outgoing_left") < kinds.index("incoming_arrived")
