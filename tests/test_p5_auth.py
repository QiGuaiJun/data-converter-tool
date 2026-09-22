"""登录模块（P1）专项回归测试（2026-09-22）。

覆盖设计文档 `docs/登录功能-实施设计-20260922.md` 的 P1 契约：

1. 密码一律 scrypt 加盐哈希；校验为常量时间比较
2. 会话：token 只在库中保存 sha256、可过期、可停用、可登出销毁
3. 双通道认证：
   * Cookie 会话（浏览器）—— 未登录访问页面跳登录页、访问接口 401
   * HTTP Basic（脚本 / 健康检查）—— 保证 acceptance/ 下既有复跑脚本零改造
   * `/api/ping` 始终豁免（容器健康检查依赖它）
4. 开放注册：默认 `viewer` 角色、可选邀请码、密码/用户名校验
5. 防爆破：连续失败达阈值后锁定，锁定期内正确密码也拒绝
6. 审计：登录 / 登出 / 注册 / 失败尝试都落 `_audit_logs`
7. 首个管理员引导：`_users` 为空且配置了 ADMIN_PASSWORD 时自动创建 admin

隔离：导入 server 前把 DATA_DIR / UPLOADS_DIR / EXPORTS_DIR 指向临时目录，
并开启 APP_AUTH_ENABLED（否则认证不生效），绝不触碰真实 runtime/。
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="dc_auth_")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")
os.environ["APP_AUTH_ENABLED"] = "true"
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "AdminPass!2026"
os.environ.pop("DC_SIGNUP_CODE", None)  # 默认：注册不需要邀请码

import server  # noqa: E402

ADMIN_PASSWORD = "AdminPass!2026"


@atexit.register
def _cleanup() -> None:
    shutil.rmtree(_tmp, ignore_errors=True)


# ---------------------------------------------------------------- HTTP 测试夹具


class _Client:
    """极简 HTTP 客户端：记住 Cookie，方便测会话链路。"""

    def __init__(self, base: str) -> None:
        self.base = base
        self.cookie = ""

    def request(self, method: str, path: str, body: dict | None = None, basic: tuple[str, str] | None = None,
                cookie: str | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if data:
            request.add_header("Content-Type", "application/json")
        if basic:
            import base64

            token = base64.b64encode(f"{basic[0]}:{basic[1]}".encode("utf-8")).decode("ascii")
            request.add_header("Authorization", "Basic " + token)
        effective_cookie = self.cookie if cookie is None else cookie
        if effective_cookie:
            request.add_header("Cookie", effective_cookie)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8", "replace")
                return response.status, dict(response.headers), _maybe_json(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            return exc.code, dict(exc.headers), _maybe_json(raw)

    def login(self, username: str, password: str, remember: bool = False):
        status, headers, payload = self.request(
            "POST", "/api/auth/login", {"username": username, "password": password, "remember": remember}
        )
        set_cookie = headers.get("Set-Cookie", "")
        if set_cookie:
            self.cookie = set_cookie.split(";")[0]
        return status, payload


def _maybe_json(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw[:200]}


@pytest.fixture(scope="module")
def http_server():
    server.bootstrap_admin_from_env()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.ImportPrototypeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def client(http_server):
    return _Client(http_server)


@pytest.fixture()
def viewer_client(http_server):
    """注册一个只读账号并登录。"""
    name = f"viewer_{os.urandom(3).hex()}"
    c = _Client(http_server)
    status, headers, body = c.request(
        "POST", "/api/auth/register", {"username": name, "password": "ViewerPass!2026", "displayName": "只读用户"}
    )
    assert status == 200 and body.get("ok"), body
    set_cookie = headers.get("Set-Cookie", "")
    if set_cookie:
        c.cookie = set_cookie.split(";")[0]
    return c


# ---------------------------------------------------------------- 1. 密码哈希


def test_password_hash_roundtrip_and_uniqueness():
    first = server.hash_password("Passw0rd!x")
    second = server.hash_password("Passw0rd!x")
    assert first != second, "同一口令两次哈希必须不同（盐随机）"
    assert server.verify_password("Passw0rd!x", first)
    assert server.verify_password("Passw0rd!x", second)
    assert not server.verify_password("Passw0rd!y", first)
    assert first.startswith("scrypt$")


@pytest.mark.parametrize("bad", ["", "plain", "scrypt$1$2$3", "md5$aa$bb$cc$dd$ee"])
def test_verify_password_rejects_malformed(bad):
    assert server.verify_password("whatever", bad) is False


def test_hash_never_stores_plaintext():
    stored = server.hash_password("Secret!123")
    assert "Secret!123" not in stored


# ---------------------------------------------------------------- 2. 会话


def test_session_lifecycle():
    user_id = "u_" + os.urandom(4).hex()
    with server.connect_db() as conn:
        conn.execute(
            "insert into _users (id, username, display_name, password_hash, role, enabled, created_at)"
            " values (?, ?, ?, ?, 'viewer', 1, ?)",
            (user_id, "sess_" + user_id, "会话用户", server.hash_password("x"), server.now_text()),
        )
    token, seconds = server.create_session(user_id, "127.0.0.1", "pytest", False)
    resolved = server.resolve_session(token)
    assert resolved and resolved["id"] == user_id and resolved["role"] == "viewer"

    # 库里只能看到 sha256，明文 token 不得落库
    import hashlib

    with server.connect_db() as conn:
        rows = conn.execute("select token_hash from _sessions where user_id = ?", (user_id,)).fetchall()
    assert rows and all(row["token_hash"] != token for row in rows)
    assert rows[0]["token_hash"] == hashlib.sha256(token.encode()).hexdigest()

    server.destroy_session(token)
    assert server.resolve_session(token) is None


def test_expired_session_is_rejected_and_cleaned():
    user_id = "u_exp_" + os.urandom(4).hex()
    with server.connect_db() as conn:
        conn.execute(
            "insert into _users (id, username, display_name, password_hash, role, enabled, created_at)"
            " values (?, ?, ?, ?, 'viewer', 1, ?)",
            (user_id, "exp_" + user_id, "过期", server.hash_password("x"), server.now_text()),
        )
    token = server.secrets.token_urlsafe(16)
    import hashlib

    with server.connect_db() as conn:
        conn.execute(
            "insert into _sessions (id, token_hash, user_id, created_at, expires_at, last_seen_at, ip, user_agent)"
            " values (?, ?, ?, ?, ?, ?, '', '')",
            ("s_exp", hashlib.sha256(token.encode()).hexdigest(), user_id,
             server.now_text(), "2000-01-01 00:00:00", server.now_text()),
        )
    assert server.resolve_session(token) is None
    with server.connect_db() as conn:
        left = conn.execute("select count(*) from _sessions where id = 's_exp'").fetchone()[0]
    assert int(left) == 0, "过期会话应被顺带清理"


def test_disabled_user_session_invalidated():
    user_id = "u_dis_" + os.urandom(4).hex()
    with server.connect_db() as conn:
        conn.execute(
            "insert into _users (id, username, display_name, password_hash, role, enabled, created_at)"
            " values (?, ?, ?, ?, 'viewer', 0, ?)",
            (user_id, "dis_" + user_id, "停用", server.hash_password("x"), server.now_text()),
        )
    token, _ = server.create_session(user_id, "127.0.0.1", "pytest", False)
    assert server.resolve_session(token) is None


def test_destroy_user_sessions_kills_all():
    user_id = "u_kill_" + os.urandom(4).hex()
    with server.connect_db() as conn:
        conn.execute(
            "insert into _users (id, username, display_name, password_hash, role, enabled, created_at)"
            " values (?, ?, ?, ?, 'viewer', 1, ?)",
            (user_id, "kill_" + user_id, "多端", server.hash_password("x"), server.now_text()),
        )
    tokens = [server.create_session(user_id, "127.0.0.1", "pytest", False)[0] for _ in range(3)]
    assert server.destroy_user_sessions(user_id) == 3
    assert all(server.resolve_session(token) is None for token in tokens)


# ---------------------------------------------------------------- 3. 双通道认证（HTTP）


def test_ping_is_exempt(client):
    status, _, payload = client.request("GET", "/api/ping")
    assert status == 200 and payload.get("ok"), payload


def test_signup_info_is_exempt(client):
    status, _, payload = client.request("GET", "/api/auth/signup-info")
    assert status == 200 and payload.get("ok") is True
    assert payload.get("authEnabled") is True


def test_page_request_redirects_to_login(client):
    """未登录访问页面 → 落到登录页本体。

    _Client 会跟随重定向，所以不能断言「拿到 302」：登录页自己也 302 时
    （即重定向死循环）同样会停在 302，那种情况反而是坏的。
    这里断言最终拿到登录页本体，并由 test_p6_permissions 断言白名单常量。
    """
    status, _, body = client.request("GET", "/")
    assert status == 200, status
    # 客户端只保留响应体前 200 字符，足以看到登录页标题
    assert "登录 - 数据导表工具" in str(body)


def test_sub_page_redirect_keeps_next(client):
    """子页面未登录访问时，重定向目标要带上原地址（next），登录后才能跳回。

    urllib 默认跟随重定向，看不到 Location，所以这里自建一个「不跟随」的 opener。
    """

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(client.base + "/query.html", timeout=10)
        raise AssertionError("未登录访问 /query.html 竟然没有重定向")
    except urllib.error.HTTPError as exc:
        assert exc.code == 302, exc.code
        location = str(exc.headers.get("Location", ""))
        assert location.startswith("/login.html?next="), location
        assert "query.html" in location, location


def test_api_request_without_session_returns_401_json(client):
    status, _, payload = client.request("GET", "/api/connections")
    assert status == 401
    assert payload.get("needLogin") is True
    assert "未登录" in str(payload.get("error", ""))


def test_static_assets_are_not_blocked(client):
    for path in ("/styles.css", "/shell.js", "/favicon.svg"):
        status, _, _ = client.request("GET", path)
        assert status == 200, f"{path} 应放行（否则登录页自身样式加载不了）"


def test_basic_auth_channel_still_works(client):
    """脚本通道：acceptance/ 下既有复跑脚本依赖它，必须保持可用。"""
    status, _, payload = client.request(
        "GET", "/api/connections", basic=(server.os.environ["ADMIN_USER"], ADMIN_PASSWORD)
    )
    assert status == 200 and payload.get("ok") is True

    status, _, _ = client.request("GET", "/api/connections", basic=("admin", "wrong-password"))
    assert status == 401


def test_login_flow_with_cookie(client):
    status, payload = client.login(server.os.environ["ADMIN_USER"], ADMIN_PASSWORD)
    assert status == 200 and payload.get("user", {}).get("role") == "admin"

    status, _, me = client.request("GET", "/api/auth/me")
    assert status == 200 and me.get("user", {}).get("username") == server.os.environ["ADMIN_USER"]

    status, _, payload = client.request("GET", "/api/connections")
    assert status == 200 and payload.get("ok") is True

    # Cookie 必须是 HttpOnly（前端脚本读不到，降低 XSS 窃取风险）
    status, headers, _ = client.request("POST", "/api/auth/login",
                                        {"username": server.os.environ["ADMIN_USER"], "password": ADMIN_PASSWORD})
    assert "HttpOnly" in headers.get("Set-Cookie", "")


def test_login_with_wrong_password_then_logout(client):
    status, payload = client.login(server.os.environ["ADMIN_USER"], "definitely-wrong")
    assert status == 401
    assert "不正确" in str(payload.get("error"))

    client.login(server.os.environ["ADMIN_USER"], ADMIN_PASSWORD)
    status, _, _ = client.request("POST", "/api/auth/logout")
    assert status == 200
    status, _, _ = client.request("GET", "/api/connections")
    assert status == 401, "登出后原会话必须失效"


def test_login_requires_both_fields(client):
    status, _, payload = client.request("POST", "/api/auth/login", {"username": "admin", "password": ""})
    assert status == 400
    assert "用户名与密码" in str(payload.get("error"))


# ---------------------------------------------------------------- 4. 开放注册


def test_register_defaults_to_viewer_and_rejects_short_password(client):
    name = "reg_" + os.urandom(3).hex()
    status, _, payload = client.request("POST", "/api/auth/register", {"username": name, "password": "short"})
    assert status == 400 and "至少 8 位" in str(payload.get("error"))

    status, headers, payload = client.request(
        "POST", "/api/auth/register", {"username": name, "password": "GoodPass!2026"}
    )
    assert status == 200 and payload.get("user", {}).get("role") == "viewer"

    # 用户名唯一
    status, _, payload = client.request(
        "POST", "/api/auth/register", {"username": name, "password": "GoodPass!2026"}
    )
    assert status == 400 and "已被占用" in str(payload.get("error"))


def test_signup_code_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("DC_SIGNUP_CODE", "INVITE-2026")
    name = "code_" + os.urandom(3).hex()
    status, _, payload = client.request(
        "POST", "/api/auth/register", {"username": name, "password": "GoodPass!2026"}
    )
    assert status == 403 and "邀请码" in str(payload.get("error"))

    status, _, payload = client.request(
        "POST", "/api/auth/register", {"username": name, "password": "GoodPass!2026", "code": "WRONG"}
    )
    assert status == 403

    status, _, payload = client.request(
        "POST", "/api/auth/register", {"username": name, "password": "GoodPass!2026", "code": "INVITE-2026"}
    )
    assert status == 200 and payload.get("user", {}).get("role") == "viewer"


# ---------------------------------------------------------------- 5. 防爆破


def test_account_locks_after_repeated_failures(client):
    username = "lock_" + os.urandom(3).hex()
    c = _Client(client.base)
    c.request("POST", "/api/auth/register", {"username": username, "password": "GoodPass!2026"})
    for _ in range(server.AUTH_MAX_FAILED):
        c.request("POST", "/api/auth/login", {"username": username, "password": "bad-guess"})
    # 锁定后即使密码正确也拒绝
    status, payload = c.login(username, "GoodPass!2026")
    assert status == 401
    assert "锁定" in str(payload.get("error"))


# ---------------------------------------------------------------- 6. 审计


def test_audit_records_login_and_register():
    with server.connect_db() as conn:
        actions = {row["action"] for row in conn.execute("select action from _audit_logs").fetchall()}
    assert {"login", "register"} <= actions


def test_audit_records_denied_attempts(client):
    c = _Client(client.base)
    c.request("POST", "/api/auth/login", {"username": "no_such_user_xyz", "password": "whatever"})
    with server.connect_db() as conn:
        row = conn.execute(
            "select status, detail from _audit_logs where action = 'login' and username = 'no_such_user_xyz'"
            " order by created_at desc limit 1"
        ).fetchone()
    assert row is not None and row["status"] == "denied"


# ---------------------------------------------------------------- 7. 首个管理员引导


def test_bootstrap_creates_admin_from_env():
    row = server.find_user(server.os.environ["ADMIN_USER"])
    assert row is not None, "ADMIN_PASSWORD 已配置时启动应创建首个管理员"
    assert row["role"] == "admin"
    assert server.verify_password(ADMIN_PASSWORD, str(row["password_hash"]))
    # 幂等：再调一次不会重复创建
    before = server.count_users()
    server.bootstrap_admin_from_env()
    assert server.count_users() == before


# ---------------------------------------------------------------- 8. 隔离自检


def test_isolated_from_production_data():
    assert Path(tempfile.gettempdir()).resolve() in Path(server.DB_PATH).resolve().parents, server.DB_PATH
