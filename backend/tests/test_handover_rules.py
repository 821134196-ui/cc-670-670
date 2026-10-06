"""硬规则测试：换班空档、资质/许可过期、无人监护、重复签认、代签、延迟回执、重复交接。"""
from __future__ import annotations

from datetime import timedelta

from app.database import SessionLocal
from app.models import Person
from app.services import presence
from app.services.clock import clock


def _pid(username: str) -> int:
    db = SessionLocal()
    try:
        return db.query(Person).filter(Person.username == username).one().id
    finally:
        db.close()


def _confirm_open_items(client, auth, permit_id: int) -> None:
    page = client.get(
        f"/api/permits/{permit_id}/handover-page", headers=auth.headers("zhang")
    ).json()
    for item in page["items"]:
        if item["state"] == "OPEN":
            r = client.post(f"/api/checklist-items/{item['id']}/confirm",
                            headers=auth.headers("zhang"))
            assert r.status_code == 200, r.text


# ----------------------------- 正常交接 + 空档锁定 -----------------------------

def test_full_handover_flow_with_gap(client, auth, scenarios):
    S = scenarios
    # 停工发起交接：张建国 → 李文斌
    r = S.pause("zhang", 1, "li")
    assert r.status_code == 201, r.text
    ho = r.json()
    hid = ho["id"]
    assert ho["status"] == "PENDING"

    page = client.get("/api/permits/1/handover-page", headers=auth.headers("zhang")).json()
    assert page["permit"]["work_status"] == "PAUSED"

    # 原监护人在现场先核对签认；T0+2min 离场，T0+10min 接班人到场
    r = S.sign("zhang", hid, "OUTGOING", 1)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "OUTGOING_SIGNED"

    clock.advance(120)
    assert S.access("zhang", "OUT", clock.now()).status_code == 201
    clock.advance(480)
    assert S.access("li", "IN", clock.now()).status_code == 201

    r = S.sign("li", hid, "INCOMING", 1)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "FULLY_SIGNED"
    assert r.json()["incoming_on_site"] is True

    # 未完成事项未闭环前不能复工
    r = S.resume("li", hid)
    assert r.status_code == 201
    assert r.json()["status"] == "REJECTED"
    assert any("未完成事项" in x for x in r.json()["reject_reasons"])

    _confirm_open_items(client, auth, 1)

    r = S.resume("li", hid)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "PENDING"
    req_id = r.json()["id"]

    # 班组长视图能看到无人监护时段：480 秒
    ov = client.get("/api/leader/overview", headers=auth.headers("leader")).json()
    gap = next(g for g in ov["active_gaps"] if g["handover_id"] == hid)
    assert gap["gap_seconds"] == 480
    assert gap["ongoing"] is False
    assert any(q["id"] == req_id for q in ov["pending_requests"])

    r = S.approve("leader", req_id, True)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED"

    final = client.get(f"/api/handovers/{hid}", headers=auth.headers("leader")).json()
    assert final["status"] == "RESUMED"
    assert final["locked_gap_seconds"] == 480

    # 复工后一张迟到的入场回执到达（声称 T0+3min 已入场）：
    # 已锁定的空档快照不得被改写
    late = scenarios.T0 + timedelta(minutes=3)
    rr = S.access("li", "IN", late, delay=480)
    assert rr.status_code == 201 and rr.json()["delayed"] is True
    final2 = client.get(f"/api/handovers/{hid}", headers=auth.headers("leader")).json()
    assert final2["locked_gap_seconds"] == 480

    permit = client.get("/api/permits", headers=auth.headers("leader")).json()[0]
    assert permit["work_status"] == "IN_PROGRESS"
    assert permit["current_guardian"]["username"] == "li"


