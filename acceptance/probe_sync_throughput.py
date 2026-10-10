"""真实数据链路验证：lcdp_fe（源） → dc_sync_dst（目标）。

目的不是实现功能，而是**在写产品代码前，用真实数据把方案里最不确定的几件事量出来**：

  1. 302,938 行的表能不能流式读、内存会不会爆
  2. 自动按源结构建目标表是否可行（含中文列名、text、decimal）
  3. utf8mb4 中文写入是否原样（含 emoji / 生僻字）
  4. 全量覆盖一轮的真实耗时（决定"全量够不够用"这个方案判断是否成立）
  5. 逐行比对是否真的相等（不是只比行数）

只读源库；只写 dc_sync_dst（我方测试库）。
"""

from __future__ import annotations

import os
import sys
import time

import pymysql

HOST, PORT, USER, PWD = "127.0.0.1", 3306, "root", "123456"
SRC_DB = "lcdp_fe"
DST_DB = "dc_sync_dst"


def connect(db: str | None = None):
    return pymysql.connect(
        host=HOST, port=PORT, user=USER, password=PWD, database=db,
        charset="utf8mb4", autocommit=False,
    )


def rss_mb() -> float:
    """进程常驻内存（MB）。

    ⚠️ 绝不要退到 tracemalloc 当回退：tracemalloc 一旦 start() 就会追踪**所有** Python 分配，
    把整体耗时拖慢约 3 倍（实测同一条链路 13.8 s → 43 s），测出来的"内存"也就失去意义。
    这里直接用 Windows 的 GetProcessMemoryInfo。
    """
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    proc = kernel32.GetCurrentProcess()
    counters = PMC()
    counters.cb = ctypes.sizeof(PMC)
    if not ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo(
        ctypes.c_void_p(proc), ctypes.byref(counters), counters.cb
    ):
        return -1.0
    return counters.WorkingSetSize / 1024 / 1024


