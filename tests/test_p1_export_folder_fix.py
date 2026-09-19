"""P1 导出目标文件夹修复的常驻回归测试。

为什么要有这个文件：上轮修复的验证脚本只存在于 %TEMP% 里，项目内没有常驻用例，
一旦后续改动碰了 run_saved_job / 导出日志拼装，回归就无人看守。这里把关键断言固化下来。

覆盖：
- P1    导出步骤未配 exportFolder → 步骤 message 含产物绝对路径 + 默认目录警告文案
- P1    导出步骤配了 exportFolder → 产物落指定目录、且 message 不含警告
- P1    嵌套 type=job 子作业 → 顶层 outputs 合并自身与子作业产物、顶层 message 含「产出文件」
- P1    同一文件被父子作业各收集一次时按路径去重，「产出文件（N 个）」的 N == 实际文件数
- P1-1  前端 public/schedule.js 的 mergeScheduleRuns：真实运行记录 → 路径与表头各只出现一次
       （用 node 对真实前端源码求值，不重写前端逻辑；本机无 node 时自动跳过）
- P1-A  子作业失败时父 run 的 message 只能有一个「产出文件」表头/一份路径，且失败原因仍可见
       （父子导出同一个文件 / 父子导出不同文件两种场景都看住）
- P2-1  失败分支 outputs 非空时 message 也要列出已落盘产物
- P2-A  产物去重按平台语义归一化：Windows 大小写 / 分隔符变体算同一个文件，展示首次的原始串
- P2-B  前端不再用正则从 message 认产物：换行 + 盘符开头的非产物行不会被当成产物
- P2-C  失败 run + 父子导出同一文件的前端合并用例（删掉表头去重逻辑就会 FAIL，见负向对照）
- P3-B  /api/job-runs 形状（_frontend_runs）携带 run.outputs 结构化产物清单
- P3-C  嵌套子作业失败时，子作业已落盘的半成品仍冒泡进父 outputs（与父 message 一致）
- 降级  历史 run 没有 outputs 字段 → 前端按 message 原样渲染，不报错、不丢信息
- 一致性 成功 / 失败 / 跳过三条出口的返回键集合完全一致
- 边界  is_server_default_export() 识别服务端默认目录

隔离：DATA_DIR / UPLOADS_DIR / EXPORTS_DIR 全部指向本进程的临时目录；数据源只用 SQLite，
不连 MySQL、不触碰真实生产库。每个用例前后各清理一次（临时作业行 / 运行记录 / 夹具表 /
落盘文件）；进程退出时统一关闭隔离库连接并删除临时目录，跑完 %TEMP% 与测试库都是零残留。
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# 隔离测试环境：数据/上传/导出落临时目录，不触碰真实 data/ uploads/ exports/
_tmp = tempfile.mkdtemp(prefix="dc_p1_")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")

import server

# sqlite3 连接作为 with 语句使用时只提交事务、不 close，句柄会一直占着临时库文件；
# Windows 上这会挡住隔离目录的删除，把 %TEMP%/dc_p1_* 越攒越多。这里记录句柄，
# 进程退出时统一关闭再删目录，保证本文件跑完 %TEMP% 零残留。
_TRACKED_CONNECTIONS: list[object] = []
_ORIGINAL_CONNECT_DB = server.connect_db


def _tracking_connect_db():
    conn = _ORIGINAL_CONNECT_DB()
    _TRACKED_CONNECTIONS.append(conn)
    return conn


server.connect_db = _tracking_connect_db


@atexit.register
def _remove_isolated_tmp() -> None:
    for conn in _TRACKED_CONNECTIONS:
        try:
            conn.close()
        except Exception:  # pragma: no cover - 关闭失败不影响测试结论
            pass
    shutil.rmtree(_tmp, ignore_errors=True)

PROJECT_DIR = Path(__file__).resolve().parent.parent
NODE_BIN = shutil.which("node")

# 本文件创建的夹具表（跑完必须删掉，不留在测试库里）
FIXTURE_TABLES = ("p1_export_src", "p1_nested_src", "p1_dup_src")

# 三条出口（成功 / 失败 / 跳过）必须返回完全相同的键集合
EXPECTED_RUN_KEYS = {"id", "jobId", "status", "message", "outputs"}

# 前端探针：把 public/schedule.js 的整体源码放进 Function 里求值，取到真实的
# mergeScheduleRuns（不是复制一份逻辑），输入是服务端真实落库的运行记录 JSON，输出合并结果。
FRONTEND_MERGE_PROBE = r'''
const fs = require("fs");
const source = fs.readFileSync(process.argv[2], "utf8");
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const element = {
  textContent: "", innerHTML: "", className: "", value: "", checked: false, dataset: {},
  classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
  addEventListener() {}, showModal() {}, close() {}, closest() { return element; },
  querySelector() { return element; }, querySelectorAll() { return []; }, forEach() {},
};
const document = { querySelector() { return element; }, querySelectorAll() { return []; }, addEventListener() {}, body: element };
const window = { setInterval() {}, clearInterval() {}, setTimeout() {}, location: { href: "" } };
const fetch = async () => ({ ok: true, json: async () => ({}) });
const factory = new Function(
  "document", "window", "fetch", "confirm", "alert", "localStorage", "setInterval", "clearInterval",
  '"use strict";\n' + source + "\nreturn mergeScheduleRuns;",
);
const mergeScheduleRuns = factory(
  document, window, fetch, () => true, () => {},
  { getItem() { return null; }, setItem() {} }, () => 0, () => {},
);
process.stdout.write(JSON.stringify({ merged: mergeScheduleRuns(payload.runs, payload.rootJobId) }));
'''


# 前端探针：同样对真实 public/jobs.js 求值，取到作业详情页的 renderRunLog，
# 用来验证「有 outputs 字段 → 只渲染一份清单」「没有该字段的历史记录 → 原样渲染不报错」。
FRONTEND_JOBS_PROBE = r'''
const fs = require("fs");
const source = fs.readFileSync(process.argv[2], "utf8");
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const element = {
  textContent: "", innerHTML: "", className: "", value: "", checked: false, dataset: {},
  classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
  addEventListener() {}, showModal() {}, close() {}, closest() { return element; },
  querySelector() { return element; }, querySelectorAll() { return []; }, forEach() {},
};
const document = { querySelector() { return element; }, querySelectorAll() { return []; }, addEventListener() {}, body: element };
const window = { setInterval() {}, clearInterval() {}, setTimeout() {}, location: { href: "" } };
const fetch = async () => ({ ok: true, json: async () => ({ jobs: [], connections: [], runs: [] }) });
const factory = new Function(
  "document", "window", "fetch", "confirm", "alert", "localStorage", "setInterval", "clearInterval",
  '"use strict";\n' + source + "\nreturn renderRunLog;",
);
const renderRunLog = factory(
  document, window, fetch, () => true, () => {},
  { getItem() { return null; }, setItem() {} }, () => 0, () => {},
);
process.stdout.write(JSON.stringify({ html: payload.runs.map(renderRunLog).join("") }));
'''


def _run_node_probe(probe_source: str, frontend_file: Path, runs: list[dict[str, object]], tmp_path: Path, extra: dict[str, object] | None = None) -> dict[str, object]:
    """把真实前端源码交给 node 求值，返回探针输出（供断言消费）。"""
    probe = tmp_path / "frontend_probe.cjs"
    probe.write_text(probe_source, encoding="utf-8")
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({**(extra or {}), "runs": runs}, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [str(NODE_BIN), str(probe), str(frontend_file), str(payload)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, f"node 求值 {frontend_file.name} 失败：{completed.stderr}"
    return json.loads(completed.stdout)


# ---------------------------------------------------------------------------
# 夹具与自清理
# ---------------------------------------------------------------------------


def _make_table(table: str, rows: int = 3) -> None:
    """在隔离 SQLite 库里建一张导出源表（不碰 MySQL）。"""
    with server.connect_db() as conn:
        conn.execute(f"drop table if exists {table}")
        conn.execute(f"create table {table} (id integer, name text)")
        conn.executemany(f"insert into {table} values (?, ?)", [(index, f"n{index}") for index in range(rows)])


def export_step_config(table: str, output_name: str, folder: Path | None = None) -> dict[str, object]:
    """构造一个导出步骤配置；folder 为空即模拟"用户漏填目标文件夹"。"""
    config: dict[str, object] = {
        "targetDbType": "sqlite",
        "items": [{"type": "table", "table": table, "name": table}],
        "extension": "csv",
        "outputName": output_name,
    }
    if folder is not None:
        config["exportTargetMode"] = "folder"
        config["exportFolder"] = str(folder)
    return config


def make_step(name: str, step_type: str, config: dict[str, object]) -> dict[str, object]:
    return {"name": name, "type": step_type, "enabled": True, "continueOnError": False, "config": config}


def save_job(name: str, steps: list[dict[str, object]], guard: dict[str, object] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"name": name, "steps": steps}
    if guard is not None:
        payload["guard"] = guard
    return server.save_job(payload)


def fetch_run(run_id: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    """从库里读回落盘的运行记录与步骤记录（校验写库内容与返回值一致）。"""
    with server.connect_db() as conn:
        run_row = conn.execute("select * from _job_runs where id = ?", (run_id,)).fetchone()
        step_rows = conn.execute(
            "select * from _job_run_steps where run_id = ? order by step_index", (run_id,)
        ).fetchall()
    assert run_row is not None, f"运行记录未落库：{run_id}"
    return dict(run_row), [dict(row) for row in step_rows]


def _frontend_runs(*job_ids: str) -> list[dict[str, object]]:
    """把真实落库的运行记录整理成前端 /api/job-runs 的形状（供 mergeScheduleRuns 消费）。

    只对齐分组键（schedule_id / started_at）：手动运行没有 schedule_id，父子 run 跨秒时
    本来分不到一组，测试需要确定性分组。message / steps / outputs 原样保留，不改写服务端产出；
    outputs 与 server.handle_job_runs 一样由 _job_runs.outputs_json 反解（历史记录 → []）。
    """
    placeholders = ",".join("?" for _ in job_ids)
    with server.connect_db() as conn:
        run_rows = [
            dict(row)
            for row in conn.execute(
                f"select * from _job_runs where job_id in ({placeholders}) order by started_at", job_ids
            ).fetchall()
        ]
        step_rows = [
            dict(row)
            for row in conn.execute(
                f"select * from _job_run_steps where run_id in "
                f"(select id from _job_runs where job_id in ({placeholders})) order by step_index",
                job_ids,
            ).fetchall()
        ]
    step_keys = ("id", "step_index", "step_name", "step_type", "started_at", "ended_at", "elapsed_ms", "status", "message")
    return [
        {
            "id": row["id"],
            "job_id": row["job_id"],
            "job_name": row["job_name"],
            "schedule_id": "p1_fixture",
            "started_at": "2026-01-01 00:00:00",
            "ended_at": row["ended_at"],
            "elapsed_ms": row["elapsed_ms"],
            "status": row["status"],
            "message": row["message"],
            "outputs": server.decode_run_outputs(row.get("outputs_json", "")),
            "steps": [{key: step[key] for key in step_keys} for step in step_rows if step["run_id"] == row["id"]],
        }
        for row in run_rows
    ]


def _merge_via_frontend(runs: list[dict[str, object]], root_job_id: str, tmp_path: Path) -> list[dict[str, object]]:
    """真实 public/schedule.js 的 mergeScheduleRuns 求值结果。"""
    result = _run_node_probe(
        FRONTEND_MERGE_PROBE, PROJECT_DIR / "public" / "schedule.js", runs, tmp_path, {"rootJobId": root_job_id}
    )
    return result["merged"]


def _cleanup() -> None:
    """清掉本文件产生的一切痕迹：作业行、运行记录、夹具表、落盘文件。"""
    with server.connect_db() as conn:
        job_ids = [
            row["id"]
            for row in conn.execute("select id from _jobs where name like 'p1_%'").fetchall()
        ]
        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            conn.execute(
                f"delete from _job_run_steps where run_id in "
                f"(select id from _job_runs where job_id in ({placeholders}))",
                job_ids,
            )
            conn.execute(f"delete from _job_runs where job_id in ({placeholders})", job_ids)
            conn.execute(f"delete from _job_file_guards where job_id in ({placeholders})", job_ids)
            conn.execute(f"delete from _jobs where id in ({placeholders})", job_ids)
        # 兜底：作业行已被删掉但运行记录仍在（如手工建运行记录的场景）
        conn.execute("delete from _job_runs where job_name like 'p1_%'")
        for table in FIXTURE_TABLES:
            conn.execute(f"drop table if exists {table}")
    for folder in (Path(_tmp) / "exports", Path(_tmp) / "out"):
        shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_cleanup():
    """每个用例前后都清一次：即使断言失败，也不给测试库和磁盘留下残留。"""
    _cleanup()
    yield
    _cleanup()


# ---------------------------------------------------------------------------
# P1：导出步骤的产物路径与默认目录警告
# ---------------------------------------------------------------------------


def test_export_without_folder_logs_absolute_path_and_warning() -> None:
    """未配 exportFolder：步骤 message 必须给出产物绝对路径，并警告是服务端默认目录。"""
    _make_table("p1_export_src")
    job = save_job(
        "p1_no_folder",
        [make_step("导出", "export", export_step_config("p1_export_src", "p1_no_folder_out"))],
    )
    run = server.run_saved_job(str(job["id"]))
    assert run["status"] == "成功"

    expected = str(Path(server.EXPORTS) / "p1_no_folder_out.csv")
    assert run["outputs"] == [expected]
    assert Path(expected).is_file(), "产物必须真实落盘"
    assert Path(expected).is_absolute()
    assert server.is_server_default_export(expected) is True

    run_row, step_rows = fetch_run(str(run["id"]))
    step_message = str(step_rows[0]["message"])
    assert expected in step_message, "步骤 message 必须含产物绝对路径"
    assert "警告" in step_message and "未配置目标文件夹" in step_message
    assert expected in str(run_row["message"]), "落库的顶层 message 必须与返回值一致"
    assert "产出文件（1 个）：" in str(run["message"])
    assert str(run["message"]).count(expected) == 1


def test_export_with_folder_writes_there_without_warning() -> None:
    """配了 exportFolder：产物落在指定目录，且日志里不能再出现默认目录警告。"""
    _make_table("p1_export_src")
    folder = Path(_tmp) / "out" / "p1_target"
    job = save_job(
        "p1_with_folder",
        [make_step("导出", "export", export_step_config("p1_export_src", "p1_with_folder_out", folder))],
    )
    run = server.run_saved_job(str(job["id"]))
    assert run["status"] == "成功"

    expected = str(folder / "p1_with_folder_out.csv")
    assert run["outputs"] == [expected]
    assert Path(expected).is_file(), "产物必须落在 exportFolder 指定的目录"
    assert server.is_server_default_export(expected) is False

    _, step_rows = fetch_run(str(run["id"]))
    step_message = str(step_rows[0]["message"])
    assert expected in step_message
    assert "警告" not in step_message, "已配置目标文件夹时不得再报警告"
    assert "警告" not in str(run["message"])


def test_nested_job_outputs_bubble_up_to_top_level() -> None:
    """嵌套 type=job：顶层 outputs = 自身步骤产物 + 子作业产物，顶层 message 含「产出文件」。"""
    _make_table("p1_export_src")
    _make_table("p1_nested_src")
    child = save_job(
        "p1_nested_child",
        [make_step("子导出", "export", export_step_config("p1_nested_src", "p1_nested_child_out"))],
    )
    parent = save_job(
        "p1_nested_parent",
        [
            make_step("自身导出", "export", export_step_config("p1_export_src", "p1_nested_parent_out")),
            make_step("调用子作业", "job", {"jobId": child["id"]}),
        ],
    )
    run = server.run_saved_job(str(parent["id"]))
    assert run["status"] == "成功"

    parent_file = str(Path(server.EXPORTS) / "p1_nested_parent_out.csv")
    child_file = str(Path(server.EXPORTS) / "p1_nested_child_out.csv")
    assert run["outputs"] == [parent_file, child_file], "顶层必须合并自身与子作业的产物"
    assert len(run["outputs"]) == len(set(run["outputs"])), "顶层 outputs 不得有重复路径"
    assert Path(parent_file).is_file() and Path(child_file).is_file()

    message = str(run["message"])
    assert "产出文件（2 个）：" in message
    assert message.count("产出文件（") == 1, "「产出文件」表头只允许出现一次"
    assert message.count(parent_file) == 1 and message.count(child_file) == 1

    # 子作业自己的 message 里也含同一批路径 —— 这正是前端合并父子 run 时会重复的根源，
    # 前端 mergeScheduleRuns 已按路径去重（见 public/schedule.js）
    with server.connect_db() as conn:
        child_run = conn.execute(
            "select * from _job_runs where job_id = ? and message like ?", (child["id"], "%产出文件%")
        ).fetchone()
    assert child_run is not None and child_file in str(child_run["message"])


def test_duplicate_outputs_are_deduped_and_count_matches_files() -> None:
    """父子作业导出同一个文件时，路径与表头都不重复，N 等于实际文件数。"""
    _make_table("p1_dup_src")
    child = save_job(
        "p1_dup_child",
        [make_step("子导出", "export", export_step_config("p1_dup_src", "p1_dup_same_out"))],
    )
    parent = save_job(
        "p1_dup_parent",
        [
            make_step("自身导出", "export", export_step_config("p1_dup_src", "p1_dup_same_out")),
            make_step("调用子作业", "job", {"jobId": child["id"]}),
        ],
    )
    run = server.run_saved_job(str(parent["id"]))
    assert run["status"] == "成功"

    same_file = str(Path(server.EXPORTS) / "p1_dup_same_out.csv")
    assert run["outputs"] == [same_file], "同一条路径被收集两次后必须去重成 1 条"
    message = str(run["message"])
    assert "产出文件（1 个）：" in message
    assert message.count("产出文件（") == 1
    assert message.count(same_file) == 1


# ---------------------------------------------------------------------------
# P1-1：前端合并父子 run 时不重复列产物（真实前端源码 + 真实服务端运行记录）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(NODE_BIN is None, reason="本机没有 node，跳过前端合并函数的端到端回归")
def test_frontend_merge_dedupes_outputs_and_keeps_child_steps(tmp_path: Path) -> None:
    """服务端真实运行记录 → 真实 public/schedule.js 的 mergeScheduleRuns → 路径/表头各 1 次。"""
    _make_table("p1_export_src")
    _make_table("p1_nested_src")
    child = save_job(
        "p1_front_child",
        [make_step("子导出", "export", export_step_config("p1_nested_src", "p1_front_child_out"))],
    )
    parent = save_job(
        "p1_front_parent",
        [
            make_step("自身导出", "export", export_step_config("p1_export_src", "p1_front_parent_out")),
            make_step("调用子作业", "job", {"jobId": child["id"]}),
        ],
    )
    run = server.run_saved_job(str(parent["id"]))
    assert run["status"] == "成功"

    runs = _frontend_runs(str(parent["id"]), str(child["id"]))
    assert len(runs) == 2, "父子两条运行记录都要喂给前端函数"

    probe = tmp_path / "merge_probe.cjs"
    probe.write_text(FRONTEND_MERGE_PROBE, encoding="utf-8")
    payload = tmp_path / "runs.json"
    payload.write_text(
        json.dumps({"runs": runs, "rootJobId": str(parent["id"])}, ensure_ascii=False), encoding="utf-8"
    )
    completed = subprocess.run(
        [str(NODE_BIN), str(probe), str(PROJECT_DIR / "public" / "schedule.js"), str(payload)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, f"node 求值 public/schedule.js 失败：{completed.stderr}"

    merged = json.loads(completed.stdout)["merged"]
    assert len(merged) == 1, "同一 schedule_id + started_at 的父子 run 必须合并成一条"
    top = merged[0]
    message = str(top["message"])
    parent_file = str(Path(server.EXPORTS) / "p1_front_parent_out.csv")
    child_file = str(Path(server.EXPORTS) / "p1_front_child_out.csv")

    assert top["status"] == "成功"
    assert message.count(parent_file) == 1, f"自身产物路径应只出现一次：{message}"
    assert message.count(child_file) == 1, f"子作业产物路径应只出现一次：{message}"
    assert message.count("产出文件（") == 1, f"「产出文件」表头应只出现一次：{message}"
    assert "产出文件（2 个）：" in message, f"清单数量应等于实际文件数：{message}"

    # 子 run 的步骤级 message 必须仍然可见（要求是文件路径去重，不是砍掉子 run 的 message）
    assert any(child_file in str(step.get("message", "")) for step in top["steps"]), "子 run 步骤级 message 丢失"
    assert any("p1_front_child" in str(step.get("step_name", "")) for step in top["steps"]), "子 run 步骤名丢失"


# ---------------------------------------------------------------------------
# P2-1：失败分支也要列出已落盘产物
# ---------------------------------------------------------------------------


def test_failed_run_lists_partial_outputs() -> None:
    """第 1 步导出成功、第 2 步失败：失败 message 必须给出半成品路径。"""
    _make_table("p1_export_src")
    job = save_job(
        "p1_fail_partial",
        [
            make_step("先导出", "export", export_step_config("p1_export_src", "p1_fail_partial_out")),
            make_step("再查询", "query", {"targetDbType": "sqlite", "sql": "select * from p1_table_missing"}),
        ],
    )
    run = server.run_saved_job(str(job["id"]))
    assert run["status"] == "失败"

    expected = str(Path(server.EXPORTS) / "p1_fail_partial_out.csv")
    assert run["outputs"] == [expected], "失败时也要把已落盘产物交出来"
    assert Path(expected).is_file()

    message = str(run["message"])
    assert expected in message, "失败 message 必须含产物路径"
    assert "产出文件（1 个，失败前已落盘）：" in message
    assert "最后错误" in message
    assert message.count(expected) == 1

    run_row, _ = fetch_run(str(run["id"]))
    assert str(run_row["message"]) == message
    assert str(run_row["status"]) == "失败"


# ---------------------------------------------------------------------------
# P1-A / P3-C：子作业失败时的产物清单与失败原因
# ---------------------------------------------------------------------------


def test_failed_nested_job_same_file_keeps_single_outputs_block() -> None:
    """QA 复现场景①：父自身导出 A.csv + 子作业（导出 A.csv 后失败）→ 路径 1 次、表头 1 次。

    这正是上轮漏修的失败分支：子作业整段 message（含它自己的「产出文件」块）被内嵌进
    last_error，本层又追加一次自己的产物块，同一条 run message 就出现 2 个表头、同一路径 2 次。
    """
    _make_table("p1_dup_src")
    child = save_job(
        "p1_qa_child",
        [
            make_step("子导出", "export", export_step_config("p1_dup_src", "p1_qa_same_out")),
            make_step("子查询", "query", {"targetDbType": "sqlite", "sql": "select * from p1_qa_missing"}),
        ],
    )
    parent = save_job(
        "p1_qa_parent",
        [
            make_step("自身导出", "export", export_step_config("p1_dup_src", "p1_qa_same_out")),
            make_step("调用子作业", "job", {"jobId": child["id"]}),
        ],
    )
    run = server.run_saved_job(str(parent["id"]))
    assert run["status"] == "失败"

    same_file = str(Path(server.EXPORTS) / "p1_qa_same_out.csv")
    message = str(run["message"])
    print(f"\n[P1-A 场景① 原始 run.message]\n{message}")
    assert Path(same_file).is_file(), "半成品必须真实落盘"
    assert run["outputs"] == [same_file], "父 outputs 只有 1 个文件"
    assert message.count(same_file) == 1, f"产物路径只能出现 1 次：\n{message}"
    assert message.count("产出文件（") == 1, f"「产出文件」表头只能出现 1 次：\n{message}"
    assert "产出文件（1 个，失败前已落盘）：" in message
    assert "no such table: p1_qa_missing" in message, "失败原因不得被剥掉"
    assert "最后错误" in message

    run_row, step_rows = fetch_run(str(run["id"]))
    assert str(run_row["message"]) == message, "落库 message 必须与返回值一致"
    step_message = str(step_rows[1]["message"])
    assert "产出文件" not in step_message, f"失败的 job 步骤不该再嵌一份产物块：{step_message}"
    assert "no such table: p1_qa_missing" in step_message


def test_failed_nested_job_different_files_keeps_single_header() -> None:
    """QA 复现场景②：父子导出不同文件 + 子作业失败 → 表头 1 次、两个路径各 1 次。"""
    _make_table("p1_export_src")
    _make_table("p1_nested_src")
    child = save_job(
        "p1_qa2_child",
        [
            make_step("子导出", "export", export_step_config("p1_nested_src", "p1_qa2_child_out")),
            make_step("子查询", "query", {"targetDbType": "sqlite", "sql": "select * from p1_qa2_missing"}),
        ],
    )
    parent = save_job(
        "p1_qa2_parent",
        [
            make_step("自身导出", "export", export_step_config("p1_export_src", "p1_qa2_parent_out")),
            make_step("调用子作业", "job", {"jobId": child["id"]}),
        ],
    )
    run = server.run_saved_job(str(parent["id"]))
    assert run["status"] == "失败"

    parent_file = str(Path(server.EXPORTS) / "p1_qa2_parent_out.csv")
    child_file = str(Path(server.EXPORTS) / "p1_qa2_child_out.csv")
    message = str(run["message"])
    print(f"\n[P1-A 场景② 原始 run.message]\n{message}")
    # P3-C：失败时子作业已落盘的半成品必须冒泡，父 outputs 与父 message 内容一致
    assert run["outputs"] == [parent_file, child_file]
    assert message.count("产出文件（") == 1, f"「产出文件」表头只能出现 1 次：\n{message}"
    assert message.count(parent_file) == 1 and message.count(child_file) == 1
    assert "产出文件（2 个，失败前已落盘）：" in message
    assert "no such table: p1_qa2_missing" in message

    _, step_rows = fetch_run(str(run["id"]))
    failed_step = str(step_rows[1]["message"])
    assert "no such table: p1_qa2_missing" in failed_step, "失败原因必须留在步骤日志里"
    assert "产出文件" not in failed_step


def test_strip_outputs_block_keeps_error_text_that_merely_looks_like_a_path() -> None:
    """剥离清单只按"确属该子作业产物"的白名单，错误文本里像路径的行不能被误删。"""
    known = [str(Path(server.EXPORTS) / "kept_out.csv")]
    raw = (
        "作业执行失败：1 个步骤失败。最后错误：导入失败：\n"
        "D:\\input\\不存在的源表.xlsx 路径不存在\n"
        "产出文件（1 个，失败前已落盘）：\n"
        f"{known[0]}\n"
        "（警告：该导出步骤未配置目标文件夹）"
    )
    stripped = server.strip_outputs_block(raw, known)
    assert "no such table" not in stripped  # 无关内容不该凭空出现
    assert "D:\\input\\不存在的源表.xlsx 路径不存在" in stripped, "非产物行是失败原因的一部分，必须保留"
    assert "产出文件" not in stripped and "（警告：" not in stripped
    assert known[0] not in stripped
    assert "最后错误" in stripped


# ---------------------------------------------------------------------------
# P2-A：产物去重按平台语义归一化
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="P2-A 只说 Windows 语义：POSIX 下大小写变体是两个真文件，本就该分开")
def test_dedupe_export_outputs_normalizes_case_and_separators_on_windows() -> None:
    """Windows：同一真实文件的大小写 / 分隔符变体算 1 个，展示首次出现的原始字符串。"""
    first = str(Path(server.EXPORTS) / "Shared" / "qc_case_out.csv")
    variants = [first, first.upper(), first.replace("\\", "/"), str(Path(server.EXPORTS) / "SHARED" / "QC_CASE_OUT.csv")]
    result = server.dedupe_export_outputs(variants)
    assert result == [first], f"大小写/分隔符变体必须归一化成 1 条且保留原始串：{result}"

    message = "（占位）" + server.format_outputs_block(result, failed=True)
    assert message.count("产出文件（1 个，失败前已落盘）：") == 1
    assert message.count(first) == 1


def test_dedupe_export_outputs_drops_blank_and_exact_duplicates() -> None:
    """平台无关的基线行为：空白项丢弃、精确重复只留首次出现的那条，顺序不变。"""
    paths = ["C:\\out\\a.csv", "", "   ", "C:\\out\\b.csv", "C:\\out\\a.csv", "C:\\out\\b.csv"]
    assert server.dedupe_export_outputs(paths) == ["C:\\out\\a.csv", "C:\\out\\b.csv"]


# ---------------------------------------------------------------------------
# P2-B / P2-A / 降级：前端只认 run.outputs 字段，不再正则解析 message 文本
# ---------------------------------------------------------------------------


@pytest.mark.skipif(NODE_BIN is None, reason="本机没有 node，跳过前端合并函数的端到端回归")
def test_frontend_merge_failed_parent_child_same_file_single_header(tmp_path: Path) -> None:
    """P2-C：失败 run + 父子导出同一文件 → 合并结果里表头 1 次、路径 1 次、失败原因可见。

    这条用例是"表头去重"这道防线的看守：把 public/schedule.js 里 dropDetailLines 丢弃
    「产出文件」表头那一行条件删掉（QA 的负向对照变体 B），本用例必须 FAIL ——
    删掉后父 run 自己 message 里的表头会留在正文，与重新生成的清单表头一起出现 2 次。
    用的是真实服务端运行记录（隔离 SQLite）+ 真实前端源码，不是手搓的输入。
    """
    _make_table("p1_dup_src")
    child = save_job(
        "p1_qa3_child",
        [
            make_step("子导出", "export", export_step_config("p1_dup_src", "p1_qa3_same_out")),
            make_step("子查询", "query", {"targetDbType": "sqlite", "sql": "select * from p1_qa3_missing"}),
        ],
    )
    parent = save_job(
        "p1_qa3_parent",
        [
            make_step("自身导出", "export", export_step_config("p1_dup_src", "p1_qa3_same_out")),
            make_step("调用子作业", "job", {"jobId": child["id"]}),
        ],
    )
    run = server.run_saved_job(str(parent["id"]))
    assert run["status"] == "失败"

    runs = _frontend_runs(str(parent["id"]), str(child["id"]))
    assert len(runs) == 2, "父子两条运行记录都要喂给前端函数"
    assert all(row["status"] == "失败" for row in runs)
    merged = _merge_via_frontend(runs, str(parent["id"]), tmp_path)
    assert len(merged) == 1, "同一 schedule_id + started_at 的父子 run 必须合并成一条"

    same_file = str(Path(server.EXPORTS) / "p1_qa3_same_out.csv")
    top = merged[0]
    message = str(top["message"])
    print(f"\n[P2-C 合并后 message]\n{message}")
    assert top["status"] == "失败"
    assert message.count("产出文件（") == 1, f"「产出文件」表头只能出现 1 次：\n{message}"
    assert message.count(same_file) == 1, f"同一产物路径只能出现 1 次：\n{message}"
    assert "no such table: p1_qa3_missing" in message, f"失败原因必须仍然可见：\n{message}"
    assert top["outputs"] == [same_file]


@pytest.mark.skipif(NODE_BIN is None, reason="本机没有 node，跳过前端合并函数的端到端回归")
def test_frontend_merge_reads_outputs_field_and_ignores_drive_letter_noise(tmp_path: Path) -> None:
    """换行 + 盘符开头的非产物行不得被当成产物；产物数只由 outputs 字段决定。"""
    output_file = "C:\\dc_out\\parent_partial.xlsx"
    noisy = "D:\\input\\不存在的源表.xlsx 路径不存在"
    parent_run = {
        "id": "p2b_parent",
        "job_id": "p2b_parent_job",
        "job_name": "p2b_parent_job",
        "schedule_id": "p2b_fixture",
        "started_at": "2026-01-01 00:00:00",
        "ended_at": "2026-01-01 00:00:01",
        "elapsed_ms": 1000,
        "status": "失败",
        "message": f"作业执行失败：1 个步骤失败。最后错误：导入失败：\n{noisy}\n产出文件（1 个，失败前已落盘）：\n{output_file}",
        "outputs": [output_file],
        "steps": [],
    }
    child_run = {
        "id": "p2b_child",
        "job_id": "p2b_child_job",
        "job_name": "p2b_child_job",
        "schedule_id": "p2b_fixture",
        "started_at": "2026-01-01 00:00:00",
        "ended_at": "2026-01-01 00:00:02",
        "elapsed_ms": 2000,
        "status": "失败",
        "message": "作业执行失败：1 个步骤失败。最后错误：no such table: p2b_missing",
        # 历史记录形状：完全没有 outputs 字段（降级路径与结构化路径在同一条日志里共存）
        "steps": [],
    }
    merged = _merge_via_frontend([parent_run, child_run], "p2b_parent_job", tmp_path)
    assert len(merged) == 1
    message = str(merged[0]["message"])
    print(f"\n[P2-B 合并后 message]\n{message}")
    assert "产出文件（2 个" not in message, "盘符开头的非产物行不得被计入 N"
    assert message.count("产出文件（") == 1
    assert message.count(output_file) == 1, "真实产物只列一次"
    assert noisy in message, "非产物行是错误原因的一部分，必须留在文本里"
    assert merged[0]["outputs"] == [output_file], "合并结果的 outputs 只含真实产物"
    assert merged[0]["status"] == "失败"


@pytest.mark.skipif(NODE_BIN is None, reason="本机没有 node，跳过前端合并函数的端到端回归")
def test_frontend_merge_dedupes_case_variant_paths(tmp_path: Path) -> None:
    """P2-A 前端侧：outputs 字段里的同一文件大小写变体 → N == 1 且展示首次出现的原始串。"""
    first = "C:\\dc_out\\Shared\\qc_case_out.csv"
    variant = "C:\\DC_OUT\\SHARED\\QC_CASE_OUT.csv"
    root_run = {
        "id": "p2a_root",
        "job_id": "p2a_root_job",
        "job_name": "p2a_root_job",
        "schedule_id": "p2a_fixture",
        "started_at": "2026-01-01 00:00:00",
        "ended_at": "2026-01-01 00:00:01",
        "elapsed_ms": 1000,
        "status": "成功",
        "message": f"作业执行成功：1 个步骤成功，0 个步骤未启用。\n产出文件（2 个）：\n{first}\n{variant}",
        "outputs": [first, variant],
        "steps": [],
    }
    child_run = dict(root_run, id="p2a_child", job_id="p2a_child_job", job_name="p2a_child_job", outputs=[variant], message="")
    merged = _merge_via_frontend([root_run, child_run], "p2a_root_job", tmp_path)
    message = str(merged[0]["message"])
    assert merged[0]["outputs"] == [first], "大小写变体必须归一化成 1 条且保留首次出现的原始串"
    assert message.count("产出文件（1 个）：") == 1, f"N 必须等于实际文件数：{message}"
    assert first in message and variant not in message


@pytest.mark.skipif(NODE_BIN is None, reason="本机没有 node，跳过前端详情的端到端回归")
def test_frontend_jobs_page_renders_outputs_field_once_and_degrades_for_legacy_runs(tmp_path: Path) -> None:
    """作业详情页：有 outputs 字段只渲染一份清单；没有该字段的历史记录原样渲染不报错。"""
    output_file = "C:\\dc_out\\detail_out.csv"
    fresh_run = {
        "id": "p3b_fresh",
        "job_name": "p3b_fresh",
        "started_at": "2026-01-01 00:00:00",
        "ended_at": "2026-01-01 00:00:01",
        "elapsed_ms": 10,
        "status": "失败",
        "message": f"作业执行失败：0 个步骤成功，1 个步骤失败，0 个步骤未启用。最后错误：boom\n产出文件（2 个，失败前已落盘）：\n{output_file}\n{output_file}",
        "outputs": [output_file, output_file],
        "steps": [],
    }
    # 真实历史记录的形状：message 里没有产物块，也没有 outputs 字段
    legacy_run = {
        "id": "p3b_legacy",
        "job_name": "p3b_legacy",
        "started_at": "2025-01-01 00:00:00",
        "ended_at": "2025-01-01 00:00:01",
        "elapsed_ms": 5,
        "status": "成功",
        "message": "作业执行成功：1 个步骤成功，0 个步骤未启用。",
        "steps": [],
    }
    result = _run_node_probe(
        FRONTEND_JOBS_PROBE, PROJECT_DIR / "public" / "jobs.js", [fresh_run, legacy_run], tmp_path
    )
    html = str(result["html"])
    assert html.count(output_file) == 1, f"详情页只能列一次产物路径：{html}"
    assert html.count("产出文件（1 个，失败前已落盘）：") == 1
    assert html.count("产出文件（") == 1
    assert "作业执行成功：1 个步骤成功，0 个步骤未启用。" in html, "历史记录必须原样渲染，不能报错/丢信息"


# ---------------------------------------------------------------------------
# 一致性：三条出口的返回键集合完全一致
# ---------------------------------------------------------------------------


def test_all_exit_paths_return_same_keys() -> None:
    """成功 / 失败 / 跳过三条出口都必须返回 id/jobId/status/message/outputs。"""
    _make_table("p1_export_src")
    success_job = save_job(
        "p1_exit_success",
        [make_step("导出", "export", export_step_config("p1_export_src", "p1_exit_success_out"))],
    )
    failed_job = save_job(
        "p1_exit_failed",
        [make_step("查询", "query", {"targetDbType": "sqlite", "sql": "select * from p1_table_missing"})],
    )
    skipped_job = save_job(
        "p1_exit_skipped",
        [make_step("导出", "export", export_step_config("p1_export_src", "p1_exit_skipped_out"))],
        guard={"type": "date_match", "mode": "dates", "values": ["1999-01-01"]},
    )

    runs = {
        "成功": server.run_saved_job(str(success_job["id"])),
        "失败": server.run_saved_job(str(failed_job["id"])),
        "跳过": server.run_saved_job(str(skipped_job["id"])),
    }
    for status, run in runs.items():
        assert run["status"] == status
        assert set(run) == EXPECTED_RUN_KEYS, f"{status} 出口的键集合不一致：{sorted(run)}"
        assert isinstance(run["outputs"], list)
    assert runs["跳过"]["outputs"] == [], "跳过时不产出任何文件"


# ---------------------------------------------------------------------------
# 边界：服务端默认目录判定
# ---------------------------------------------------------------------------


def test_is_server_default_export_boundary() -> None:
    """落在服务端默认导出目录 → True；用户指定目录 / 子目录 / 平台其他位置 → False。"""
    assert server.is_server_default_export(str(Path(server.EXPORTS) / "probe.xlsx")) is True

    user_folder = Path(_tmp) / "out" / "p1_user_folder"
    user_folder.mkdir(parents=True, exist_ok=True)
    assert server.is_server_default_export(str(user_folder / "probe.xlsx")) is False
    assert server.is_server_default_export(str(Path(_tmp) / "probe.xlsx")) is False
    # 默认目录下的子目录也不是"默认目录本身"（用户显式配了子目录）
    assert server.is_server_default_export(str(Path(server.EXPORTS) / "sub" / "probe.xlsx")) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