def test_timeline_order(client, auth, scenarios):
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    S.sign("zhang", hid, "OUTGOING", 1)
    clock.advance(60)
    S.access("zhang", "OUT", clock.now())
    clock.advance(300)
    S.access("li", "IN", clock.now())
    S.sign("li", hid, "INCOMING", 1)
    _confirm_open_items(client, auth, 1)
    req_id = S.resume("li", hid).json()["id"]
    S.approve("leader", req_id, True)

    tl = client.get(f"/api/handovers/{hid}/timeline",
                    headers=auth.headers("leader")).json()["events"]
    types = [e["type"] for e in tl]
    assert types == [
        "WORK_PAUSED",
        "OUTGOING_SIGNED",
        "GUARDIAN_DEPARTED",
        "GUARDIAN_ARRIVED",
        "INCOMING_SIGNED",
        "RESUME_REQUESTED",
        "WORK_RESUMED",
    ]
    # 时间线本身严格有序
    times = [e["occurred_at"] for e in tl]
    assert times == sorted(times)


# ----------------------------- 无人监护空档 -----------------------------

def test_resume_blocked_when_no_guardian_on_site(client, auth, scenarios):
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    # 接班人到场签认后、申请复工前又离场
    S.access("li", "IN", clock.now())
    S.sign("zhang", hid, "OUTGOING", 1)
    S.sign("li", hid, "INCOMING", 1)
    clock.advance(180)
    S.access("li", "OUT", clock.now())
    _confirm_open_items(client, auth, 1)

    r = S.resume("li", hid)
    body = r.json()
    assert body["status"] == "REJECTED"
    assert any("无监护人在场" in x for x in body["reject_reasons"])

    # 系统拦截的申请不会进入班组长待审批列表
    ov = client.get("/api/leader/overview", headers=auth.headers("leader")).json()
    assert all(q["handover_id"] != hid for q in ov["pending_requests"])


def test_sign_blocked_when_incoming_not_entered(client, auth, scenarios):
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    S.sign("zhang", hid, "OUTGOING", 1)
    # 李文斌门禁无任何入场记录
    r = S.sign("li", hid, "INCOMING", 1)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "SIGNER_NOT_ON_SITE"


def test_leader_sees_ongoing_gap(client, auth, scenarios):
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    clock.advance(120)
    S.access("zhang", "OUT", clock.now())
    clock.advance(600)
    ov = client.get("/api/leader/overview", headers=auth.headers("leader")).json()
    gap = next(g for g in ov["active_gaps"] if g["handover_id"] == hid)
    assert gap["ongoing"] is True
    assert gap["gap_seconds"] == 600
    assert gap["incoming_arrived_at"] is None


# ----------------------------- 资质过期 -----------------------------

def test_incoming_qualification_expired_blocks_resume(client, auth, scenarios):
    S = scenarios
    # 接班人赵德柱资质已过期：发起/签认可走完（现场仍要交接清楚），复工必须拦截
    hid = S.pause("zhang", 1, "zhao_expired").json()["id"]
    S.access("zhao_expired", "IN", clock.now())
    S.sign("zhang", hid, "OUTGOING", 1)
    r = S.sign("zhao_expired", hid, "INCOMING", 1)
    assert r.status_code == 200
    assert r.json()["incoming_qualified"] is False

    _confirm_open_items(client, auth, 1)
    r = S.resume("zhao_expired", hid)
    assert r.json()["status"] == "REJECTED"
    assert any("资质" in x and "过期" in x for x in r.json()["reject_reasons"])

    # 即使硬闯审批接口，也没有待审批申请
    ov = client.get("/api/leader/overview", headers=auth.headers("leader")).json()
    assert ov["pending_requests"] == []


def test_qualification_expires_between_request_and_approval(client, auth, scenarios):
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    S.access("li", "IN", clock.now())
    S.sign("zhang", hid, "OUTGOING", 1)
    S.sign("li", hid, "INCOMING", 1)
    _confirm_open_items(client, auth, 1)
    req_id = S.resume("li", hid).json()["id"]
    # 审批前资质到期（李文斌资质 200 天后，快进 201 天）
    clock.advance(201 * 24 * 3600)
    r = S.approve("leader", req_id, True)
    assert r.json()["status"] == "REJECTED"
    assert any("资质" in x for x in r.json()["reject_reasons"])
    ho = client.get(f"/api/handovers/{hid}", headers=auth.headers("leader")).json()
    assert ho["status"] != "RESUMED"


