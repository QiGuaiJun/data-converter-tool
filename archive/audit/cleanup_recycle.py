# -*- coding: utf-8 -*-
"""把 data-converter-tool 的构建产物 / 缓存 / 旧备份 / 日志送进 Windows 回收站。

安全约定：
- 只处理显式列出的相对路径（白名单，不做通配扫描）
- 一律 FOF_ALLOWUNDO（走回收站，可恢复）
- 删前打印清单与大小，删后复核，输出机读结果
- 绝不触碰：imports.db / .secret_key / .storage-write-test / linked_sources/ / task_sources/
"""
import ctypes
import os
import sys
from ctypes import wintypes

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT = r"C:\Users\Administrator\WorkBuddy\2026-08-27-16-17-35\data-converter-tool"

TARGETS = [
    # A 类：可再生产物
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    "scripts/__pycache__",
    # C 类：旧数据库备份（保留今日最新一份）
    "data/imports.db.bak_20260912_before_restore",
    "data/imports.db.bak_20260912_restore",
    "data/imports.db.bak_before_restore",
    "data/imports.db.bak_cleanup",
    "data/imports.db.bak_orphan_cleanup",
    "data/imports.db.bak_pathfix",
    # B 类：日志与调试残留
    "data/server.log",
    "data/server.session.log",
    "data/server_test.log",
    "data/watchdog.log",
    "data/proc_chain.txt",
    "data/wmi_launch.txt",
    "data/app.db",
]

# 显式保护清单（即便上面写错也不允许命中）
PROTECTED = {
    "data/imports.db",
    "data/.secret_key",
    "data/.storage-write-test",
    "data/imports.db.bak_20260916_before_qa5cleanup",
    "data/linked_sources",
    "data/task_sources",
    "server.py",
}


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
shell32.SHFileOperationW.restype = ctypes.c_int

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


def dir_size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def recycle_one(abs_path):
    joined = abs_path + "\0\0"
    buf = ctypes.create_unicode_buffer(joined)
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, ctypes.c_void_p)
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None
    rc = shell32.SHFileOperationW(ctypes.byref(op))
    return rc, bool(op.fAnyOperationsAborted)


def human(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return "%.1f%s" % (n, unit)
        n /= 1024.0
    return "%.1fT" % n


def main():
    print("=" * 72)
    print("清理对象清单（共 %d 项）" % len(TARGETS))
    print("=" * 72)
    present = []
    for rel in TARGETS:
        if rel in PROTECTED:
            print("  !! 拒绝：%s 在保护清单中" % rel)
            continue
        abs_p = os.path.join(PROJECT, rel.replace("/", os.sep))
        if os.path.exists(abs_p):
            kind = "目录" if os.path.isdir(abs_p) else "文件"
            size = dir_size(abs_p)
            print("  [%-2s] %-52s %10s" % (kind, rel, human(size)))
            present.append(rel)
        else:
            print("  [跳过] %-52s (不存在)" % rel)

    print()
    print("=" * 72)
    print("送回收站 ...")
    print("=" * 72)
    ok, fail = 0, 0
    for rel in present:
        abs_p = os.path.join(PROJECT, rel.replace("/", os.sep))
        rc, aborted = recycle_one(abs_p)
        if rc == 0 and not aborted:
            still = os.path.exists(abs_p)
            if still:
                print("  ?? %-52s rc=0 但仍存在" % rel)
                fail += 1
            else:
                print("  OK %-52s 已入回收站" % rel)
                ok += 1
        else:
            print("  !! %-52s rc=%d aborted=%s" % (rel, rc, aborted))
            fail += 1

    print()
    print("=" * 72)
    print("复核：保护清单必须全部仍在")
    print("=" * 72)
    for rel in sorted(PROTECTED):
        abs_p = os.path.join(PROJECT, rel.replace("/", os.sep))
        exists = os.path.exists(abs_p)
        print("  %-52s %s" % (rel, "存在 OK" if exists else "!! 丢失"))

    print()
    print("=" * 72)
    print("结果：入回收站 %d 项 / 失败 %d 项" % (ok, fail))
    print("=" * 72)

    # 剩余 data/ 一览
    print()
    print("data/ 目录剩余内容：")
    d = os.path.join(PROJECT, "data")
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            print("  %-52s %10s" % (name, human(dir_size(p))))


if __name__ == "__main__":
    main()
