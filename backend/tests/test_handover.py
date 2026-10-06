"""核心业务场景验证：换班空档、资质、许可、签认幂等等。

所有用例运行在冻结时钟上（见 conftest 的 clock fixture），用 timeutil.advance
推进时间，从而精确控制刷卡、离场、到场与签认的先后顺序。
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import models, services, timeutil
from tests.conftest import make_job, make_permit, make_person, swipe


def _out_in(db, outgoing, incoming, loc, *, out_at=None, in_at=None):
    db.add(swipe(db, outgoing, "out", loc, out_at or timeutil.now()))
    if incoming is not None:
        db.add(swipe(db, incoming, "in", loc, in_at or timeutil.now()))
    db.commit()


# ───────────────────── 场景一：完整正常交接与复工 ─────────────────────

def test_happy_path_handover_and_resume(db):
    """停工 → 原监护人签认离场 → 空档 → 接班人到场签认 → 申请复工 → 批准。"""
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li)
    loc = job.location

    db.add(swipe(db, li, "in", loc, timeutil.now() - timedelta(hours=1)))
    db.commit()

    h = services.start_handover(db, job, li, zhao)
    assert job.status == "paused"

    # 原监护人离场前先签认。
    services.sign_off(db, h, "outgoing", li, True, True)
    _out_in(db, li, None, loc)                 # 原监护人离场
    services.annotate_handover_access(db, db.scalars(
        select(models.AccessEvent).where(models.AccessEvent.direction == "out")).all()[-1])

    timeutil.advance(60)                        # 60 秒无人监护空档
    db.add(swipe(db, zhao, "in", loc, timeutil.now()))
    db.commit()
    services.annotate_handover_access(db, db.scalars(
        select(models.AccessEvent).where(models.AccessEvent.person_id == zhao.id)).all()[-1])

    gap = services.unattended_gap(db, h)
    assert gap.seconds == pytest.approx(60, abs=1)
    assert gap.open is False

    services.sign_off(db, h, "incoming", zhao, True, True)
    assert h.status == "completed"

    req = services.request_resume(db, h, zhao)
    leader = make_person(db, "王班长", "leader", role="leader", cert=None, exp_in_days=None)
    services.decide_resume(db, req, leader, True)

    assert job.status == "running"
    assert job.guardian_id == zhao.id
    assert h.status == "resumed" and h.active == 0
    assert h.unattended_seconds == pytest.approx(60, abs=1)


# ───────────────────── 场景二：换班空档（无人监护时间段） ─────────────────────

def test_open_gap_when_incoming_not_arrived(db):
    """接班人一直未到场 → 空档持续，gap.open=True。"""
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li)
    loc = job.location
    db.add(swipe(db, li, "in", loc, timeutil.now() - timedelta(hours=1)))
    db.commit()

    h = services.start_handover(db, job, li, zhao)
    db.add(swipe(db, li, "out", loc, timeutil.now()))
    db.commit()
    timeutil.advance(300)

    gap = services.unattended_gap(db, h)
    assert gap.open is True
    assert gap.seconds == pytest.approx(300, abs=1)
    assert gap.end is None


def test_no_gap_when_overlap(db):
    """接班人先到、原监护人后走（当面重叠交接）→ 无空档。"""
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li)
    loc = job.location
    db.add(swipe(db, li, "in", loc, timeutil.now() - timedelta(hours=1)))
    db.commit()

    h = services.start_handover(db, job, li, zhao)
    db.add(swipe(db, zhao, "in", loc, timeutil.now()))
    db.commit()
    services.sign_off(db, h, "outgoing", li, True, True)
    services.sign_off(db, h, "incoming", zhao, True, True)
    timeutil.advance(120)
    db.add(swipe(db, li, "out", loc, timeutil.now()))
    db.commit()

    gap = services.unattended_gap(db, h)
    assert gap.seconds == 0 and gap.open is False


def test_no_gap_when_outgoing_never_leaves(db):
    """原监护人始终未离场 → 无空档。"""
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li)
    h = services.start_handover(db, job, li, zhao)
    assert services.unattended_gap(db, h).seconds == 0


# ───────────────────── 场景三：接班人资质过期 → 不能复工 ─────────────────────

def _completed_handover(db, outgoing, incoming, job, loc):
    """双方在场并完成签认的交接（不刻意制造空档）。"""
    db.add_all([
        swipe(db, outgoing, "in", loc, timeutil.now() - timedelta(hours=1)),
        swipe(db, incoming, "in", loc, timeutil.now()),
    ])
    db.commit()
    h = services.start_handover(db, job, outgoing, incoming)
    services.sign_off(db, h, "outgoing", outgoing, True, True)
    services.sign_off(db, h, "incoming", incoming, True, True)
    return h


def test_expired_cert_blocks_resume(db):
    li = make_person(db, "李", "li")
    sun = make_person(db, "孙", "sun", exp_in_days=-2)  # 资质过期
    job = make_job(db, li)
    leader = make_person(db, "王", "leader", role="leader", cert=None, exp_in_days=None)

    h = _completed_handover(db, li, sun, job, job.location)
    req = services.request_resume(db, h, sun)

    codes = {b["code"] for b in services.resume_blockers(db, h)}
    assert "incoming_unqualified" in codes

    with pytest.raises(services.BusinessError) as e:
        services.decide_resume(db, req, leader, True)
    assert e.value.code == "resume_blocked"
    assert job.status == "paused"  # 班组长也无法放行


def test_wrong_cert_type_blocks_resume(db):
    li = make_person(db, "李", "li")
    zhou = make_person(db, "周", "zhou", cert="动火作业监护证")
    job = make_job(db, li)
    h = _completed_handover(db, li, zhou, job, job.location)
    assert "incoming_unqualified" in {b["code"] for b in services.resume_blockers(db, h)}


def test_cert_renewed_then_resume_allowed(db):
    """资质续期后阻断解除，证明过期判定确实来自资质。"""
    li = make_person(db, "李", "li")
    sun = make_person(db, "孙", "sun", exp_in_days=-2)
    job = make_job(db, li)
    h = _completed_handover(db, li, sun, job, job.location)
    assert any(b["code"] == "incoming_unqualified" for b in services.resume_blockers(db, h))

    sun.cert_expires_at = timeutil.now() + timedelta(days=10)
    db.commit()
    assert not any(b["code"] == "incoming_unqualified"
                   for b in services.resume_blockers(db, h))


# ───────────────────── 场景四：许可过期 / 撤销 → 不能复工 ─────────────────────

def test_expired_permit_blocks_resume(db):
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li, permit=make_permit(db, code="P-OLD", valid=False))
    h = _completed_handover(db, li, zhao, job, job.location)
    assert "permit_invalid" in {b["code"] for b in services.resume_blockers(db, h)}


def test_revoked_permit_blocks_resume(db):
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    permit = make_permit(db)
    permit.revoked = True
    db.commit()
    job = make_job(db, li, permit=permit)
    h = _completed_handover(db, li, zhao, job, job.location)
    assert any(b["code"] == "permit_invalid" for b in services.resume_blockers(db, h))


# ───────────────────── 场景五：现场无人监护 → 不能复工 ─────────────────────

def test_incoming_absent_blocks_resume(db):
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li)
    loc = job.location
    db.add(swipe(db, li, "in", loc, timeutil.now() - timedelta(hours=1)))
    db.commit()

    h = services.start_handover(db, job, li, zhao)
    services.sign_off(db, h, "outgoing", li, True, True)

    # 接班人从未刷卡进入现场 → 签认被拒。
    with pytest.raises(services.BusinessError) as e:
        services.sign_off(db, h, "incoming", zhao, True, True)
    assert e.value.code == "signer_absent"

    codes = {b["code"] for b in services.resume_blockers(db, h)}
    assert "no_guardian_on_site" in codes


# ───────────────────── 场景六：重复签认幂等 / 重复交接 ─────────────────────

def test_duplicate_signoff_is_idempotent(db):
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li)
    loc = job.location
    db.add_all([
        swipe(db, li, "in", loc, timeutil.now() - timedelta(hours=1)),
        swipe(db, zhao, "in", loc, timeutil.now()),
    ])
    db.commit()

    h = services.start_handover(db, job, li, zhao)
    s1 = services.sign_off(db, h, "outgoing", li, True, True)
    s2 = services.sign_off(db, h, "outgoing", li, True, True)  # 重复提交
    assert s1.id == s2.id

    signs = db.scalars(select(models.SignOff).where(
        models.SignOff.handover_id == h.id)).all()
    assert len(signs) == 1  # 重复提交没有产生第二条，保留首次签认时间


def test_duplicate_handover_rejected(db):
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li)
    services.start_handover(db, job, li, zhao)
    with pytest.raises(services.BusinessError) as e:
        services.start_handover(db, job, li, zhao)
    assert e.value.code == "handover_exists"
    assert len(db.scalars(select(models.Handover)).all()) == 1


def test_second_handover_allowed_after_resume(db):
    """复工后同一作业允许新一轮交接（部分唯一索引不阻挡 active=0）。"""
    li, zhao = make_person(db, "李", "li"), make_person(db, "赵", "zhao")
    qian = make_person(db, "钱", "qian")
    job = make_job(db, li)
    leader = make_person(db, "王", "leader", role="leader", cert=None, exp_in_days=None)
    loc = job.location
    db.add_all([
        swipe(db, li, "in", loc, timeutil.now() - timedelta(hours=2)),
        swipe(db, zhao, "in", loc, timeutil.now()),
    ])
    db.commit()

    h1 = services.start_handover(db, job, li, zhao)
    services.sign_off(db, h1, "outgoing", li, True, True)
    services.sign_off(db, h1, "incoming", zhao, True, True)
    services.decide_resume(db, services.request_resume(db, h1, zhao), leader, True)

    timeutil.advance(10)
    db.add(swipe(db, qian, "in", loc, timeutil.now()))
    db.commit()
    h2 = services.start_handover(db, job, zhao, qian)
    assert h2.id != h1.id and h1.active == 0 and h2.active == 1


# ───────────────────── 场景七：禁止共用账号 / 代签 ─────────────────────

def test_cannot_sign_for_other_role(db):
    """原监护人用自己账号尝试替接班人签 → 拒绝。"""
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li)
    loc = job.location
    db.add_all([
        swipe(db, li, "in", loc, timeutil.now() - timedelta(hours=1)),
        swipe(db, zhao, "in", loc, timeutil.now()),
    ])
    db.commit()
    h = services.start_handover(db, job, li, zhao)
    services.sign_off(db, h, "outgoing", li, True, True)
    with pytest.raises(services.BusinessError) as e:
        services.sign_off(db, h, "incoming", li, True, True)  # 李替赵签
    assert e.value.code == "signer_mismatch"


def test_same_person_handover_rejected(db):
    li = make_person(db, "李", "li")
    job = make_job(db, li)
    with pytest.raises(services.BusinessError):
        services.start_handover(db, job, li, li)


# ───────────────────── 场景八：延迟门禁回执不得回填在场 ─────────────────────

def test_late_receipt_does_not_fabricate_presence(db):
    """只有一条迟到的 out 回执、从无 in：不得臆造进入时间，判为不在场。"""
    zhao = make_person(db, "赵", "zhao")
    loc = "A区-测试点"
    db.add(swipe(db, zhao, "out", loc, timeutil.now() - timedelta(minutes=10)))
    db.commit()
    assert services.is_present(db, zhao.id, loc) is False
    assert services.presence_intervals(db, zhao.id, loc) == []


def test_late_in_receipt_extends_from_actual_swipe(db):
    """迟到的 in 回执按实际刷卡时间起算，而非回执到达时间。"""
    zhao = make_person(db, "赵", "zhao")
    loc = "A区-测试点"
    actual_in = timeutil.now() - timedelta(minutes=10)
    db.add(swipe(db, zhao, "in", loc, actual_in))  # received_at=现在
    db.commit()
    assert services.is_present(db, zhao.id, loc, actual_in + timedelta(minutes=5))


def test_gap_uses_actual_swipe_time_not_receipt(db):
    """两条回执同一时刻才到达，空档仍按真实刷卡时间计算，不被抹掉。"""
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li)
    loc = job.location
    db.add(swipe(db, li, "in", loc, timeutil.now() - timedelta(hours=1)))
    db.commit()

    h = services.start_handover(db, job, li, zhao)
    t0 = timeutil.now()
    timeutil.advance(180)  # 回执 180 秒后才到达
    # 迟来的回执：离场实际发生在 t0，到场实际发生在 t0+180。
    db.add_all([
        swipe(db, li, "out", loc, t0),
        swipe(db, zhao, "in", loc, t0 + timedelta(seconds=180)),
    ])
    db.commit()
    assert services.unattended_gap(db, h).seconds == pytest.approx(180, abs=1)


# ───────────────────── 场景九：交接历史时间线顺序 ─────────────────────

def test_timeline_order(db):
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    leader = make_person(db, "王", "leader", role="leader", cert=None, exp_in_days=None)
    job = make_job(db, li)
    loc = job.location
    db.add(swipe(db, li, "in", loc, timeutil.now() - timedelta(hours=1)))
    db.commit()

    h = services.start_handover(db, job, li, zhao)

    # 原监护人离场前完成本人签认。
    services.sign_off(db, h, "outgoing", li, True, True)

    db.add(swipe(db, li, "out", loc, timeutil.now()))
    db.commit()
    services.annotate_handover_access(db, db.scalars(
        select(models.AccessEvent).where(models.AccessEvent.direction == "out")).all()[-1])

    timeutil.advance(30)
    db.add(swipe(db, zhao, "in", loc, timeutil.now()))
    db.commit()
    services.annotate_handover_access(db, db.scalars(
        select(models.AccessEvent).where(models.AccessEvent.person_id == zhao.id)).all()[-1])

    services.sign_off(db, h, "incoming", zhao, True, True)
    services.decide_resume(db, services.request_resume(db, h, zhao), leader, True)

    kinds = [e.kind for e in db.scalars(
        select(models.TimelineEvent).where(models.TimelineEvent.job_id == job.id)
        .order_by(models.TimelineEvent.at, models.TimelineEvent.id)).all()]
    assert kinds[0] == "paused"
    assert kinds.index("outgoing_left") < kinds.index("incoming_arrived")
    assert kinds.index("incoming_arrived") < kinds.index("resumed")
    assert kinds[-1] == "resumed"
    assert "resume_requested" in kinds


# ───────────────────── 场景十：未确认风险/隔离不能签认 ─────────────────────

def test_must_confirm_risks_and_isolations(db):
    li = make_person(db, "李", "li")
    zhao = make_person(db, "赵", "zhao")
    job = make_job(db, li)
    loc = job.location
    db.add(swipe(db, li, "in", loc, timeutil.now() - timedelta(hours=1)))
    db.commit()
    h = services.start_handover(db, job, li, zhao)
    with pytest.raises(services.BusinessError) as e:
        services.sign_off(db, h, "outgoing", li, False, True)
    assert e.value.code == "not_confirmed"


def test_isolation_compromise_visible(db):
    li = make_person(db, "李", "li")
    job = make_job(db, li)
    job.isolations[0].intact = False
    db.commit()
    assert job.isolations[0].intact is False