# ----------------------------- 许可过期 -----------------------------

def test_permit_expired_blocks_resume(client, auth, scenarios):
    S = scenarios
    # 许可 P-2026-002 30 分钟后到期
    hid = S.pause("chen", 2, "li").json()["id"]
    S.access("li", "IN", clock.now())
    S.sign("chen", hid, "OUTGOING", 2)
    S.sign("li", hid, "INCOMING", 2)
    clock.advance(31 * 60)
    r = S.resume("li", hid)
    assert r.json()["status"] == "REJECTED"
    assert any("许可" in x and "过期" in x for x in r.json()["reject_reasons"])


# ----------------------------- 重复签认 / 代签 / 重复交接 -----------------------------

def test_duplicate_sign_rejected_and_nonce_idempotent(client, auth, scenarios):
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    S.access("li", "IN", clock.now())

    r1 = S.sign("zhang", hid, "OUTGOING", 1, nonce="n-zhang-1")
    assert r1.status_code == 200
    # 无 nonce 的重复提交 → 409，不产生第二次签认
    r2 = S.sign("zhang", hid, "OUTGOING", 1)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "ALREADY_SIGNED"
    # 携带原 nonce 重放 → 返回同一交接单，时间线不增加
    r3 = S.sign("zhang", hid, "OUTGOING", 1, nonce="n-zhang-1")
    assert r3.status_code == 200
    assert r3.json()["id"] == hid

    S.sign("li", hid, "INCOMING", 1)
    r4 = S.sign("li", hid, "INCOMING", 1, nonce="n-li-1")
    assert r4.status_code == 409

    tl = client.get(f"/api/handovers/{hid}/timeline",
                    headers=auth.headers("zhang")).json()["events"]
    assert [e["type"] for e in tl].count("OUTGOING_SIGNED") == 1
    assert [e["type"] for e in tl].count("INCOMING_SIGNED") == 1


def test_proxy_sign_forbidden(client, auth, scenarios):
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    S.access("li", "IN", clock.now())
    # 张建国试图替接班人签
    r = S.sign("zhang", hid, "INCOMING", 1)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "NOT_SIGNING_PARTY"
    # 李文斌试图抢先替原监护人签
    r = S.sign("li", hid, "OUTGOING", 1)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "NOT_SIGNING_PARTY"
    # 无资质工人账号不能签
    r = S.sign("sun_none", hid, "OUTGOING", 1)
    assert r.status_code == 403
    # 交接单仍停留在 PENDING
    ho = client.get(f"/api/handovers/{hid}", headers=auth.headers("zhang")).json()
    assert ho["status"] == "PENDING"


def test_cannot_open_second_handover(client, auth, scenarios):
    S = scenarios
    assert S.pause("zhang", 1, "li").status_code == 201
    r = S.pause("zhang", 1, "chen")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "HANDOVER_ALREADY_OPEN"
    # 数据库里仍只有一张交接单
    rows = client.get("/api/permits/1/handovers", headers=auth.headers("zhang")).json()
    assert len(rows) == 1


def test_incoming_must_differ_from_outgoing(client, auth, scenarios):
    S = scenarios
    r = S.pause("zhang", 1, "zhang")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "SAME_PERSON"


def test_signing_order_enforced(client, auth, scenarios):
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    S.access("li", "IN", clock.now())
    # 接班人不能先签
    r = S.sign("li", hid, "INCOMING", 1)
    assert r.status_code == 409


# ----------------------------- 风险/隔离逐条核对 -----------------------------

