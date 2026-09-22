"""登录模块 P3 / P4：权限矩阵、CSRF、登录限流、账号管理与审计。

隔离：与 tests/ 下其它用例一致 —— 导入 server 之前把 DATA_DIR / UPLOADS_DIR / EXPORTS_DIR
指向本进程的临时目录；凡是需要「空库」的用例再用 fresh_db fixture 把 server.DB_PATH
临时指向另一个临时库，避免与其它用例文件共享同一份数据。

真实 runtime/ 全程只读，绝不写入（见文末隔离自检）。
"""

from __future__ import annotations

import atexit
import base64
import io
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="dc_permissions_")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")
# 注意：这里**不要**覆写 APP_AUTH_ENABLED / ADMIN_USER / ADMIN_PASSWORD。
# 它们是进程级环境变量，pytest 会先导入全部用例文件再跑用例，
# 在这里赋值会把 test_p5_auth.py 的期望值冲掉（表现为它的 Basic 校验与管理员引导失败）。
# 本文件只测权限判定本身，不需要这些开关。

import server  # noqa: E402


@atexit.register
def _cleanup() -> None:
    shutil.rmtree(_tmp, ignore_errors=True)


def _real_db_baseline() -> dict:
    """导入本文件时记录真实 runtime/ 库的字节数，作为整轮的对照基线。"""
    real = Path(__file__).resolve().parent.parent / "runtime" / "data" / "imports.db"
    return {"path": real, "size": real.stat().st_size if real.exists() else -1}


_REAL_DB_BASELINE = _real_db_baseline()


class FakeHandler:
    """只实现 json_response / 审计真正用到的那几个方法。"""

    def __init__(self, channel: str = "session", role: str = "viewer", headers: dict | None = None) -> None:
        self.auth_channel = channel
        if channel == "session" and role:
            self.auth_user = {
                "id": f"uid-{role}",
                "username": role,
                "displayName": role,
                "role": role,
            }
        self.headers = dict(headers or {})
        self.status: int | None = None
        self.response_headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()

    def send_response(self, status: int) -> None:
        self.status = int(status)

    def send_header(self, key: str, value: str) -> None:
        self.response_headers.append((key, str(value)))

    def end_headers(self) -> None:
        pass

    def request_ip(self) -> str:
        return "10.9.9.9"

    @property
    def payload(self) -> dict:
        return json.loads(self.wfile.getvalue().decode("utf-8"))


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """把 server.DB_PATH 临时指向一个全新的库（含全部元数据表）。"""
    db_path = tmp_path / "imports.db"
    monkeypatch.setattr(server, "DB_PATH", db_path)
    with server.connect_db():
        pass
    return db_path


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    server._login_rate_buckets.clear()
    yield
    server._login_rate_buckets.clear()


def _basic_header() -> str:
    """Basic 通道在 request_actor 里按通道名直接放行，头内容不参与判定。"""
    raw = f"{os.environ.get('ADMIN_USER', 'admin')}:{os.environ.get('ADMIN_PASSWORD', 'x')}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------- 1. 角色与路由表


def test_role_ordering():
    assert server.role_at_least("admin", "operator")
    assert server.role_at_least("operator", "viewer")
    assert not server.role_at_least("viewer", "operator")
    assert not server.role_at_least("viewer", "admin")
    # 未知角色（脏数据）一律不满足任何要求
    assert not server.role_at_least("superuser", "viewer")
    assert not server.role_at_least("", "viewer")


def test_unlisted_route_defaults_to_admin():
    """没登记的路由按 admin 处理：宁可漏放只读接口，也不让新接口默认对只读账号敞开。"""
    assert server.route_min_role("GET", "/api/some-future-endpoint") == "admin"
    assert server.route_min_role("get", "/api/tables") == "viewer"


