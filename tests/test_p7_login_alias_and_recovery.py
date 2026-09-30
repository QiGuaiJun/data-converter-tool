"""登录别名（邮箱 / 手机号）与「忘记密码」邮箱验证码自助找回。

隔离：与 tests/ 下其它用例一致 —— 导入 server 之前把 DATA_DIR / UPLOADS_DIR /
EXPORTS_DIR 指向本进程的临时目录；不要在这里覆写 APP_AUTH_ENABLED / ADMIN_USER /
ADMIN_PASSWORD（进程级环境变量会冲掉别的用例文件的期望值）。
本文件只覆写 DC_SMTP_*（仅本文件用到，安全）。

发信一律用桩替换 `server.send_mail`，**不打真实 SMTP**。
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="dc_alias_")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")

import server  # noqa: E402

SMTP_ENV = {
    "DC_SMTP_HOST": "smtp.example.com",
    "DC_SMTP_PORT": "465",
    "DC_SMTP_USER": "noreply@example.com",
    "DC_SMTP_PASSWORD": "auth-code",
    "DC_SMTP_FROM": "noreply@example.com",
    "DC_SMTP_SSL": "true",
}
CODE_RE = re.compile(r"验证码：(\d{6})")


class _Handler:
    """够 handle_auth_forgot_* 用的最小替身：提供 body / 响应 / IP。"""

    def __init__(self, body: dict | None = None, ip: str = "10.1.2.3") -> None:
        raw = json.dumps(body or {}).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.response_headers: list[tuple[str, str]] = []
        self._ip = ip

    def send_response(self, status: int) -> None:
        self.status = int(status)

    def send_header(self, key: str, value: str) -> None:
        self.response_headers.append((key, str(value)))

    def end_headers(self) -> None:
        pass

    def request_ip(self) -> str:
        return self._ip

    @property
    def payload(self) -> dict:
        return json.loads(self.wfile.getvalue().decode("utf-8"))


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
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


@pytest.fixture()
def smtp_on(monkeypatch):
    for key, value in SMTP_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture()
def outbox(monkeypatch):
    """把发信换成记入列表的桩，返回 outbox。"""
    sent: list[dict[str, str]] = []

    def fake_send(to_addr: str, subject: str, body: str) -> None:
        sent.append({"to": to_addr, "subject": subject, "body": body})

    monkeypatch.setattr(server, "send_mail", fake_send)
    return sent


def _post_send(handler_cls, body: dict, ip: str = "10.1.2.3") -> _Handler:
    handler = _Handler(body, ip=ip)
    handler_cls.handle_auth_forgot_send(handler)
    return handler


def _post_reset(handler_cls, body: dict, ip: str = "10.1.2.3") -> _Handler:
    handler = _Handler(body, ip=ip)
    handler_cls.handle_auth_forgot_reset(handler)
    return handler


def _make_user(email: str = "zs@corp.com", phone: str = "13800138000", username: str = "张三") -> dict:
    return server.create_user({
        "username": username, "role": "operator",
        "password": "Init-Pass-2026", "email": email, "phone": phone,
    })


def _code_from(outbox: list[dict[str, str]]) -> str:
    assert outbox, "没有发出任何邮件"
    match = CODE_RE.search(outbox[-1]["body"])
    assert match, f"邮件里没有验证码：{outbox[-1]['body']!r}"
    return match.group(1)


# --------------------------------------------------------------- 接线与开关
def test_forgot_paths_are_anonymous_and_csrf_exempt():
    """两个找回接口必须在"免登录"与"免 CSRF"名单里 —— 否则未登录根本调不到。"""
    for path in ("/api/auth/forgot/send", "/api/auth/forgot/reset"):
        assert path in server.AUTH_EXEMPT_PATHS, f"{path} 不在 AUTH_EXEMPT_PATHS"
        assert path in server.CSRF_EXEMPT_PATHS, f"{path} 不在 CSRF_EXEMPT_PATHS"


def test_recovery_disabled_without_smtp(fresh_db, monkeypatch):
    """没配 SMTP 时必须"整体不可用"而不是半残：接口拒绝，前端也拿不到开关。"""
    for key in SMTP_ENV:
        monkeypatch.delenv(key, raising=False)
    assert server.email_recovery_enabled() is False
    handler_cls = server.ImportPrototypeHandler
    sent = _post_send(handler_cls, {"identifier": "张三"})
    assert sent.status == 503 and sent.payload["ok"] is False
    reset = _post_reset(handler_cls, {"identifier": "张三", "code": "123456", "password": "New-Pass-2026"})
    assert reset.status == 503 and reset.payload["ok"] is False


def test_smtp_requires_password_when_user_set(monkeypatch):
    """配了用户名却没配授权码 = 没配好，不能算启用。"""
    monkeypatch.setenv("DC_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("DC_SMTP_USER", "noreply@example.com")
    monkeypatch.delenv("DC_SMTP_PASSWORD", raising=False)
    assert server.email_recovery_enabled() is False


# --------------------------------------------------------------- 登录别名
def test_identifier_resolves_username_email_phone(fresh_db):
    _make_user(email="ZhangSan@Corp.COM", phone="138 0013 8000")
    for identifier in ("张三", "zhangsan@corp.com", "ZhangSan@corp.com", "13800138000", "+8613800138000", "138 0013 8000"):
        row = server.find_user_by_identifier(identifier)
        assert row is not None, f"{identifier} 解析失败"
        assert row["username"] == "张三"
    assert server.find_user_by_identifier("nobody@x.com") is None
    assert server.find_user_by_identifier("") is None


def test_alias_is_normalized_on_save(fresh_db):
    user = _make_user(email="ZhangSan@Corp.COM", phone="+86 138-0013-8000")
    assert user["email"] == "zhangsan@corp.com"
    assert user["phone"] == "13800138000"


def test_alias_conflicts_and_format_are_rejected(fresh_db):
    _make_user()
    with pytest.raises(ValueError, match="邮箱已被其他账号使用"):
        server.create_user({"username": "李四", "password": "Init-Pass-2026", "email": "ZS@corp.com"})
    with pytest.raises(ValueError, match="手机号已被其他账号使用"):
        server.create_user({"username": "王五", "password": "Init-Pass-2026", "phone": "13800138000"})
    with pytest.raises(ValueError, match="邮箱格式不正确"):
        server.create_user({"username": "赵六", "password": "Init-Pass-2026", "email": "not-an-email"})
    with pytest.raises(ValueError, match="手机号格式不正确"):
        server.create_user({"username": "孙七", "password": "Init-Pass-2026", "phone": "12345"})


def test_update_user_can_set_and_clear_alias(fresh_db):
    user = _make_user()
    updated, changes = server.update_user({"id": user["id"], "email": "new@corp.com"}, None)
    assert updated["email"] == "new@corp.com" and any("邮箱" in c for c in changes)
    # 只改邮箱不应动密码
    row = server.find_user_by_id(user["id"])
    assert server.verify_password("Init-Pass-2026", row["password_hash"])
    # 传空字符串 = 清空绑定
    cleared, _ = server.update_user({"id": user["id"], "phone": ""}, None)
    assert cleared["phone"] == ""
    # 不传该键 = 不动
    kept, _ = server.update_user({"id": user["id"], "role": "viewer"}, None)
    assert kept["email"] == "new@corp.com"


def test_mask_email():
    assert server.mask_email("zhangsan@corp.com") == "z******n@corp.com"
    assert server.mask_email("ab@x.com") == "a@x.com"
    assert server.mask_email("a@x.com") == "a@x.com"


# --------------------------------------------------------------- 发送验证码
def test_send_code_success_masks_target_and_stores_only_hash(fresh_db, smtp_on, outbox):
    _make_user(email="zhangsan@corp.com")
    handler_cls = server.ImportPrototypeHandler
    resp = _post_send(handler_cls, {"identifier": "张三"})
    assert resp.status == 200 and resp.payload["sent"] is True
    assert resp.payload["target"] == "z******n@corp.com"      # 回显脱敏
    assert "zhangsan@corp.com" not in json.dumps(resp.payload)  # 不泄露完整邮箱
    code = _code_from(outbox)
    with server.connect_db() as conn:
        row = conn.execute("select * from _auth_reset_codes").fetchone()
    assert row["code_hash"] != code and code not in row["code_hash"]  # 不落明文
    assert server._hash_reset_code(code, row["salt"], row["user_id"]) == row["code_hash"]
    assert row["attempts"] == 0 and row["used_at"] is None


def test_send_code_for_unknown_account_is_generic_success(fresh_db, smtp_on, outbox):
    """账号不存在也要返回成功文案，否则就成了账号探测器。"""
    handler_cls = server.ImportPrototypeHandler
    resp = _post_send(handler_cls, {"identifier": "查无此人"})
    assert resp.status == 200 and resp.payload["sent"] is True
    assert "target" not in resp.payload
    assert outbox == []  # 当然不会真发信


def test_send_code_without_email_bound_is_rejected(fresh_db, smtp_on, outbox):
    server.create_user({"username": "李四", "password": "Init-Pass-2026"})
    resp = _post_send(server.ImportPrototypeHandler, {"identifier": "李四"})
    assert resp.status == 400 and "还没有绑定邮箱" in resp.payload["error"]
    assert outbox == []


def test_send_code_cooldown_blocks_immediate_resend(fresh_db, smtp_on, outbox):
    _make_user()
    handler_cls = server.ImportPrototypeHandler
    assert _post_send(handler_cls, {"identifier": "张三"}).status == 200
    second = _post_send(handler_cls, {"identifier": "张三"})
    assert second.status == 400 and "秒后再试" in second.payload["error"]
    assert len(outbox) == 1


def test_send_code_mail_failure_discards_code(fresh_db, smtp_on, monkeypatch):
    """发信失败要删掉刚生成的码：否则用户手上没有码，库里却躺着一个有效的。"""
    _make_user()

    def boom(*_args, **_kwargs):
        raise RuntimeError("邮件发送失败：连接超时")

    monkeypatch.setattr(server, "send_mail", boom)
    resp = _post_send(server.ImportPrototypeHandler, {"identifier": "张三"})
    assert resp.status == 502 and "邮件发送失败" in resp.payload["error"]
    with server.connect_db() as conn:
        assert conn.execute("select count(*) from _auth_reset_codes").fetchone()[0] == 0


# --------------------------------------------------------------- 重置密码
def test_reset_success_changes_password_and_kills_sessions(fresh_db, smtp_on, outbox):
    user = _make_user(email="zhangsan@corp.com")
    token, _ = server.create_session(user["id"], "10.0.0.1", "test-agent", False)
    assert server.resolve_session(token) is not None, "前置：会话应当有效"
    handler_cls = server.ImportPrototypeHandler
    # 这里刻意用**邮箱**发起找回：同时验证"邮箱可作为登录别名
    # 参与找回流程"这条链路（账号名走的是另一条分支）。
    _post_send(handler_cls, {"identifier": "zhangsan@corp.com"})
    code = _code_from(outbox)

    resp = _post_reset(handler_cls, {"identifier": "张三", "code": code, "password": "Brand-New-2026"})
    assert resp.status == 200 and resp.payload["ok"] is True
    row = server.find_user_by_id(user["id"])
    assert server.verify_password("Brand-New-2026", row["password_hash"])
    assert not server.verify_password("Init-Pass-2026", row["password_hash"])
    assert row["locked_until"] in (None, "") and int(row["failed_count"]) == 0
    # 改密后立刻作废全部旧会话（复用 update_user 的既有行为）
    assert server.resolve_session(token) is None, "重置后旧会话应全部失效"


def test_reset_wrong_code_increments_attempts_and_persists(fresh_db, smtp_on, outbox):
    """尝试次数必须在 raise 之后仍然落库 —— 若在 with 块内 raise 会被回滚，等于可以无限试码。"""
    _make_user()
    handler_cls = server.ImportPrototypeHandler
    _post_send(handler_cls, {"identifier": "张三"})
    code = _code_from(outbox)
    wrong = "000000" if code != "000000" else "111111"

    for expected_left in (4, 3):
        resp = _post_reset(handler_cls, {"identifier": "张三", "code": wrong, "password": "Brand-New-2026"})
        assert resp.status == 400 and f"还可尝试 {expected_left} 次" in resp.payload["error"]
    with server.connect_db() as conn:
        assert conn.execute("select attempts from _auth_reset_codes").fetchone()[0] == 2, "尝试次数没有落库"


def test_reset_locks_out_after_max_attempts(fresh_db, smtp_on, outbox):
    _make_user()
    handler_cls = server.ImportPrototypeHandler
    _post_send(handler_cls, {"identifier": "张三"})
    code = _code_from(outbox)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(server.AUTH_RESET_MAX_ATTEMPTS):
        _post_reset(handler_cls, {"identifier": "张三", "code": wrong, "password": "Brand-New-2026"})
    # 次数用尽后，即使给出正确验证码也不认
    resp = _post_reset(handler_cls, {"identifier": "张三", "code": code, "password": "Brand-New-2026"})
    assert resp.status == 400 and "尝试次数过多" in resp.payload["error"]


def test_reset_code_is_single_use(fresh_db, smtp_on, outbox):
    user = _make_user()
    handler_cls = server.ImportPrototypeHandler
    _post_send(handler_cls, {"identifier": "张三"})
    code = _code_from(outbox)
    assert _post_reset(handler_cls, {"identifier": "张三", "code": code, "password": "Brand-New-2026"}).status == 200
    again = _post_reset(handler_cls, {"identifier": "张三", "code": code, "password": "Another-Pass-2026"})
    assert again.status == 400 and "已使用过" in again.payload["error"]
    assert server.verify_password("Brand-New-2026", server.find_user_by_id(user["id"])["password_hash"])


def test_reset_expired_code_is_rejected(fresh_db, smtp_on, outbox):
    _make_user()
    handler_cls = server.ImportPrototypeHandler
    _post_send(handler_cls, {"identifier": "张三"})
    code = _code_from(outbox)
    with server.connect_db() as conn:
        conn.execute("update _auth_reset_codes set expires_at = '2000-01-01 00:00:00'")
    resp = _post_reset(handler_cls, {"identifier": "张三", "code": code, "password": "Brand-New-2026"})
    assert resp.status == 400 and "已过期" in resp.payload["error"]


def test_reset_short_password_does_not_consume_code(fresh_db, smtp_on, outbox):
    """密码格式不合格就不该浪费掉一次验证码。

    校验抛的是 ValueError，由 do_POST 的统一异常分支转成 400 JSON
    （与 handle_auth_login 等既有接口一致），所以这里断言"抛异常"而不是"返回 400"。
    """
    _make_user()
    handler_cls = server.ImportPrototypeHandler
    _post_send(handler_cls, {"identifier": "张三"})
    code = _code_from(outbox)
    with pytest.raises(ValueError, match="8 位"):
        _post_reset(handler_cls, {"identifier": "张三", "code": code, "password": "short"})
    with server.connect_db() as conn:
        assert conn.execute("select used_at from _auth_reset_codes").fetchone()[0] is None
    # 同一个码仍可正常使用
    assert _post_reset(handler_cls, {"identifier": "张三", "code": code, "password": "Brand-New-2026"}).status == 200


def test_reset_unknown_account_is_generic(fresh_db, smtp_on, outbox):
    resp = _post_reset(server.ImportPrototypeHandler, {"identifier": "查无此人", "code": "123456", "password": "Brand-New-2026"})
    assert resp.status == 400 and resp.payload["error"] == "验证码不正确或已过期。"


def test_ip_rate_limit_applies(fresh_db, smtp_on, outbox):
    """与登录共用按 IP 的限流桶：狂刷发送接口会被挡住。"""
    _make_user()
    handler_cls = server.ImportPrototypeHandler
    last = None
    for _ in range(server.LOGIN_RATE_MAX + 2):
        last = _post_send(handler_cls, {"identifier": "张三"})
    assert last.status == 429 and "过于频繁" in last.payload["error"]


# --------------------------------------------------------------- 邮件发送本身
class _FakeSMTP:
    """替身 SMTP 客户端：只记录调用，不真的连网。

    不测 smtplib 的协议实现（那是标准库的职责），只测我们这层：
    收件人/发件人/主题/正文怎么拼、用没用 SSL、以及失败怎么转成 RuntimeError。
    """

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args: tuple | None = None
        self.message = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, message):
        self.message = message


@pytest.fixture()
def fake_smtp(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(server.smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(server.smtplib, "SMTP", _FakeSMTP)
    return _FakeSMTP


def test_send_mail_composes_message_and_uses_ssl(fresh_db, smtp_on, fake_smtp):
    server.send_reset_code_mail("zhangsan@corp.com", "123456")
    client = fake_smtp.instances[-1]
    assert client.host == "smtp.example.com" and client.port == 465
    assert client.timeout == server.SMTP_TIMEOUT_SECONDS
    assert client.login_args == ("noreply@example.com", "auth-code")
    assert client.message["To"] == "zhangsan@corp.com"
    assert client.message["From"] == "noreply@example.com"
    body = client.message.get_content()
    assert "123456" in body and f"{server.AUTH_RESET_CODE_TTL_MINUTES} 分钟" in body
    # 正文里不放任何链接：既符合"验证码就是验证码"，也降低被当垃圾邮件的概率
    assert "http://" not in body and "https://" not in body
    assert not client.started_tls, "465 端口应走 SMTP_SSL，不该再 STARTTLS"


def test_send_mail_plain_uses_starttls(fresh_db, smtp_on, fake_smtp, monkeypatch):
    monkeypatch.setenv("DC_SMTP_SSL", "false")
    monkeypatch.setenv("DC_SMTP_PORT", "587")
    server.send_reset_code_mail("zhangsan@corp.com", "654321")
    client = fake_smtp.instances[-1]
    assert client.port == 587 and client.started_tls is True


def test_send_mail_failure_becomes_runtime_error(fresh_db, smtp_on, monkeypatch):
    class Boom:
        def __init__(self, *_args, **_kwargs):
            raise OSError("connection refused")

    monkeypatch.setattr(server.smtplib, "SMTP_SSL", Boom)
    with pytest.raises(RuntimeError, match="邮件发送失败"):
        server.send_mail("zhangsan@corp.com", "主题", "正文")
