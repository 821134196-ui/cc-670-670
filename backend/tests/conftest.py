"""pytest 公共夹具：独立 SQLite、冻结时钟、每个用例重建并播种。"""
import os

# 必须在导入 app.database 之前指定测试库
os.environ["DB_PATH"] = "/tmp/guardian_test.db"
os.environ["AUTO_INIT_DB"] = "0"
os.environ["AUTO_SEED"] = "0"

from datetime import datetime  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.clock import clock  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402

T0 = datetime(2026, 10, 6, 8, 0, 0)


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    clock.freeze(T0)
    seed_if_empty()
    with TestClient(app) as c:
        yield c
    clock.reset()


@pytest.fixture
def auth(client):
    """返回按用户名登录并附带令牌的请求辅助器。"""
    tokens: dict[str, str] = {}

    def login(username: str, password: str = "123456") -> str:
        r = client.post("/api/auth/login",
                        json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        tok = r.json()["token"]
        tokens[username] = tok
        return tok

    def headers(username: str) -> dict:
        tok = tokens.get(username) or login(username)
        return {"Authorization": f"Bearer {tok}"}

    class _AuthHelper:
        pass

    _AuthHelper.login = staticmethod(login)
    _AuthHelper.headers = staticmethod(headers)
    return _AuthHelper()


@pytest.fixture
def scenarios(client, auth):
    """常用业务动作封装，返回 (json, status) 便于断言。"""
    from datetime import timedelta

    def access(person_username: str, direction: str, at: datetime,
               delay: float = 0, note: str | None = None):
        # 人员 id 通过 /api/persons 查
        r = client.get("/api/persons", headers=auth.headers(person_username))
        pid = next(p["id"] for p in r.json() if p["username"] == person_username)
        return client.post(
            "/api/access-events",
            headers=auth.headers(person_username),
            json={
                "person_id": pid, "direction": direction,
                "event_time": at.isoformat(), "delay_seconds": delay,
                "note": note,
            },
        )

    def pause(operator: str, permit_id: int, incoming_username: str):
        r = client.get("/api/persons/guardians", headers=auth.headers(operator))
        pid = next(p["id"] for p in r.json() if p["username"] == incoming_username)
        return client.post(
            f"/api/permits/{permit_id}/pause",
            headers=auth.headers(operator),
            json={"incoming_guardian_id": pid},
        )

    def all_item_checks(permit_id: int, viewer: str) -> dict:
        page = client.get(
            f"/api/permits/{permit_id}/handover-page", headers=auth.headers(viewer)
        ).json()
        return {str(i["id"]): True for i in page["items"]
                if i["kind"] in ("RISK", "ISOLATION")}

    def sign(signer: str, handover_id: int, role: str, permit_id: int,
             checks: dict | None = None, nonce: str | None = None):
        checks = checks if checks is not None else all_item_checks(permit_id, signer)
        return client.post(
            f"/api/handovers/{handover_id}/sign",
            headers=auth.headers(signer),
            json={"role": role, "item_checks": checks, "nonce": nonce},
        )

    def resume(signer: str, handover_id: int):
        return client.post(
            f"/api/handovers/{handover_id}/request-resume",
            headers=auth.headers(signer),
        )

    def approve(leader: str, request_id: int, ok: bool = True, comment=None):
        return client.post(
            f"/api/resume-requests/{request_id}/decision",
            headers=auth.headers(leader),
            json={"approve": ok, "comment": comment},
        )

    class _ScenarioHelper:
        pass

    _ScenarioHelper.T0 = T0
    _ScenarioHelper.timedelta = timedelta
    _ScenarioHelper.access = staticmethod(access)
    _ScenarioHelper.pause = staticmethod(pause)
    _ScenarioHelper.sign = staticmethod(sign)
    _ScenarioHelper.resume = staticmethod(resume)
    _ScenarioHelper.approve = staticmethod(approve)
    _ScenarioHelper.all_item_checks = staticmethod(all_item_checks)
    return _ScenarioHelper()