@pytest.mark.parametrize(
    "method,path,viewer_ok",
    [
        ("GET", "/api/tables", True),
        ("GET", "/api/table", True),
        ("GET", "/api/logs", True),
        ("GET", "/api/jobs", True),
        ("GET", "/api/queries", True),
        ("GET", "/api/docs", True),
        ("POST", "/api/query/run", True),
        ("POST", "/api/import", False),
        ("POST", "/api/preview", False),
        ("POST", "/api/export/run", False),
        ("POST", "/api/connections/test", False),
        ("POST", "/api/queries", False),
        ("POST", "/api/jobs/run", False),
        ("DELETE", "/api/jobs", False),
        ("GET", "/api/audit-logs", False),
        ("POST", "/api/users", False),
    ],
)
def test_viewer_permission_matrix(fresh_db, method, path, viewer_ok):
    handler = FakeHandler(channel="session", role="viewer", headers={"X-DC-Request": "1"})
    allowed = server.guard_request(handler, method, path)
    assert allowed is viewer_ok, f"{method} {path} viewer 期望 {viewer_ok}，实际 {allowed}"
    if not viewer_ok:
        assert handler.status == 403
        assert "权限不足" in str(handler.payload.get("error"))


@pytest.mark.parametrize(
    "method,path",
    [("POST", "/api/connections"), ("DELETE", "/api/connections"), ("GET", "/api/audit-logs"), ("POST", "/api/users")],
)
def test_operator_cannot_reach_admin_routes(fresh_db, method, path):
    handler = FakeHandler(channel="session", role="operator", headers={"X-DC-Request": "1"})
    assert server.guard_request(handler, method, path) is False
    assert handler.status == 403
    assert handler.payload.get("needRole") == "admin"


@pytest.mark.parametrize("method,path", [("POST", "/api/import"), ("POST", "/api/connections"), ("DELETE", "/api/jobs")])
def test_admin_can_reach_everything(fresh_db, method, path):
    handler = FakeHandler(channel="session", role="admin", headers={"X-DC-Request": "1"})
    assert server.guard_request(handler, method, path) is True
    assert handler.status is None


def test_script_and_local_channels_are_unrestricted(fresh_db):
    """Basic（脚本 / 健康检查）与未启用认证的本机模式一律等同 admin。"""
    for channel in ("basic", "local"):
        handler = FakeHandler(channel=channel, role="")
        assert server.guard_request(handler, "DELETE", "/api/connections") is True
        assert server.guard_request(handler, "POST", "/api/users") is True
        actor = server.request_actor(handler)
        assert actor is not None
        assert actor["role"] == "admin"


def test_non_api_paths_are_not_role_checked():
    handler = FakeHandler(channel="session", role="viewer")
    assert server.guard_request(handler, "GET", "/index.html") is True


def test_login_page_is_reachable_without_session():
    """/login.html 必须在「未登录也能打开」的名单里。

    否则 require_auth 会把它 302 到 /login.html?next=/login.html，浏览器报
    ERR_TOO_MANY_REDIRECTS —— 登录页打不开，整个登录模块不可用。
    UI 侧由 acceptance/ui_replay_auth.js 的 AUTH-01/AUTH-02 做端到端兜底。
    """
    assert "/login.html" in server.AUTH_PUBLIC_PAGES


# ---------------------------------------------------------------- 2. CSRF


def test_csrf_blocks_session_write_without_header(fresh_db):
    handler = FakeHandler(channel="session", role="admin")
    assert server.guard_request(handler, "POST", "/api/import") is False
    assert handler.status == 403
    assert "X-DC-Request" in str(handler.payload.get("error"))


def test_csrf_allows_session_write_with_header(fresh_db):
    handler = FakeHandler(channel="session", role="admin", headers={"X-DC-Request": "1"})
    assert server.guard_request(handler, "POST", "/api/import") is True


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_csrf_skips_safe_methods(fresh_db, method):
    handler = FakeHandler(channel="session", role="admin")
    assert server.require_csrf(handler, method, "/api/tables") is True


def test_csrf_skips_basic_channel(fresh_db):
    """脚本走 Basic，不带自定义头也必须放行 —— acceptance/ 下 13 个复跑脚本零改造。"""
    handler = FakeHandler(channel="basic", role="", headers={"Authorization": _basic_header()})
    assert server.guard_request(handler, "POST", "/api/import") is True


@pytest.mark.parametrize("path", sorted(server.CSRF_EXEMPT_PATHS))
def test_csrf_exempt_paths(fresh_db, path):
    handler = FakeHandler(channel="session", role="viewer")
    assert server.require_csrf(handler, "POST", path) is True


def test_csrf_rejects_cross_origin_even_with_header(fresh_db):
    handler = FakeHandler(
        channel="session",
        role="admin",
        headers={"X-DC-Request": "1", "Host": "dc.example.com", "Origin": "https://evil.example.net"},
    )
    assert server.guard_request(handler, "POST", "/api/import") is False
    assert handler.status == 403
    assert "来源" in str(handler.payload.get("error"))


