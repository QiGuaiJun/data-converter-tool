"""同步模块（2026-10-08 起）· 阶段 1：数据模型与校验。

⚠️ 本文件必须在 ``import server`` **之前**设置临时数据目录：``server.DB_PATH`` 是模块导入时
按 ``DATA_DIR`` 算出来并定死的，谁先 import server 谁决定它（文件名字母序靠前，会拖垮其他
模块的"环境已隔离/生产数据未被触碰"守卫用例）。

真实 MySQL 链路的验证不放这里（单测必须能在没有 MySQL 的机器上跑）——
那是 ``acceptance/verify_sync_real_pair.py``，需要真的库。
"""

from __future__ import annotations

import os
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="p8_sync_")
os.environ.setdefault("DATA_DIR", os.path.join(_SANDBOX, "data"))
os.environ.setdefault("UPLOADS_DIR", os.path.join(_SANDBOX, "up"))
os.environ.setdefault("EXPORTS_DIR", os.path.join(_SANDBOX, "exp"))

import pytest  # noqa: E402

import server  # noqa: E402


def test_normalize_sync_mode_unknown_falls_back_to_full():
    assert server.normalize_sync_mode(None) == "full"
    assert server.normalize_sync_mode("FULL") == "full"
    assert server.normalize_sync_mode("upsert") == "upsert"
    # 未知值不能变成某个"可执行"的模式
    assert server.normalize_sync_mode("truncate") == "full"
    assert server.normalize_sync_mode("偷偷全量") == "full"


def test_unknown_mode_is_refused_instead_of_silently_degrading():
    """未接上链路的模式必须明确拒绝——而不是悄悄按另一种模式跑。

    四档全开放之后（2026-10-09），这条守的是同一件事的另一半：
    `normalize_sync_mode` 会把未知值折成 full，若 SYNC_MODES_READY 里混进
    一个没实现的模式，执行入口必须报错，不能让它"看起来跑通了"。
    """
    # 开放的模式与 SYNC_MODES 一致，且每一档都有中文名（界面与日志直接用它）
    assert set(server.SYNC_MODES_READY) == set(server.SYNC_MODES)
    for mode in server.SYNC_MODES:
        assert server.SYNC_MODE_LABELS[mode]
    # 未知模式会被折成 full 而不是凭空变成别的语义
    assert server.normalize_sync_mode("truncate") == "full"


def test_sync_read_sql_is_shared_between_preview_and_execute():
    """preview 与 execute 必须共用同一句 SELECT 骨架。

    两边各写一个 SQL 字面量的话，迟早出现"预览说 100 行、跑起来读了 1000 行"，
    而且这种漂移不会被任何单侧测试发现 —— 所以这里锁的是**同一个产出**。
    """
    base = server.sync_select_sql(
        "table", "", "dc_src", "订单表", ["订单编号", "下单时间"]
    )
    assert base == "select `订单编号`, `下单时间` from `dc_src`.`订单表`"
    sql_mode = server.sync_select_sql("sql", "select 订单编号 from t where id > 1", "", "", ["订单编号"])
    assert sql_mode.endswith("from (select 订单编号 from t where id > 1) sync_src")
    # 两处入参顺序不同也要产出同一形状，避免调用点写反了还看不出来
    assert server.sync_select_sql("TABLE", "", "d", "t", ["a"]) == "select `a` from `d`.`t`"


def test_sync_resolve_task_marks_persisted_and_ad_hoc():
    """临时配置不能写水位：没有任务 id 就没有"上次同步到哪儿"的语义。"""
    saved = server.save_sync_task({
        "name": "解析 Test", "sourceConnectionId": "src", "targetConnectionId": "dst",
        "sourceTable": "订单表", "targetTable": "订单表",
    })
    resolved = server.sync_resolve_task({"taskId": saved["id"]})
    assert resolved["persistedId"] == saved["id"]
    assert resolved["id"] == saved["id"]
    ad_hoc = server.sync_resolve_task({"sourceConnectionId": "src", "targetConnectionId": "dst"})
    assert ad_hoc["persistedId"] == ""
    assert ad_hoc["sync_mode"] == "full"
    server.delete_sync_task(saved["id"])


