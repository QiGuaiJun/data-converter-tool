"""把历史运行日志重写成新格式（去重 + 剥嵌套 + 系统错误翻译成人话）。

背景（2026-10-08）：业主反馈"任务的运行日志特别乱"。乱在三点：
  1. 父子套娃：父作业把子作业整段 message 内嵌进 last_error，出现
     「子作业执行失败：作业执行失败：…最后错误：[Errno 13] Permission denied: …」；
  2. 段落重复：「（本次触发执行…）」被子作业带进来一次、父作业又追加一次；
  3. 英文报错直出，看不出真实原因（其实是那个 xlsx 正开在 Excel 里）。

`server.py` 已经改好了**新产生**的日志；这个脚本负责把**已经存库**的旧日志按同一套规则
重写一遍，否则业主打开历史记录看到的还是老样子。

只改展示文本（`_job_runs.message` 与 `_job_run_steps.message`），
**不碰任何判定字段**（状态、耗时、产物清单、步骤顺序都不动）。
信息不会丢：最内层的真实错误一定保留，产物清单与括号段落原样保留（只去重复）。

用法：
    python scripts/normalize_run_logs.py            # 干跑，打印改写前后对照
    python scripts/normalize_run_logs.py --apply    # 备份后写回
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _env import load_env_file  # noqa: E402

# ⚠️ 必须在 import server 之前加载 .env：云端 DATA_DIR 在应用目录之外，
# 不加载会连到另一个（空的）库，表现为"日志一条都没有"。
_ENV_USED = load_env_file()

# 这些函数就在 server.py 里，直接复用 —— 保证"历史重写"与"新日志"走的是同一套规则，
# 不会出现两套文案风格。
from server import compact_failure_reason, dedupe_log_paragraphs, translate_system_error  # noqa: E402

# 与 server 用同一个真值来源，别再自己拼路径（否则又会连错库）
DB_PATH = Path(str(__import__("server").DB_PATH))


_DICT_REPR_RE = re.compile(r"\{([^{}]*'[^']+'\s*:\s*\d+[^{}]*)\}")


def humanize_dict_repr(text: str) -> str:
    """把漏到日志里的 Python 字典字面量排成人话。

    历史步骤行里写着 `数据库校验行数：{'云购商城销售数据表': 104496}` ——
    把 repr 直接摆给业务用户看太糙（2026-10-08）。新版代码已不再产生这种文本，
    这里只负责把旧记录洗一遍。
    """
    def replace(match: "re.Match[str]") -> str:
        pairs = re.findall(r"'([^']+)':\s*(\d+)", match.group(1))
        return "、".join(f"{name} {count} 行" for name, count in pairs) if pairs else match.group(0)

    return _DICT_REPR_RE.sub(replace, str(text or ""))


_DOUBLED_PREFIX_RE = re.compile(
    r"((?:子作业|作业)(?:「[^」]*」)?(?:执行成功|执行失败|已跳过)：)作业(?:执行成功|执行失败|已跳过)："
)


def collapse_doubled_prefix(text: str) -> str:
    """把「子作业执行成功：作业执行成功：…」这种叠了两层的标签收成一层。

    成功分支原来直接拼 `子作业执行成功：` + 子作业首行，而首行自带 `作业执行成功：`，
    于是叠了一层；失败分支同理。属于展示层冗余，去掉不丢信息。
    """
    out = str(text or "")
    for _ in range(4):  # 嵌套层数有限
        collapsed = _DOUBLED_PREFIX_RE.sub(r"\1", out)
        if collapsed == out:
            break
        out = collapsed
    return out


# ---------------------------------------------------------------- 旧格式 → 新格式
# 2026-10-08 之前日志是「结论句 + （括号段落） + 分号长句」的写法；现在统一成
# 「标签：值」一行一个维度。下面只认**已知的固定句式**，认不出一律原样保留（绝不丢信息）。
_LEGACY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^作业执行成功：(\d+) 个步骤成功，(\d+) 个步骤未启用。$"),
     "执行结果：成功 · {0} 个步骤完成 · {1} 个未启用"),
    (re.compile(r"^作业执行失败：(\d+) 个步骤成功，(\d+) 个步骤失败，(\d+) 个步骤未启用。$"),
     "执行结果：失败 · {0} 个成功 · {1} 个失败 · {2} 个未启用"),
    (re.compile(r"^（本次触发执行：源文件有更新 — (.*)）$"), "触发执行：源文件有更新 —— {0}"),
    (re.compile(r"^（以下步骤本轮未重跑：源文件无更新 — (.*)）$"), "本轮未重跑：{0}（源文件无更新）"),
    (re.compile(r"^（警告：以下源文件更新条件无法评估，本次已按「有更新」执行：(.*)）$"),
     "更新条件无法评估：{0}（本次已按「有更新」执行）"),
    (re.compile(r"^（警告：有文件写入服务端默认目录，说明对应导出步骤未配置目标文件夹）$"),
     "警告：有文件写进服务端默认目录，说明对应导出步骤没配目标文件夹"),
    (re.compile(r"^（(预检判定无新增.*。)）$"), "备注：{0}"),
    (re.compile(r"^子作业执行成功：作业执行成功：(\d+) 个步骤成功，(\d+) 个步骤未启用。$"),
     "子作业结果：成功 —— {0} 个步骤完成 · {1} 个未启用"),
    (re.compile(r"^子作业执行失败：(\d+) 个步骤成功，(\d+) 个步骤失败，(\d+) 个步骤未启用。$"),
     "子作业结果：失败 —— {0} 个成功 · {1} 个失败 · {2} 个未启用"),
)

_LEGACY_IMPORT_RE = re.compile(
    r"^导入完成；来源：(?P<path>.*?)；文件：(?P<files>.*?)；目标表：(?P<tables>.*?)；"
    r"读取 (?P<read>[\d,]+) 行，成功写入 (?P<written>[\d,]+) 行，"
    r"更新 (?P<updated>[\d,]+) 行，跳过 (?P<skipped>[\d,]+) 行；"
    r"数据库校验行数：(?P<verified>.*?)；(?P<sql>.*?)。?$"
)
_LEGACY_EXPORT_RE = re.compile(r"^导出 (?P<count>\d+) 个文件（(?P<rows>[\d,]+) 行）：(?P<paths>.*)$")
_LEGACY_EXPORT_EMPTY_RE = re.compile(r"^导出 0 个文件，(?P<rows>[\d,]+) 行。$")


def _convert_legacy_line(line: str) -> str | None:
    """把一行旧格式文本转成新格式；不是已知句式就返回 None（由调用方原样保留）。"""
    for pattern, template in _LEGACY_PATTERNS:
        match = pattern.match(line)
        if match:
            return template.format(*match.groups())
    match = _LEGACY_IMPORT_RE.match(line)
    if match:
        groups = match.groupdict()
        return "\n".join([
            f"目标表：{groups['tables']}",
            f"来源：{groups['path']}",
            f"行数：读取 {groups['read']} · 写入 {groups['written']}"
            f" · 更新 {groups['updated']} · 跳过 {groups['skipped']}",
            f"数据库复核：{groups['verified']}",
            f"前置 SQL：{groups['sql']}",
        ])
    match = _LEGACY_EXPORT_RE.match(line)
    if match:
        paths = [item for item in match.group("paths").split("；") if item.strip()]
        return "\n".join([f"导出文件：{match.group('count')} 个 · 共 {match.group('rows')} 行", *paths])
    match = _LEGACY_EXPORT_EMPTY_RE.match(line)
    if match:
        return f"导出文件：0 个（共 {match.group('rows')} 行）"
    return None


def convert_legacy_message(text: str) -> str:
    """逐行把旧格式改写成新格式；认不出的行原样保留。"""
    out: list[str] = []
    for raw in str(text or "").splitlines():
        converted = _convert_legacy_line(raw.strip())
        out.append(converted if converted is not None else raw)
    return "\n".join(out)


def normalize_message(text: str) -> str:
    """把一条运行/步骤日志重写成新格式。"""
    original = str(text or "")
    if not original.strip():
        return original
    deduped = collapse_doubled_prefix(dedupe_log_paragraphs(convert_legacy_message(original)))
    if "最后错误：" not in deduped:
        # 没有嵌套的（成功记录、步骤行）：把裸的英文系统错误翻成人话 +
        # 把漏进来的 Python 字典字面量排成人话。两者对正常文本都是空操作。
        return humanize_dict_repr(translate_system_error(deduped))

    lines = [line for line in deduped.splitlines() if line.strip()]
    conclusion = ""
    outputs: list[str] = []
    paragraphs: list[str] = []
    extras: list[str] = []
    in_outputs = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("产出文件"):
            in_outputs = True
            outputs.append(line)
            continue
        if stripped.startswith("（"):
            in_outputs = False
            paragraphs.append(line)
            continue
        if in_outputs:
            outputs.append(line)
            continue
        if not conclusion and "最后错误：" in stripped:
            # ⚠️ 结论句与嵌套错误**在同一行**上（「作业执行失败：N 个步骤…最后错误：子作业执行失败：…」），
            # 这里必须从「最后错误：」处截断，只留前半句统计；否则第一行还是原来那坨糊的。
            conclusion = stripped.split("最后错误：", 1)[0].strip()
            continue
        if not conclusion and stripped.startswith(("作业执行", "子作业", "本次任务执行")):
            conclusion = stripped
            continue
        # 认不出来的行不静默丢掉（可能是原始错误的续行），放到最后原样保留
        extras.append(line)
    # 失败原因 = 整条日志里最内层那句话（compact_failure_reason 负责剥壳 + 翻译）
    reason = compact_failure_reason(deduped)
    parts: list[str] = []
    if conclusion:
        parts.append(conclusion)
    parts.append(f"失败原因：{reason}")
    if outputs:
        parts.extend(outputs)
    parts.extend(paragraphs)
    parts.extend(extras)
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正写回（默认只干跑）")
    parser.add_argument("--limit", type=int, default=3, help="干跑时打印几条前后对照（默认 3）")
    args = parser.parse_args()

    if not DB_PATH.is_file():
        print(f"找不到库文件：{DB_PATH}")
        return 2
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    changes: list[tuple[str, str, str, str]] = []  # (表, id, 旧, 新)
    for table in ("_job_runs", "_job_run_steps"):
        for row in conn.execute(f"select id, message from {table} where message is not null and message <> ''"):
            new = normalize_message(str(row["message"]))
            if new != str(row["message"]):
                changes.append((table, str(row["id"]), str(row["message"]), new))

    print(f"库文件：{DB_PATH}（{DB_PATH.stat().st_size} 字节）")
    print(f"环境文件：{_ENV_USED or '（未找到 .env，按默认路径取库）'}")
    print(f"需要改写的记录：{len(changes)} 条"
          f"（{sum(1 for c in changes if c[0] == '_job_runs')} 条运行 / "
          f"{sum(1 for c in changes if c[0] == '_job_run_steps')} 条步骤）")
    print()
    for table, _id, old, new in changes[: args.limit]:
        print(f"===== {table} =====")
        print("--- 改写前 ---")
        print(old)
        print("--- 改写后 ---")
        print(new)
        print()

    if not args.apply:
        print("（干跑结束，未做任何修改。加 --apply 才会写回）")
        return 0
    if not changes:
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = DB_PATH.with_name(f"imports.db.bak-logfix-{stamp}")
    shutil.copy2(DB_PATH, backup)
    print(f"已备份到：{backup}")

    with conn:  # 一个事务，失败全回滚
        for table, row_id, _old, new in changes:
            conn.execute(f"update {table} set message = ? where id = ?", (new, row_id))
    conn.execute("vacuum")
    conn.close()
    print(f"已改写 {len(changes)} 条记录。")
    print(f"如需回退：copy \"{backup}\" \"{DB_PATH}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