def test_csrf_accepts_same_origin(fresh_db):
    handler = FakeHandler(
        channel="session",
        role="admin",
        headers={"X-DC-Request": "1", "Host": "dc.example.com", "Origin": "https://dc.example.com"},
    )
    assert server.guard_request(handler, "POST", "/api/import") is True


def test_same_origin_allows_requests_without_origin(fresh_db):
    """非浏览器客户端（curl）不带 Origin / Referer，不能被误杀。"""
    handler = FakeHandler(channel="session", role="admin", headers={"Host": "dc.example.com"})
    assert server.same_origin_request(handler) is True


# ---------------------------------------------------------------- 3. 只读账号的 SQL 拦截


def test_readonly_blocked_statements_are_empty_for_queries():
    assert server.readonly_blocked_statements("select 1") == []
    assert server.readonly_blocked_statements("with t as (select 1 as a) select * from t") == []
    assert server.readonly_blocked_statements("select * from t where id in (select id from u)") == []


@pytest.mark.parametrize(
    "sql",
    [
        "create table p6_t (id int)",
        "insert into t (id) values (1)",
        "update t set id = 1 where id = 2",
        "delete from t where id = 1",
        "drop table t",
        "truncate table t",
        "update t set id = 1",
    ],
)
def test_readonly_blocks_write_statements(sql):
    blocked = server.readonly_blocked_statements(sql)
    assert blocked, sql
    assert blocked[0]["level"] in {"write", "danger"}


def test_readonly_reports_only_the_write_statement():
    blocked = server.readonly_blocked_statements("select 1; drop table t; select 2")
    assert len(blocked) == 1
    assert blocked[0]["level"] == "danger"
    assert "drop" in str(blocked[0]["sql"]).lower()


# ---------------------------------------------------------------- 4. 登录限流


def test_login_rate_limit_triggers_after_threshold():
    ip = "203.0.113.7"
    for _ in range(server.LOGIN_RATE_MAX):
        assert server.login_rate_retry_after(ip) == 0
    wait = server.login_rate_retry_after(ip)
    assert wait > 0
    assert wait <= server.LOGIN_RATE_WINDOW_SECONDS
    # 换个 IP 不受影响
    assert server.login_rate_retry_after("203.0.113.8") == 0


def test_login_rate_limit_window_expires():
    ip = "203.0.113.9"
    server._login_rate_buckets[ip] = [0.0] * server.LOGIN_RATE_MAX
    assert server.login_rate_retry_after(ip) == 0


# ---------------------------------------------------------------- 5. 账号管理


def _create(fresh_db, username, role="viewer", password="P6-Pass-2026"):
    return server.create_user({"username": username, "displayName": username, "password": password, "role": role})


def test_create_user_and_public_fields(fresh_db):
    user = _create(fresh_db, "p6_viewer", "viewer")
    assert user["username"] == "p6_viewer"
    assert user["role"] == "viewer"
    assert user["enabled"] is True
    # 对外结构里绝不能出现口令哈希
    assert "password_hash" not in user
    assert "passwordHash" not in user
    row = server.find_user("p6_viewer")
    assert row is not None
    assert str(row["password_hash"]).startswith("scrypt$")
    assert "P6-Pass-2026" not in str(row["password_hash"])


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"username": "ab", "password": "P6-Pass-2026", "role": "viewer"}, "用户名至少"),
        ({"username": "p6 bad name", "password": "P6-Pass-2026", "role": "viewer"}, "只能包含"),
        ({"username": "p6_weak", "password": "short", "role": "viewer"}, "密码至少"),
        ({"username": "p6_badrole", "password": "P6-Pass-2026", "role": "root"}, "角色不合法"),
    ],
)
def test_create_user_validation(fresh_db, payload, message):
    with pytest.raises(ValueError) as excinfo:
        server.create_user(payload)
    assert message in str(excinfo.value)


def test_create_user_rejects_duplicate(fresh_db):
    _create(fresh_db, "p6_dup")
    with pytest.raises(ValueError) as excinfo:
        _create(fresh_db, "p6_dup")
    assert "已存在" in str(excinfo.value)


def test_list_users_has_no_secret_and_counts_sessions(fresh_db):
    user = _create(fresh_db, "p6_sessions", "operator")
    server.create_session(str(user["id"]), "10.0.0.1", "pytest", False)
    items = {item["username"]: item for item in server.list_users()}
    assert items["p6_sessions"]["activeSessions"] == 1
    assert "password_hash" not in items["p6_sessions"]


