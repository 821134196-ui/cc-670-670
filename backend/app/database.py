from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base

# 默认使用本地 SQLite 文件；测试通过 set_engine 覆盖为内存库。
DB_URL = "sqlite:///./guardian_handover.db"

engine = create_engine(
    DB_URL, connect_args={"check_same_thread": False}, future=True
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def set_engine(url: str) -> None:
    """测试/脚本切换数据库（如 sqlite:///:memory:）。"""
    global engine, SessionLocal
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