def test_items_must_be_confirmed_one_by_one(client, auth, scenarios):
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    checks = S.all_item_checks(1, "zhang")
    first = next(iter(checks))
    checks[first] = False  # 漏确认一条
    r = S.sign("zhang", hid, "OUTGOING", 1, checks=checks)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "ITEMS_NOT_CONFIRMED"
    assert first in r.json()["error"]["unconfirmed_item_ids"]


# ----------------------------- 门禁延迟回执 -----------------------------

def test_delayed_receipt_does_not_backfill_presence(client, auth, scenarios):
    """服务层直测：在场判断只使用判定时刻之前已到达的回执。"""
    li_id = _pid("li")
    # 物理过闸 T0+5min，回执 T0+10min 才到
    physical_in = scenarios.T0 + timedelta(minutes=5)
    arrived = scenarios.T0 + timedelta(minutes=10)

    S = scenarios
    S.pause("zhang", 1, "li")
    # 回执到达前：T0+7min 系统不知道李文斌已入场
    assert presence.is_on_site(SessionLocal(), li_id, physical_in + timedelta(minutes=2)) is False
    # 时间来到 T0+10min，延迟 300 秒的回执此刻才到达（event_time 为 T0+5min）
    clock.advance(600)
    r = S.access("li", "IN", physical_in, delay=300)
    assert r.status_code == 201, r.text
    assert r.json()["delayed"] is True
    # 回执到达后：当前时刻判定在场
    assert presence.is_on_site(SessionLocal(), li_id, arrived) is True
    # 但仍不能“补出”回执到达之前的在场——T0+7min 依旧判为不在场
    assert presence.is_on_site(SessionLocal(), li_id, physical_in + timedelta(minutes=2)) is False


def test_sign_fails_before_delayed_receipt_arrives(client, auth, scenarios):
    """端到端：人已过闸但回执未到达时，签认必须被拒。"""
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    S.sign("zhang", hid, "OUTGOING", 1)
    clock.advance(300)
    # 物理上 5 分钟前已过闸，但此刻不提交回执（模拟网络中断）
    r = S.sign("li", hid, "INCOMING", 1)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "SIGNER_NOT_ON_SITE"
    # 回执延迟到达后才能签认
    physical = clock.now() - timedelta(seconds=240)
    assert S.access("li", "IN", physical, delay=240).status_code == 201
    assert S.sign("li", hid, "INCOMING", 1).status_code == 200


def test_duplicate_access_receipt_rejected(client, auth, scenarios):
    S = scenarios
    assert S.access("zhang", "OUT", clock.now()).status_code == 201
    r = S.access("zhang", "OUT", clock.now())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "DUPLICATE_ACCESS_EVENT"


# ----------------------------- 账号安全 -----------------------------

def test_no_shared_account_and_bad_login(client, auth):
    r = client.post("/api/auth/login", json={"username": "zhang", "password": "wrong"})
    assert r.status_code == 401
    # 无令牌访问被拒
    assert client.get("/api/permits").status_code == 401
    # 工人不能调班组长接口
    client.post("/api/auth/login", json={"username": "sun_none", "password": "123456"})
    r = client.get("/api/leader/overview", headers=auth.headers("sun_none"))
    assert r.status_code == 403


def test_leader_rejection_blocks_resume(client, auth, scenarios):
    S = scenarios
    hid = S.pause("zhang", 1, "li").json()["id"]
    S.access("li", "IN", clock.now())
    S.sign("zhang", hid, "OUTGOING", 1)
    S.sign("li", hid, "INCOMING", 1)
    _confirm_open_items(client, auth, 1)
    req_id = S.resume("li", hid).json()["id"]
    r = S.approve("leader", req_id, False, comment="隔离措施需重新挂牌")
    assert r.json()["status"] == "REJECTED"
    # 同一申请不可二次审批
    r2 = S.approve("leader", req_id, True)
    assert r2.status_code == 409
    ho = client.get(f"/api/handovers/{hid}", headers=auth.headers("leader")).json()
    assert ho["status"] == "FULLY_SIGNED"
    assert ho["resumed_at"] is None