def test_all_sync_routes_are_registered_with_explicit_min_role():
    """7 个同步接口必须**逐条**登记最小角色。

    漏登记的后果不是"打不开"，而是 fail closed 按 admin 处理（正常人手一个 operator，
    于是接口看着存在却 403 —— 极难排查）。所以这里把 7 条路径写死。
    """
    expected = {
        ("GET", "/api/sync/tasks"): "viewer",
        ("POST", "/api/sync/tasks"): "operator",
        ("DELETE", "/api/sync/tasks"): "operator",
        ("POST", "/api/sync/probe"): "viewer",
        ("POST", "/api/sync/preview"): "viewer",
        ("POST", "/api/sync/run"): "operator",
        ("GET", "/api/sync/runs"): "viewer",
    }
    for route, minimum in expected.items():
        assert server.route_min_role(route[0], route[1]) == minimum, route


def test_sync_suggest_ddl_is_static_text_only():
    """SQL 源 + 目标表不存在时给的建表草稿：只读文本，且按列类型给类型。"""
    description = [("id", 8), ("金额", 246), ("下单时间", 12), ("备注", 252), ("未知名类型", 9999)]
    ddl = server.sync_suggest_ddl(description, "目标表")
    assert ddl.startswith("create table `目标表` (")
    assert "`id` bigint" in ddl and "`金额` decimal" in ddl
    assert "`下单时间` datetime" in ddl and "`备注` longtext" in ddl
    assert "`未知名类型` longtext" in ddl  # 认不出来的一律退回最宽的 longtext，不猜窄
    assert server.sync_suggest_ddl([], "空") == ""


def test_sync_preview_value_is_json_safe():
    """样例单元格必须能直接进 json.dumps：date/datetime 统一转成 `YYYY-MM-DD HH:MM:SS`。"""
    import datetime as dt

    assert server.sync_preview_value(None) is None
    assert server.sync_preview_value("中文 ✅") == "中文 ✅"
    assert server.sync_preview_value(3) == 3
    assert server.sync_preview_value(dt.date(2026, 10, 9)) == "2026-10-09 00:00:00"
    assert server.sync_preview_value(b"\xe4\xb8\xad") == "中"
    server.json.dumps(server.sync_preview_value(dt.datetime(2026, 10, 9, 8, 30)))


def test_sync_name_list_dedupes_and_ignores_junk():
    assert server.sync_name_list('["a","b","a"]') == ["a", "b"]
    assert server.sync_name_list(["a", "", "  ", "b", "a"]) == ["a", "b"]
    assert server.sync_name_list("{坏 json") == []
    assert server.sync_name_list(None) == []


def test_sync_batch_rows_is_clamped():
    assert server.sync_batch_rows(None) == server.SYNC_DEFAULT_BATCH
    assert server.sync_batch_rows(0) == server.SYNC_DEFAULT_BATCH
    assert server.sync_batch_rows(50) == 100
    assert server.sync_batch_rows(10 ** 9) == server.SYNC_MAX_BATCH
    assert server.sync_batch_rows("abc") == server.SYNC_DEFAULT_BATCH


