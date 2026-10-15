"""同步模块 · 执行入口（execute_sync_task）端到端守门。

⚠️ 本文件必须在 ``import server`` **之前**设置临时数据目录：``server.DB_PATH`` 是模块导入时
按 ``DATA_DIR`` 算出来并定死的，谁先 import server 谁决定它。这里照抄 ``tests/test_p8_sync.py``
的写法，避免把 DB_PATH 钉到生产路径。

为什么要有这个文件（对照 ``tests/test_p8_sync.py``）：
    ``test_p8_sync.py`` 全是**纯单元**（数据模型、校验、DDL 文本、类型推断），文件顶部明说
    "真实 MySQL 链路验证不放这里，那是 acceptance/verify_sync_real_pair.py"。结果
    ``server.py`` 里 ``execute_sync_task(config)`` 这条**真删表 / 真写库**的执行路径长期
    没有自动化守门 —— 一旦改动 sync 数据模型或 mode 语义，只能靠人工跑需要真库的
    acceptance 脚本才能发现回归。

这里用 **SQLite-shim**（把 ``server.connect_sync_db`` 换成 SQLite 实现，并把 MySQL 方言的
SQL 在游标层翻译成 SQLite 能吃的形态）把执行路径包成 pytest。单测在**没有 MySQL 的机器上
也能跑通**；数据全部落在 pytest 的 ``tmp_path`` 里，绝不触碰真实库。
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="p9_sync_")
os.environ.setdefault("DATA_DIR", os.path.join(_SANDBOX, "data"))
os.environ.setdefault("UPLOADS_DIR", os.path.join(_SANDBOX, "up"))
os.environ.setdefault("EXPORTS_DIR", os.path.join(_SANDBOX, "exp"))

import pytest  # noqa: E402

import pymysql  # noqa: E402

import server  # noqa: E402

# ---------------------------------------------------------------- 常量
_STRING_TYPE_CODE = pymysql.constants.FIELD_TYPE.VAR_STRING

# 源端夹具：一张"没有主键、含 varchar/datetime/decimal"的小票表，覆盖业主最关心的
# 「字符串列不能建成 longtext」这条线。
_SRC_DDL = (
    'create table "会员小票表" ('
    ' "单据编号" varchar(20),'
    ' "玩家ID" varchar(11),'
    ' "金额" decimal(10,2),'
    ' "提单时间" datetime,'
    ' "年月" varchar(7))'
)
_SRC_ROWS = [
    ("A001", "13800000001", 10.5, "2026-10-01 09:00:00", "2026-10"),
    ("A002", "13800000002", 20.0, "2026-10-02 10:00:00", "2026-10"),
    ("A003", "13800000003", 30.0, "2026-11-03 11:00:00", "2026-11"),
]


# ---------------------------------------------------------------- SQLite-shim
class _ShimCursor:
    """把 MySQL 方言 SQL 翻译成 SQLite 能执行的形态，其余原样透传给 sqlite3 游标。

    需要翻译/拦截的只有几处生产代码里的 MySQL 专有写法：
      · ``information_schema.tables/columns`` → 改成扫 ``<schema>.sqlite_master`` / ``PRAGMA``；
      · ``show create table `db`.`t``` → 按 ``PRAGMA table_info`` 合成一份 MySQL 风格 DDL；
      · ``%s`` 占位符 → ``?``；``<=>`` 空安全等于 → SQLite 的 ``IS``；
      · 建表尾部的 ``engine=InnoDB default charset=utf8mb4`` → 去掉（SQLite 没有引擎概念）。
    这些替换都是等价的，不改变被测逻辑的语义。
    """

    def __init__(self, conn: sqlite3.Connection, probe_types: dict) -> None:
        self._conn = conn
        self._probe_types = probe_types
        self._cur: sqlite3.Cursor | None = None
        self._rows: list[tuple] = []
        self._pos = 0
        self.description = None

    # ---- 上下文管理：与 pymysql 游标一致，退出时关闭 ----
    def __enter__(self) -> "_ShimCursor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._cur is not None:
            self._cur.close()
            self._cur = None

    # ---- 执行 ----
    def execute(self, sql: str, params=None) -> "_ShimCursor":
        low = sql.lower()
        if "information_schema.tables" in low:
            schema, table = params[0], params[1]
            count = self._conn.execute(
                f'select count(*) from "{schema}".sqlite_master'
                " where type='table' and name=?",
                (table,),
            ).fetchone()[0]
            self._rows = [(count,)]
            self.description = [("count(*)", None, None, None, None, None, None)]
        elif "information_schema.columns" in low:
            schema, table = params[0], params[1]
            info = self._conn.execute(
                f'PRAGMA "{schema}".table_info("{table}")'
            ).fetchall()
            # 对齐 sync_source_columns 期望的列序：
            # (column_name, column_type, data_type, is_nullable, column_key, column_comment)
            self._rows = [
                (row[1], row[2] or "", row[2] or "",
                 "NO" if row[3] else "YES", "PRI" if row[5] else "", "")
                for row in info
            ]
            self.description = [("c", None, None, None, None, None, None)] * 6
        elif low.strip().startswith("show create table"):
            ddl = self._synthesize_show_create(sql)
            self._rows = [("会员小票表", ddl)]
            self.description = [("Table", None, None, None, None, None, None),
                                ("Create Table", None, None, None, None, None, None)]
        else:
            self._delegate(sql, params)
        self._pos = 0
        return self

    def executemany(self, sql: str, seq) -> "_ShimCursor":
        self._cur = self._conn.cursor()
        self._cur.executemany(self._translate(sql), [tuple(row) for row in seq])
        self._rows = []
        self.description = None
        self._pos = 0
        return self

    # ---- 取值 ----
    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list[tuple]:
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def fetchmany(self, size: int) -> list[tuple]:
        rows = self._rows[self._pos:self._pos + size]
        self._pos += len(rows)
        return rows

    # ---- 内部 ----
    @staticmethod
    def _translate(sql: str) -> str:
        sql = sql.replace("%s", "?").replace("<=>", " IS ")
        # 去掉 MySQL 建表尾部的引擎/字符集声明；SQLite 不接受这段。
        sql = re.sub(r"\s*engine\s*=\s*innodb.*$", "", sql, flags=re.I | re.S)
        return sql.strip().rstrip(";").strip()

    def _delegate(self, sql: str, params) -> None:
        self._cur = self._conn.cursor()
        text = self._translate(sql)
        if params:
            self._cur.execute(text, tuple(params))
        else:
            self._cur.execute(text)
        self._rows = self._cur.fetchall()
        desc = list(self._cur.description or [])
        # 自定义 SQL 源会先跑 `select * from (...) sync_probe limit 0` 取结果集元数据，
        # 再据此推断建表类型（_sync_column_sql_type）。SQLite 的 description 不带类型码，
        # 这里按夹具登记的字段类型补上，好让「字符串列推 varchar(n)」这条路径被真正走到。
        if "sync_probe" in text:
            filled = []
            for item in desc:
                code, internal = self._probe_types.get(item[0], (_STRING_TYPE_CODE, 0))
                filled.append((item[0], code, None, internal, None, None, None))
            desc = filled
        self.description = desc or None

    def _synthesize_show_create(self, sql: str) -> str:
        tail = sql.strip()[len("show create table"):].strip().rstrip(";").strip()
        parts = [p.strip().strip("`").strip('"') for p in tail.split(".")]
        if len(parts) >= 2:
            schema, table = parts[0], ".".join(parts[1:])
        else:
            schema, table = "main", parts[0]
        info = self._conn.execute(f'PRAGMA "{schema}".table_info("{table}")').fetchall()
        column_lines: list[str] = []
        primary: list[tuple[int, str]] = []
        for row in info:
            name, ctype, notnull, _default, pk = row[1], row[2] or "longtext", row[3], row[4], row[5]
            line = f"  `{name}` {ctype}" + (" NOT NULL" if notnull else " DEFAULT NULL")
            column_lines.append(line)
            if pk:
                primary.append((pk, name))
        body = ",\n".join(column_lines)
        if primary:
            keys = ", ".join(f"`{name}`" for _, name in sorted(primary))
            body += f",\n  PRIMARY KEY ({keys})"
        return (
            f"CREATE TABLE `{table}` (\n{body}\n)"
            " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
        )


class _ShimConnection:
    """``server.connect_sync_db`` 返回物的 SQLite 替身：只实现 sync 路径用到的方法。"""

    def __init__(self, path: str | None = None, attach: list[tuple[str, str]] | None = None,
                 probe_types: dict | None = None) -> None:
        self._conn = sqlite3.connect(path or ":memory:")
        # autocommit：sync 路径里 commit()/rollback() 是显式调用，这里不额外包事务，
        # 让每批写入即时落盘，断言时读文件即可。
        self._conn.isolation_level = None
        for name, apath in attach or []:
            self._conn.execute(f"ATTACH DATABASE ? AS {name}", (apath,))
        self._probe_types = probe_types or {}

    def cursor(self, *args, **kwargs) -> _ShimCursor:  # 忽略 SSCursor 之类的游标类型参数
        return _ShimCursor(self._conn, self._probe_types)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------- 夹具封装
class _SyncFixture:
    """一套隔离的源库/目标库 + 把 server 的连接函数替换成 SQLite 实现。"""

    def __init__(self, tmp_path) -> None:
        self.tmp = tmp_path
        self.source_id = "p9-src"
        self.target_id = "p9-dst"
        self.src_db = "srcdb"
        self.src_file = str(tmp_path / "source.sqlite")
        self.dst_file = str(tmp_path / "target.sqlite")
        self.probe_types: dict = {}

        conn = sqlite3.connect(self.src_file)
        conn.isolation_level = None
        conn.execute(_SRC_DDL)
        conn.executemany(
            'insert into "会员小票表" values (?,?,?,?,?)', _SRC_ROWS
        )
        conn.close()
        sqlite3.connect(self.dst_file).close()  # 建一个空目标库文件

    # ---- 夹具操作 ----
    def src_exec(self, sql: str, params=()) -> None:
        conn = sqlite3.connect(self.src_file)
        conn.isolation_level = None
        conn.execute(sql, params)
        conn.close()

    def target_exists(self, table: str = "会员小票表") -> bool:
        conn = sqlite3.connect(self.dst_file)
        try:
            return bool(conn.execute(
                "select 1 from sqlite_master where type='table' and name=?", (table,)
            ).fetchone())
        finally:
            conn.close()

    def target_rows(self, table: str = "会员小票表") -> list[tuple]:
        conn = sqlite3.connect(self.dst_file)
        try:
            return conn.execute(f'select * from "{table}"').fetchall()
        finally:
            conn.close()

    def target_columns(self, table: str = "会员小票表") -> dict[str, str]:
        conn = sqlite3.connect(self.dst_file)
        try:
            return {row[1]: (row[2] or "") for row in conn.execute(
                f'PRAGMA table_info("{table}")'
            ).fetchall()}
        finally:
            conn.close()

    # ---- 装配 ----
    def install(self, monkeypatch) -> None:
        def fake_connect_sync_db(connection_id: str):
            if connection_id == self.source_id:
                return _ShimConnection(
                    attach=[(self.src_db, self.src_file)], probe_types=self.probe_types
                )
            if connection_id == self.target_id:
                return _ShimConnection(self.dst_file, probe_types=self.probe_types)
            raise AssertionError(f"夹具未登记连接 {connection_id}")

        def fake_load_saved_connection(connection_id: str) -> dict:
            if connection_id == self.source_id:
                db_name = self.src_db
            elif connection_id == self.target_id:
                db_name = "main"
            else:
                raise ValueError("选择的数据库连接不存在，请重新选择。")
            return {
                "targetDbType": "sqlite", "dbHost": "", "dbPort": "", "dbUser": "",
                "dbPassword": "", "dbName": db_name, "dbCharset": "utf8mb4",
                "sslEnabled": "false", "sslCa": "", "sslCert": "", "sslKey": "",
            }

        monkeypatch.setattr(server, "connect_sync_db", fake_connect_sync_db)
        monkeypatch.setattr(server, "load_saved_connection", fake_load_saved_connection)

    def adhoc(self, **overrides) -> dict:
        config = {
            "sourceConnectionId": self.source_id, "sourceMode": "table",
            "sourceTable": "会员小票表", "targetConnectionId": self.target_id,
            "targetTable": "会员小票表", "syncMode": "full",
        }
        config.update(overrides)
        return config


@pytest.fixture()
def sync_db(tmp_path, monkeypatch) -> _SyncFixture:
    fx = _SyncFixture(tmp_path)
    fx.install(monkeypatch)
    return fx


# ---------------------------------------------------------------- 1) 自动建表 + 类型保持
def test_full_mode_auto_creates_target_and_preserves_column_types(sync_db):
    """目标表不存在时按**源结构**建表，且字符串列保持 varchar 而不是被推成 longtext。

    这是 2026-10-14 修过的真实缺陷（自动建表把字符串列建成大文本，既做不了索引，
    又不符合语义），这里在执行入口上守一道，防止改 sync 模型时悄悄退化。

    ⚠️ 覆盖面要说清楚（2026-10-15 复核时补，避免后人误判）：
    本用例走 ``sourceMode="table"`` → ``ensure_sync_target_table``，列类型来自源端
    ``SHOW CREATE TABLE`` 的**原文复刻**，**根本不经过** ``_sync_column_sql_type``。
    真正守"类型推断"那支函数的是下面的
    ``test_sql_source_auto_create_infers_varchar_not_longtext``（走 ``sourceMode="sql"``）。
    两条守的是两条不同的建表路径 —— 改坏一条，另一条不会响，别以为覆盖到了。
    """
    assert not sync_db.target_exists()

    result = server.execute_sync_task(sync_db.adhoc(syncMode="full"))

    assert result["targetCreated"] is True
    assert result["rowsRead"] == 3
    assert result["rowsWritten"] == 3
    assert result["targetRows"] == 3

    columns = sync_db.target_columns()
    # 源结构原样复刻：varchar(11) / varchar(7) 不能被换成 longtext。
    assert columns["玩家ID"] == "varchar(11)"
    assert columns["年月"] == "varchar(7)"
    assert columns["金额"] == "decimal(10,2)"
    assert "longtext" not in " ".join(columns.values())
    # 内容逐行写入。
    assert len(sync_db.target_rows()) == 3


def test_full_mode_rerun_is_idempotent(sync_db):
    """full 是「清空 + 重写」，重复执行结果行数不能翻倍。"""
    first = server.execute_sync_task(sync_db.adhoc(syncMode="full"))
    second = server.execute_sync_task(sync_db.adhoc(syncMode="full"))

    assert first["rowsWritten"] == 3
    assert second["rowsWritten"] == 3
    assert second["targetCreated"] is False  # 第二次目标表已存在，不再重建
    assert len(sync_db.target_rows()) == 3  # 关键：不是 6


# ---------------------------------------------------------------- 2) 四档模式语义
def test_upsert_mode_replaces_rows_by_key(sync_db):
    """upsert：按业务键「删一段再整批插」，已有的更新、没有的新增。"""
    server.execute_sync_task(sync_db.adhoc(syncMode="full"))  # 先建出 3 行
    # 改一行 + 加一行
    sync_db.src_exec('update "会员小票表" set "金额"=? where "单据编号"=?', (99.9, "A001"))
    sync_db.src_exec(
        'insert into "会员小票表" values (?,?,?,?,?)',
        ("A004", "13800000004", 40.0, "2026-11-04 12:00:00", "2026-11"),
    )

    result = server.execute_sync_task(
        sync_db.adhoc(syncMode="upsert", keyColumns=["单据编号"])
    )

    assert result["rowsRead"] == 4
    assert result["rowsUpdated"] == 3   # A001/A002/A003 按键替换
    assert result["rowsWritten"] == 1   # A004 新增
    assert len(sync_db.target_rows()) == 4
    # 更新确实生效（A001 的金额变成 99.9）
    changed = [row for row in sync_db.target_rows() if row[0] == "A001"]
    assert changed and changed[0][2] == 99.9


def test_upsert_mode_rerun_is_idempotent(sync_db):
    """upsert 重复执行同一批数据，行数不翻倍、全部计为更新。"""
    server.execute_sync_task(sync_db.adhoc(syncMode="full"))
    server.execute_sync_task(sync_db.adhoc(syncMode="upsert", keyColumns=["单据编号"]))
    second = server.execute_sync_task(
        sync_db.adhoc(syncMode="upsert", keyColumns=["单据编号"])
    )
    assert second["rowsWritten"] == 0    # 没有新键
    assert second["rowsUpdated"] == 3
    assert len(sync_db.target_rows()) == 3  # 关键：不是 6


def test_append_mode_adds_rows_without_dedup(sync_db):
    """append：不做任何去重，跑两次就是两份（这是 append 的定义，不是缺陷）。"""
    server.execute_sync_task(sync_db.adhoc(syncMode="append"))
    second = server.execute_sync_task(sync_db.adhoc(syncMode="append"))
    assert second["rowsWritten"] == 3
    assert len(sync_db.target_rows()) == 6


def test_incremental_mode_reads_only_rows_after_watermark(sync_db):
    """incremental：首轮按全量建水位基线，之后只读「水位列 > 上次水位」的行。

    需要**持久化任务**（水位按 task_id 落库），所以这里真的 save 一条任务再执行。
    """
    task = server.save_sync_task({
        "name": "单测-增量水位", "sourceConnectionId": sync_db.source_id,
        "sourceMode": "table", "sourceTable": "会员小票表",
        "targetConnectionId": sync_db.target_id, "targetTable": "t_inc",
        "syncMode": "incremental", "watermarkColumn": "提单时间",
    })
    try:
        baseline = server.execute_sync_task({"id": task["id"]})
        assert baseline["rowsRead"] == 3            # 无水位 → 全量建基线
        assert baseline["watermarkTo"] == "2026-11-03 11:00:00"

        # 追加两条更晚的行
        sync_db.src_exec(
            'insert into "会员小票表" values (?,?,?,?,?)',
            ("A010", "13800000010", 50.0, "2026-12-01 08:00:00", "2026-12"),
        )
        sync_db.src_exec(
            'insert into "会员小票表" values (?,?,?,?,?)',
            ("A011", "13800000011", 60.0, "2026-12-02 09:00:00", "2026-12"),
        )

        second = server.execute_sync_task({"id": task["id"]})
        assert second["watermarkFrom"] == "2026-11-03 11:00:00"
        assert second["rowsRead"] == 2              # 只读水位之后的两行
        assert len(sync_db.target_rows("t_inc")) == 5
    finally:
        server.delete_sync_task(task["id"])


# ---------------------------------------------------------------- 3) 自定义 SQL 源推断类型
def test_sql_source_auto_create_infers_varchar_not_longtext(sync_db):
    """自定义 SQL 源、目标表不存在 → 按结果集元数据建表，字符串列推 varchar(n)。

    直接守 ``_sync_column_sql_type``：字符串取 internal_size//4（utf8mb4），取不到退 255，
    且**绝不能**一律 longtext。
    """
    sync_db.probe_types = {"玩家ID": (_STRING_TYPE_CODE, 44), "年月": (_STRING_TYPE_CODE, 28)}
    source_sql = 'select "玩家ID", "年月" from "srcdb"."会员小票表"'

    result = server.execute_sync_task(sync_db.adhoc(
        sourceMode="sql", sourceSql=source_sql,
        sourceTable="", targetTable="t_sql", syncMode="full",
    ))

    assert result["targetCreated"] is True
    assert result["rowsWritten"] == 3
    columns = sync_db.target_columns("t_sql")
    assert columns["玩家ID"] == "varchar(11)"  # 44 // 4
    assert columns["年月"] == "varchar(7)"      # 28 // 4
    assert "longtext" not in " ".join(columns.values())


# ---------------------------------------------------------------- 4) 反向用例（必须明确拒绝）
def test_mode_not_ready_is_refused_at_execute_entry(sync_db, monkeypatch):
    """未接上链路的模式必须在**执行入口**明确报错，不能静默降级成别的模式。

    与 tests/test_p8_sync.py 的 test_unknown_mode_is_refused_instead_of_silently_degrading
    同一思路，那条是纯单元（校验 SYNC_MODES_READY 与 SYNC_MODES 一致），这条守的是
    执行入口的守卫本身：一旦某个模式被移出 READY，跑它必须报错而不是"看起来跑通了"。
    """
    monkeypatch.setattr(server, "SYNC_MODES_READY", ("full", "upsert", "incremental"))
    with pytest.raises(ValueError, match="还没接上执行链路"):
        server.execute_sync_task(sync_db.adhoc(syncMode="append"))


def test_upsert_without_key_is_refused_at_execute_entry(sync_db):
    """upsert 不给业务键 → 执行入口直接拒绝（不给它静默变成全量追加的机会）。"""
    with pytest.raises(ValueError, match="键列"):
        server.execute_sync_task(sync_db.adhoc(syncMode="upsert"))
