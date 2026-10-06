"""统一时间源：全系统使用时区感知的 UTC 时间。

测试可通过 freeze/advance/reset 控制时钟，使门禁刷卡时刻、签认时刻与
在场/空档判定落在同一时间线上，便于精确验证延迟回执与换班空档。
"""
from datetime import datetime, timedelta, timezone

# 虚拟时钟：None 表示跟随真实时间；否则为冻结的基准时刻 + 已推进量。
_frozen: datetime | None = None
_offset = timedelta(0)


def now() -> datetime:
    if _frozen is None:
        return datetime.now(timezone.utc)
    return _frozen + _offset


def freeze(at: datetime | None = None) -> datetime:
    """冻结时钟到指定时刻（默认当前真实时间）。"""
    global _frozen, _offset
    _frozen = at or datetime.now(timezone.utc)
    if _frozen.tzinfo is None:
        _frozen = _frozen.replace(tzinfo=timezone.utc)
    _offset = timedelta(0)
    return now()


def advance(seconds: float) -> datetime:
    """把冻结时钟向前推进若干秒。"""
    global _offset
    if _frozen is None:
        raise RuntimeError("时钟未冻结，无法 advance")
    _offset += timedelta(seconds=seconds)
    return now()


def reset() -> None:
    global _frozen, _offset
    _frozen = None
    _offset = timedelta(0)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()
