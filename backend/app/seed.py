"""初始化演示数据：人员、资质、许可、作业、门禁。

可重复执行：已有数据时跳过。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from . import models, timeutil
from .database import SessionLocal, init_db

CERT = "受限空间作业监护证"


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.scalar(select(models.Person).limit(1)):
            return

        t = timeutil.now()

        leader = models.Person(name="王建国", login="leader_wang", role="leader")
        li = models.Person(
            name="李安全", login="guardian_li", role="guardian",
            required_cert=CERT, cert_expires_at=t + timedelta(days=200),
        )
        zhao = models.Person(
            name="赵志强", login="guardian_zhao", role="guardian",
            required_cert=CERT, cert_expires_at=t + timedelta(days=30),
        )
        # 资质已过期的接班人：用于验证“资质不符不能复工”。
        sun = models.Person(
            name="孙卫民", login="guardian_sun", role="guardian",
            required_cert=CERT, cert_expires_at=t - timedelta(days=2),
        )
        # 证书类型不符的接班人。
        zhou = models.Person(
            name="周大勇", login="guardian_zhou", role="guardian",
            required_cert="动火作业监护证", cert_expires_at=t + timedelta(days=30),
        )
        worker = models.Person(name="吴师傅", login="worker_wu", role="worker")
        db.add_all([leader, li, zhao, sun, zhou, worker])
        db.flush()

        permit = models.Permit(
            code="P-2026-018",
            title="反应釜 R-201 受限空间清理作业许可",
            required_cert=CERT,
            valid_from=t - timedelta(hours=1),
            valid_until=t + timedelta(hours=8),
        )
        # 一份已过期许可，用于第二项作业的演示。
        expired_permit = models.Permit(
            code="P-2026-007",
            title="储罐 T-105 检测作业许可",
            required_cert=CERT,
            valid_from=t - timedelta(days=2),
            valid_until=t - timedelta(hours=3),
        )
        db.add_all([permit, expired_permit])
        db.flush()

        job = models.Job(
            name="反应釜 R-201 内壁清理",
            location="A区-R201",
            permit_id=permit.id,
            guardian_id=li.id,
            status="running",
        )
        job2 = models.Job(
            name="储罐 T-105 内检",
            location="B区-T105",
            permit_id=expired_permit.id,
            guardian_id=li.id,
            status="running",
        )
        db.add_all([job, job2])
        db.flush()

        db.add_all([
            models.RiskItem(job_id=job.id, content="釜内残留苯类气体，存在中毒/爆炸风险"),
            models.RiskItem(job_id=job.id, content="有限空间通风不良，存在缺氧窒息风险"),
            models.IsolationItem(job_id=job.id, content="进料阀 V-201 已加盲板隔离", intact=True),
            models.IsolationItem(job_id=job.id, content="搅拌电机 M-201 已断电挂牌（LOTO）", intact=True),
            models.IsolationItem(job_id=job.id, content="氮气吹扫管线已断开", intact=True),
            models.PendingItem(job_id=job.id, content="15:00 再次进行气体检测", done=False),
            models.PendingItem(job_id=job.id, content="清理结束后恢复盲板并复核", done=False),
        ])

        # 原监护人 1 小时前刷卡进入，当前在现场。
        db.add(
            models.AccessEvent(
                person_id=li.id, location="A区-R201", direction="in",
                occurred_at=t - timedelta(hours=1), received_at=t - timedelta(hours=1),
            )
        )
        db.commit()
        print("✅ 种子数据已写入：人员 6 名、许可 2 份、作业 2 项")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
