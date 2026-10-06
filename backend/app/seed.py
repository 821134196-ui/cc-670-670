"""演示种子数据：账号、资质、许可、风险/隔离、门禁初始记录。

账号（密码统一见下）：
  leader      王班长（班组长，审批复工）
  zhang       张建国（现任监护人，资质有效）
  li          李文斌（接班候选人，资质有效）
  chen        陈卫东（另一作业监护人）
  zhao_expired 赵德柱（监护资质已过期，用于资质拦截演示）
  sun_none      孙小年（无监护资质的普通工人）
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, init_db
from app.models import (
    AccessEvent,
    ChecklistItem,
    Direction,
    ItemKind,
    ItemState,
    Person,
    PersonRole,
    PermitStatus,
    WorkPermit,
    WorkStatus,
)
from app.services.clock import clock

PASSWORD = "123456"


def seed_if_empty() -> None:
    db: Session = SessionLocal()
    try:
        if db.execute(select(Person)).first() is not None:
            return
        now = clock.now()

        leader = Person(
            username="leader", password=PASSWORD, name="王海涛",
            role=PersonRole.LEADER,
        )
        zhang = Person(
            username="zhang", password=PASSWORD, name="张建国",
            role=PersonRole.GUARDIAN, qualification_type="受限空间监护证",
            qualification_valid_until=now + timedelta(days=400),
        )
        li = Person(
            username="li", password=PASSWORD, name="李文斌",
            role=PersonRole.GUARDIAN, qualification_type="受限空间监护证",
            qualification_valid_until=now + timedelta(days=200),
        )
        chen = Person(
            username="chen", password=PASSWORD, name="陈卫东",
            role=PersonRole.GUARDIAN, qualification_type="动火监护证",
            qualification_valid_until=now + timedelta(days=300),
        )
        zhao = Person(
            username="zhao_expired", password=PASSWORD, name="赵德柱",
            role=PersonRole.GUARDIAN, qualification_type="受限空间监护证",
            qualification_valid_until=now - timedelta(days=2),  # 已过期
        )
        sun = Person(
            username="sun_none", password=PASSWORD, name="孙小年",
            role=PersonRole.WORKER,
        )
        db.add_all([leader, zhang, li, chen, zhao, sun])
        db.flush()

        # 许可 1：受限空间作业，进行中，8 小时后到期
        p1 = WorkPermit(
            permit_no="P-2026-001",
            title="2#反应釜清罐受限空间作业",
            work_type="受限空间作业",
            location="二期装置区 2#反应釜",
            status=PermitStatus.ACTIVE,
            valid_from=now - timedelta(hours=2),
            valid_until=now + timedelta(hours=8),
            work_status=WorkStatus.IN_PROGRESS,
            current_guardian_id=zhang.id,
            created_at=now,
        )
        db.add(p1)
        db.flush()
        db.add_all([
            ChecklistItem(permit_id=p1.id, kind=ItemKind.RISK, seq=1,
                          content="釜内残余苯类气体中毒/窒息风险", state=ItemState.CONFIRMED),
            ChecklistItem(permit_id=p1.id, kind=ItemKind.RISK, seq=2,
                          content="清罐工具碰撞产生火花的燃爆风险", state=ItemState.CONFIRMED),
            ChecklistItem(permit_id=p1.id, kind=ItemKind.ISOLATION, seq=3,
                          content="进料阀 V-201 已加盲板并挂牌", state=ItemState.CONFIRMED),
            ChecklistItem(permit_id=p1.id, kind=ItemKind.ISOLATION, seq=4,
                          content="搅拌器电源 M-205 已停电上锁（LOTO）", state=ItemState.CONFIRMED),
            ChecklistItem(permit_id=p1.id, kind=ItemKind.ISOLATION, seq=5,
                          content="强制通风机持续运行，氧含量 20.4%", state=ItemState.CONFIRMED),
            ChecklistItem(permit_id=p1.id, kind=ItemKind.RISK, seq=6,
                          content="气体检测仪待接班时再次读数记录", state=ItemState.OPEN),
        ])

        # 许可 2：动火作业，许可 30 分钟后到期（用于许可过期拦截演示）
        p2 = WorkPermit(
            permit_no="P-2026-002",
            title="管廊 3 层管线打磨动火作业",
            work_type="动火作业",
            location="北区管廊 3F-12 段",
            status=PermitStatus.ACTIVE,
            valid_from=now - timedelta(hours=7, minutes=30),
            valid_until=now + timedelta(minutes=30),
            work_status=WorkStatus.IN_PROGRESS,
            current_guardian_id=chen.id,
            created_at=now,
        )
        db.add(p2)
        db.flush()
        db.add_all([
            ChecklistItem(permit_id=p2.id, kind=ItemKind.RISK, seq=1,
                          content="周边可燃物引燃风险", state=ItemState.CONFIRMED),
            ChecklistItem(permit_id=p2.id, kind=ItemKind.ISOLATION, seq=2,
                          content="动火点 10 米内可燃物已清理，灭火器 2 具就位",
                          state=ItemState.CONFIRMED),
            ChecklistItem(permit_id=p2.id, kind=ItemKind.ISOLATION, seq=3,
                          content="地漏 D-118 已封堵", state=ItemState.CONFIRMED),
        ])

        # 初始门禁：两位现任监护人今早已入场
        db.add_all([
            AccessEvent(person_id=zhang.id, direction=Direction.IN,
                        event_time=now - timedelta(hours=2, minutes=5),
                        received_at=now - timedelta(hours=2, minutes=5)),
            AccessEvent(person_id=chen.id, direction=Direction.IN,
                        event_time=now - timedelta(hours=7, minutes=35),
                        received_at=now - timedelta(hours=7, minutes=35)),
        ])
        db.commit()
    finally:
        db.close()


def main() -> None:
    init_db()
    seed_if_empty()
    print("seed done")


if __name__ == "__main__":
    main()
