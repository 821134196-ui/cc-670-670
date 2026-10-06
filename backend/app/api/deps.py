"""令牌认证依赖：每个请求凭本人令牌解析出当前登录人。"""
from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import BusinessError
from app.models import AuthToken, Person


def current_person(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Person:
    if not authorization:
        raise BusinessError("UNAUTHORIZED", "缺少登录令牌", 401)
    token = authorization.removeprefix("Bearer ").strip()
    row = db.execute(
        select(AuthToken).where(AuthToken.token == token)
    ).scalar_one_or_none()
    if row is None:
        raise BusinessError("UNAUTHORIZED", "登录令牌无效或已过期", 401)
    person = db.get(Person, row.person_id)
    if person is None or not person.is_active:
        raise BusinessError("UNAUTHORIZED", "账号不可用", 401)
    return person


def require_leader(person: Person = Depends(current_person)) -> Person:
    from app.models import PersonRole
    if person.role != PersonRole.LEADER:
        raise BusinessError("FORBIDDEN", "需要班组长权限", 403)
    return person