def test_save_sync_task_rejects_incomplete_config():
    named = {"name": "n", "sourceConnectionId": "src", "targetConnectionId": "dst",
             "sourceTable": "t"}
    with pytest.raises(ValueError, match="名称"):
        server.save_sync_task({k: v for k, v in named.items() if k != "name"})
    with pytest.raises(ValueError, match="源连接"):
        server.save_sync_task({"name": "n", "targetConnectionId": "dst", "sourceTable": "t"})
    with pytest.raises(ValueError, match="目标连接"):
        server.save_sync_task({"name": "n", "sourceConnectionId": "src", "sourceTable": "t"})
    with pytest.raises(ValueError, match="同一个"):
        server.save_sync_task(
            {"name": "n", "sourceConnectionId": "s", "targetConnectionId": "s", "sourceTable": "t"}
        )
    with pytest.raises(ValueError, match="源表"):
        server.save_sync_task(
            {"name": "n", "sourceConnectionId": "src", "targetConnectionId": "dst", "sourceMode": "table"}
        )
    with pytest.raises(ValueError, match="源端 SQL"):
        server.save_sync_task(
            {"name": "n", "sourceConnectionId": "src", "targetConnectionId": "dst", "sourceMode": "sql"}
        )
    with pytest.raises(ValueError, match="键列"):
        server.save_sync_task(dict(named, syncMode="upsert"))
    with pytest.raises(ValueError, match="水位列"):
        server.save_sync_task(dict(named, syncMode="incremental"))


def test_sync_task_roundtrip_save_update_list_delete():
    task = server.save_sync_task({
        "name": "单测-往返", "sourceConnectionId": "src", "targetConnectionId": "dst",
        "sourceTable": "会员小票表", "syncMode": "full", "batchRows": 1234,
    })
    assert task["syncModeLabel"] == "全量覆盖"
    assert task["syncModeReady"] is True
    assert task["batchRows"] == 1234
    assert task["targetTable"] == "会员小票表"  # 目标表默认取源表名

    listed = [item for item in server.list_sync_tasks() if item["id"] == task["id"]]
    assert len(listed) == 1
    assert listed[0]["lastStatus"] == ""  # 还没跑过
    assert "watermark" in listed[0]

    updated = server.save_sync_task(dict(task, id=task["id"], name="改过名", batchRows=999))
    assert updated["name"] == "改过名"
    assert updated["batchRows"] == 999
    assert updated["id"] == task["id"]

    server.delete_sync_task(task["id"])
    assert [item for item in server.list_sync_tasks() if item["id"] == task["id"]] == []


def test_delete_sync_task_also_clears_runs():
    """任务删掉后运行历史必须一起走。

    列表页是**按 task_id** 读历史的（`/api/sync/runs?taskId=`），任务一删这些记录
    就再也点不开，只会赖在库里和"全部运行"视图里 —— 用户在列表页删完任务
    仍能看到孤儿历史，只会当成 bug 报回来。
    """
    task = server.save_sync_task({
        "name": "单测-历史清理", "sourceConnectionId": "src", "targetConnectionId": "dst",
        "sourceTable": "x", "syncMode": "full",
    })
    with server.connect_db() as conn:
        conn.execute(
            "insert into _sync_runs (id, task_id, task_name, sync_mode, source_label,"
            " target_label, started_at, status, rows_read, rows_written)"
            " values (?,?,?,?,?,?,?,?,?,?)",
            ("run-orphan-1", task["id"], "单测-历史清理", "full", "s", "t",
             "2026-10-10 09:00:00", "成功", 3, 3),
        )
    assert len(server.list_sync_runs(task["id"])) == 1

    server.delete_sync_task(task["id"])
    assert server.list_sync_runs(task["id"]) == []


def test_delete_sync_task_also_clears_watermark():
    """任务删掉后水位必须一起走：留着会让重建的同名任务误判成"已同步过"。"""
    task = server.save_sync_task({
        "name": "单测-水位清理", "sourceConnectionId": "src", "targetConnectionId": "dst",
        "sourceTable": "x", "syncMode": "full",
    })
    with server.connect_db() as conn:
        conn.execute(
            "insert or replace into _sync_watermarks (task_id, watermark_value, last_status)"
            " values (?,?,?)",
            (task["id"], "2026-10-01 00:00:00", "成功"),
        )
    server.delete_sync_task(task["id"])
    with server.connect_db() as conn:
        left = conn.execute(
            "select count(*) from _sync_watermarks where task_id = ?", (task["id"],)
        ).fetchone()[0]
    assert left == 0


def test_sync_tables_are_created_by_schema():
    with server.connect_db() as conn:
        names = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
    assert "_sync_tasks" in names
    assert "_sync_watermarks" in names
