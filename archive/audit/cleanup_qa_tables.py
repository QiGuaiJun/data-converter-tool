# -*- coding: utf-8 -*-
"""清理 imports.db 中历史测试遗留的 qa_* 表。先备份，再删，后复核。"""
import ctypes
import ctypes.wintypes
import os
import sqlite3
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT = r"C:\Users\Administrator\WorkBuddy\2026-08-27-16-17-35\data-converter-tool"
DB = os.path.join(PROJECT, "data", "imports.db")
NEW_BAK = os.path.join(PROJECT, "data", "imports.db.bak_20260916_before_qa_tables_drop")
OLD_BAK = os.path.join(PROJECT, "data", "imports.db.bak_20260916_before_qa5cleanup")

print("=" * 70)
print("1) 备份（VACUUM INTO，事务安全快照）")
print("=" * 70)
con = sqlite3.connect(DB)
cur = con.cursor()
if os.path.exists(NEW_BAK):
    os.remove(NEW_BAK)
cur.execute("VACUUM INTO ?", (NEW_BAK,))
print("  备份路径: %s" % NEW_BAK)
print("  备份大小: %d bytes" % os.path.getsize(NEW_BAK))

# 校验备份可打开且表一致
chk = sqlite3.connect("file:%s?mode=ro" % NEW_BAK.replace("\\", "/"), uri=True)
b_tabs = [r[0] for r in chk.execute("select name from sqlite_master where type='table' order by name")]
chk.close()
print("  备份内表数: %d" % len(b_tabs))

print()
print("=" * 70)
print("2) 待删的 qa_* 表")
print("=" * 70)
qa = [r[0] for r in cur.execute(
    "select name from sqlite_master where type='table' and name like 'qa\\_%' escape '\\' order by name")]
if not qa:
    print("  (无 qa_* 表，无需清理)")
for t in qa:
    n = cur.execute('select count(*) from "%s"' % t).fetchone()[0]
    cols = [r[1] for r in cur.execute('pragma table_info("%s")' % t)]
    print("  %-18s %4d 行  列: %s" % (t, n, cols))

print()
print("=" * 70)
print("3) 执行 DROP")
print("=" * 70)
for t in qa:
    cur.execute('drop table if exists "%s"' % t)
    print("  已 drop: %s" % t)
con.commit()

print()
print("=" * 70)
print("4) 复核")
print("=" * 70)
left = [r[0] for r in cur.execute(
    "select name from sqlite_master where type='table' and name like 'qa\\_%' escape '\\'")]
print("  剩余 qa_* 表: %s" % (left if left else "无 OK"))
alltabs = [r[0] for r in cur.execute("select name from sqlite_master where type='table' order by name")]
print("  当前全部表 (%d):" % len(alltabs))
for t in alltabs:
    n = cur.execute('select count(*) from "%s"' % t).fetchone()[0]
    print("     %-30s %6d 行" % (t, n))

# 关键业务表行数（应保持不变）
print()
print("  关键表行数核对：")
for t, expect in [("_db_connections", 3), ("_import_logs", 4), ("_jobs", 8), ("_schedules", 2), ("_saved_queries", 1)]:
    n = cur.execute('select count(*) from "%s"' % t).fetchone()[0]
    print("     %-22s %d 行 %s" % (t, n, "OK" if n == expect else "!! 与预期 %d 不符" % expect))
con.close()

print()
print("=" * 70)
print("5) 回收旧备份（保留最新一份）")
print("=" * 70)


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.wintypes.HWND), ("wFunc", ctypes.wintypes.UINT),
        ("pFrom", ctypes.c_void_p), ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.wintypes.WORD), ("fAnyOperationsAborted", ctypes.wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.wintypes.LPCWSTR),
    ]


def recycle(path):
    sh = ctypes.WinDLL("shell32", use_last_error=True)
    sh.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    buf = ctypes.create_unicode_buffer(path + "\0\0")
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = 3
    op.pFrom = ctypes.cast(buf, ctypes.c_void_p)
    op.pTo = None
    op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None
    sh.SHFileOperationW(ctypes.byref(op))
    return not os.path.exists(path)


if os.path.exists(OLD_BAK):
    gone = recycle(OLD_BAK)
    print("  %s -> %s" % (os.path.basename(OLD_BAK), "已移除" if gone else "!! 仍存在"))
else:
    print("  (旧备份不存在)")

print()
print("=" * 70)
print("6) data/ 最终状态")
print("=" * 70)
d = os.path.join(PROJECT, "data")
for name in sorted(os.listdir(d)):
    p = os.path.join(d, name)
    sz = os.path.getsize(p) if os.path.isfile(p) else sum(
        os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs)
    print("  %-52s %10d" % (name, sz))