def quote(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def auto_create_target(src, dst, table: str) -> str:
    """按源表结构在目标库建表（去掉自增与索引，只保留列定义）。

    这就是产品里「目标表不存在则自动按源结构建表」的做法：以 SHOW CREATE TABLE 为准，
    保证类型/长度/字符集/可空性与源一致，不去手工推断。
    """
    cur = src.cursor()
    cur.execute(f"show create table {quote(SRC_DB)}.{quote(table)}")
    ddl = cur.fetchone()[1]
    # 列定义段 = 第一个 "(" 到最后一个「以 ) 开头的行」之间。
    # ⚠️ 不能用 rpartition(")") —— KEY 行里也有 "(" ")"，会把定义截错（实测踩到）。
    start = ddl.index("(") + 1
    end = ddl.rindex("\n)")
    kept = []
    for line in ddl[start:end].splitlines():
        stripped = line.strip()
        if not stripped:                      # ⚠️ 首行是空行，不跳过会在 SQL 里留个裸逗号
            continue
        low = stripped.lower()
        if low.startswith(("primary key", "unique key", "key ", "index ", "constraint", "fulltext")):
            continue
        kept.append(stripped.rstrip(",").strip())
    columns = ",\n  ".join(kept)
    cur.execute(f"drop table if exists {quote(DST_DB)}.{quote(table)}")
    sql = f"create table {quote(DST_DB)}.{quote(table)} (\n  {columns}\n) engine=InnoDB default charset=utf8mb4"
    cur.execute(sql)
    dst.commit()
    return ddl


def main() -> int:
    table = sys.argv[1] if len(sys.argv) > 1 else "会员小票表"
    batch = int(sys.argv[2]) if len(sys.argv) > 2 else 5000

    print(f"源 {SRC_DB}.{table}  →  目标 {DST_DB}.{table}   批次 {batch}")
    print(f"起始内存 {rss_mb():.0f} MB\n")

    src = connect(SRC_DB)
    dst = connect(DST_DB)

    print("— 1) 自动按源结构建目标表 —")
    t0 = time.time()
    ddl = auto_create_target(src, dst, table)
    print(f"   建表完成 {time.time() - t0:.2f} s  （DDL {len(ddl)} 字符）")

    cur = src.cursor()
    cur.execute(
        "select column_name, column_type, is_nullable from information_schema.columns "
        "where table_schema=%s and table_name=%s order by ordinal_position",
        (SRC_DB, table),
    )
    cols_meta = cur.fetchall()
    columns = [c[0] for c in cols_meta]
    print(f"   列数 {len(columns)}：{', '.join(columns[:6])}{' …' if len(columns) > 6 else ''}")

    cur.execute(f"select count(*) from {quote(SRC_DB)}.{quote(table)}")
    total = cur.fetchone()[0]
    print(f"   源表行数 {total:,}\n")

    print("— 2) 流式读取 + 批量写入（全量覆盖）—")
    t0 = time.time()
    read_ms = write_ms = 0.0
    written = 0
    col_sql = ", ".join(quote(c) for c in columns)
    ph = ", ".join(["%s"] * len(columns))
    insert_sql = f"insert into {quote(DST_DB)}.{quote(table)} ({col_sql}) values ({ph})"

    # 用 SSCursor 流式读：一次不把 30 万行全拉进内存
    stream = src.cursor(pymysql.cursors.SSCursor)
    stream.execute(f"select {col_sql} from {quote(SRC_DB)}.{quote(table)}")

    peak = rss_mb()
    while True:
        t1 = time.time()
        rows = stream.fetchmany(batch)
        read_ms += (time.time() - t1) * 1000
        if not rows:
            break
        t2 = time.time()
        with dst.cursor() as dcur:
            dcur.executemany(insert_sql, rows)
        dst.commit()
        write_ms += (time.time() - t2) * 1000
        written += len(rows)
        peak = max(peak, rss_mb())
        if written % (batch * 10) == 0 or written == total:
            print(f"   {written:>9,} / {total:,} 行   内存 {rss_mb():.0f} MB（峰值 {peak:.0f}）")

    elapsed = time.time() - t0
    print(f"\n   写入 {written:,} 行")
    print(f"   总耗时 {elapsed:.2f} s （读取 {read_ms/1000:.2f} s / 写入 {write_ms/1000:.2f} s）")
    print(f"   吞吐 {written/elapsed:,.0f} 行/秒")
    print(f"   内存 峰值 {peak:.0f} MB\n")

    print("— 3) 逐行比对（不是只比行数）—")
    with dst.cursor() as dcur:
        dcur.execute(f"select count(*) from {quote(DST_DB)}.{quote(table)}")
        dst_count = dcur.fetchone()[0]
    print(f"   源 {total:,} 行 / 目标 {dst_count:,} 行 → {'一致' if total == dst_count else '❌ 不一致'}")

    # 用指纹比对：把每行拼起来取 md5，两端各算一次（避免拉回全量数据到内存）
    def fingerprint(conn, db):
        cur2 = conn.cursor()
        expr = ", ".join(f"ifnull(cast({quote(c)} as char), '␀')" for c in columns)
        cur2.execute(f"select md5(group_concat(concat_ws('|', {expr}) order by {col_sql})) from {quote(db)}.{quote(table)}")
        return cur2.fetchone()[0]

    t0 = time.time()
    f_src, f_dst = fingerprint(src, SRC_DB), fingerprint(dst, DST_DB)
    print(f"   源  指纹 {f_src}")
    print(f"   目标指纹 {f_dst}")
    print(f"   逐行内容{'完全一致 ✅' if f_src == f_dst else '存在差异 ❌'}  （{time.time() - t0:.1f} s）")

    print("\n— 4) 中文与特殊字符抽查 —")
    with dst.cursor() as dcur:
        dcur.execute(
            f"select {quote(columns[0])}, count(*) from {quote(DST_DB)}.{quote(table)} "
            f"group by {quote(columns[0])} order by count(*) desc limit 3"
        )
        for row in dcur.fetchall():
            print(f"   {row[0]!r}  × {row[1]:,}")

    src.close()
    dst.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