def test_update_user_role_and_disable_invalidates_sessions(fresh_db):
    user = _create(fresh_db, "p6_target", "operator")
    server.create_session(str(user["id"]), "10.0.0.1", "pytest", False)
    updated, changes = server.update_user({"id": user["id"], "role": "admin"}, {"id": "someone-else", "role": "admin"})
    assert updated["role"] == "admin"
    assert any("角色" in item for item in changes)

    # 先补一名管理员，否则下面「停用」会撞上「最后一名管理员」保护
    _create(fresh_db, "p6_backup_admin", "admin")
    server.create_session(str(user["id"]), "10.0.0.2", "pytest", False)
    updated, _ = server.update_user({"id": user["id"], "enabled": False}, {"id": "someone-else", "role": "admin"})
    assert updated["enabled"] is False
    with server.connect_db() as conn:
        rows = conn.execute("select count(*) from _sessions where user_id = ?", (str(user["id"]),)).fetchone()
    assert int(rows[0]) == 0


def test_password_reset_invalidates_sessions(fresh_db):
    user = _create(fresh_db, "p6_reset", "operator")
    server.create_session(str(user["id"]), "10.0.0.3", "pytest", False)
    server.update_user({"id": user["id"], "password": "P6-New-Pass-2026"}, {"id": "x", "role": "admin"})
    with server.connect_db() as conn:
        rows = conn.execute("select count(*) from _sessions where user_id = ?", (str(user["id"]),)).fetchone()
    assert int(rows[0]) == 0
    row = server.find_user("p6_reset")
    assert server.verify_password("P6-New-Pass-2026", str(row["password_hash"]))


def test_last_admin_cannot_be_demoted_disabled_or_deleted(fresh_db):
    admin = _create(fresh_db, "p6_only_admin", "admin")
    actor = {"id": "other-admin", "username": "other", "role": "admin"}

    with pytest.raises(ValueError) as excinfo:
        server.update_user({"id": admin["id"], "role": "viewer"}, actor)
    assert "最后一名" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        server.update_user({"id": admin["id"], "enabled": False}, actor)
    assert "最后一名" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        server.delete_user(str(admin["id"]), actor)
    assert "最后一名" in str(excinfo.value)

    # 再补一名管理员后，上面三条限制解除
    _create(fresh_db, "p6_second_admin", "admin")
    updated, _ = server.update_user({"id": admin["id"], "role": "viewer"}, actor)
    assert updated["role"] == "viewer"


def test_cannot_disable_or_delete_self(fresh_db):
    user = _create(fresh_db, "p6_self", "admin")
    _create(fresh_db, "p6_other_admin", "admin")  # 排除「最后一名管理员」这一条，单独验自锁保护
    actor = {"id": user["id"], "username": "p6_self", "role": "admin"}
    with pytest.raises(ValueError) as excinfo:
        server.update_user({"id": user["id"], "enabled": False}, actor)
    assert "当前登录" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        server.delete_user(str(user["id"]), actor)
    assert "当前登录" in str(excinfo.value)


def test_delete_user_removes_sessions(fresh_db):
    _create(fresh_db, "p6_keep_admin", "admin")
    user = _create(fresh_db, "p6_doomed", "operator")
    server.create_session(str(user["id"]), "10.0.0.4", "pytest", False)
    removed = server.delete_user(str(user["id"]), {"id": "admin-x", "role": "admin"})
    assert removed["username"] == "p6_doomed"
    assert server.find_user("p6_doomed") is None
    with server.connect_db() as conn:
        rows = conn.execute("select count(*) from _sessions where user_id = ?", (str(user["id"]),)).fetchone()
    assert int(rows[0]) == 0


def test_update_unknown_user_and_missing_id(fresh_db):
    with pytest.raises(ValueError) as excinfo:
        server.update_user({"role": "viewer"}, None)
    assert "缺少用户 ID" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        server.update_user({"id": "nope", "role": "viewer"}, None)
    assert "用户不存在" in str(excinfo.value)


# ---------------------------------------------------------------- 6. 审计


def _audit(user, action, target="", detail="", status="ok"):
    server.audit_log(user, action, target, detail, "10.0.0.9", status)


