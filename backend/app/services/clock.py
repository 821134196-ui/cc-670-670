"""全局时钟：生产环境取系统时间，测试/演示可冻结或偏移。

业务代码一律通过 clock.now() 取时间，禁止在规则判断中直接调用 datetime.now()，
这样“签认时间、回执到达时间、空档计算”才能在测试里被确定性地验证。
"""
from __future__ import annotations

from datetime import datetime, timedelta


class Clock:
    def __init__(self) -> None:
        self._frozen_at: datetime | None = None
        self._offset: timedelta = timedelta(0)

    def now(self) -> datetime:
        if self._frozen_at is not None:
            return self._frozen_at + self._offset
        return datetime.now() + self._offset

    def freeze(self, at: datetime) -> None:
        self._frozen_at = at
        self._offset = timedelta(0)

    def advance(self, seconds: float) -> None:
        self._offset += timedelta(seconds=seconds)

    def reset(self) -> None:
        self._frozen_at = None
        self._offset = timedelta(0)


clock = Clock()