def test_audit_log_writes_row(fresh_db):
    _audit({"id": "u1", "username": "alice"}, "login", "alice", "登录成功")
    payload = server.query_audit_logs(username="alice")
    assert payload["total"] == 1
    row = payload["logs"][0]
    assert row["action"] == "login"
    assert row["ip"] == "10.0.0.9"
    assert row["status"] == "ok"


def test_audit_log_never_breaks_main_flow(fresh_db, monkeypatch):
    """审计写失败不能把主流程带崩。"""

    def boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(server, "connect_db", boom)
    server.audit_log({"username": "alice"}, "login", "", "")  # 不抛异常即通过


def test_audit_filters_and_paging(fresh_db):
    _audit({"id": "u1", "username": "alice"}, "import.run", "t_orders", "3 个文件")
    _audit({"id": "u1", "username": "alice"}, "login", "", "登录成功")
    _audit({"id": "u2", "username": "bob"}, "query.denied", "conn-1", "写语句被拒", "denied")
    _audit({"id": "u2", "username": "bob"}, "login", "", "密码错误", "denied")

    assert server.query_audit_logs()["total"] == 4
    assert server.query_audit_logs(username="alice")["total"] == 2
    assert server.query_audit_logs(action="login")["total"] == 2
    assert server.query_audit_logs(status="denied")["total"] == 2
    assert server.query_audit_logs(keyword="t_orders")["total"] == 1
    assert server.query_audit_logs(keyword="被拒")["total"] == 1

    page = server.query_audit_logs(limit=2, offset=0)
    assert len(page["logs"]) == 2
    assert page["total"] == 4
    rest = server.query_audit_logs(limit=2, offset=2)
    assert len(rest["logs"]) == 2
    ids = {row["id"] for row in page["logs"]} & {row["id"] for row in rest["logs"]}
    assert ids == set()
    assert "login" in server.query_audit_logs()["actions"]


def test_audit_limit_is_clamped(fresh_db):
    with server.connect_db() as conn:
        for index in range(5):
            conn.execute(
                "insert into _audit_logs (id, created_at, user_id, username, action, target, detail, ip, status)"
                " values (?, ?, '', 'alice', 'login', '', '', '1.1.1.1', 'ok')",
                (f"row-{index}", server.now_text()),
            )
    assert len(server.query_audit_logs(limit=9999)["logs"]) == 5
    assert len(server.query_audit_logs(limit=2)["logs"]) == 2


def test_prune_audit_logs_keeps_recent(fresh_db):
    old = "2000-01-01 00:00:00"
    recent = server.now_text()
    with server.connect_db() as conn:
        for row_id, stamp in (("old-1", old), ("fresh-1", recent)):
            conn.execute(
                "insert into _audit_logs (id, created_at, user_id, username, action, target, detail, ip, status)"
                " values (?, ?, '', 'alice', 'login', '', '', '1.1.1.1', 'ok')",
                (row_id, stamp),
            )
    removed = server.prune_audit_logs()
    assert removed == 1
    remaining = {row["id"] for row in server.query_audit_logs()["logs"]}
    assert remaining == {"fresh-1"}


def test_audit_retention_default_is_90_days(monkeypatch):
    monkeypatch.delenv("DC_AUDIT_RETENTION_DAYS", raising=False)
    assert server.audit_retention_days() == 90
    monkeypatch.setenv("DC_AUDIT_RETENTION_DAYS", "30")
    assert server.audit_retention_days() == 30
    monkeypatch.setenv("DC_AUDIT_RETENTION_DAYS", "not-a-number")
    assert server.audit_retention_days() == 90


# ---------------------------------------------------------------- 7. 隔离自检


def test_runtime_database_is_untouched():
    """所有用例都必须落在系统临时目录里的库，真实 runtime/ 不能被写。"""
    db_path = Path(server.DB_PATH).resolve()
    assert Path(tempfile.gettempdir()).resolve() in db_path.parents, db_path
    real = _REAL_DB_BASELINE["path"]
    if not real.exists():
        return
    # 与「本文件被导入时」的基线对比：整轮用例跑完，生产库不能变大
    assert real.stat().st_size == _REAL_DB_BASELINE["size"], real.stat().st_size
    conn = sqlite3.connect(f"file:{real.as_posix()}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
        if "p6_must_not_exist" in tables:
            pytest.fail("生产库里出现了测试专用表")
    finally:
        conn.close()
