# -*- coding: utf-8 -*-
"""最小用例：验证把文件送 Windows 回收站的正确调用方式。只针对一个 0 字节废弃文件。"""
import ctypes
import os
import sys
from ctypes import wintypes

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TARGET = r"C:\Users\Administrator\WorkBuddy\2026-08-27-16-17-35\data-converter-tool\data\app.db"
print("目标存在:", os.path.exists(TARGET), "大小:", os.path.getsize(TARGET) if os.path.exists(TARGET) else "-")

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
hr = ole32.CoInitialize(None)
print("CoInitialize hr =", hr)


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


sh = ctypes.WinDLL("shell32", use_last_error=True)
sh.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
sh.SHFileOperationW.restype = ctypes.c_int

FO_DELETE = 3
FLAGS = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI


def attempt(label, use_buffer=True):
    if not os.path.exists(TARGET):
        print("%s: 目标已不存在（前一个方案成功了？）" % label)
        return
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    if use_buffer:
        buf = ctypes.create_unicode_buffer(TARGET + "\0\0")
        op.pFrom = ctypes.cast(buf, ctypes.c_void_p)
    else:
        op.pFrom = ctypes.cast(ctypes.c_wchar_p(TARGET + "\0\0"), ctypes.c_void_p)
    op.pTo = None
    op.fFlags = FLAGS
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None
    ctypes.set_last_error(0)
    rc = sh.SHFileOperationW(ctypes.byref(op))
    err = ctypes.get_last_error()
    print("%s: rc=%d GetLastError=%d exists=%s aborted=%s" % (label, rc, err, os.path.exists(TARGET), bool(op.fAnyOperationsAborted)))


attempt("方案A 缓冲+CoInitialize", use_buffer=True)

# 方案B：换 IFileOperation COM（较现代，不依赖 shell 拖放上下文）
if os.path.exists(TARGET):
    try:
        ole32.CoInitializeEx(None, 2)  # APARTMENTTHREADED
    except Exception as e:
        print("CoInitializeEx 异常:", e)

    CLSID_FileOperation = "{3AD05575-8857-4850-9277-11B85BDB8E09}"
    IID_IFileOperation = "{947AAB5F-0A5C-4C13-B4D6-4BF7836FC9F8}"
    from ctypes import POINTER, byref, c_void_p, c_ulong, c_wchar_p

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        def __init__(self, s):
            super().__init__()
            ole32.CLSIDFromString(c_wchar_p(s), byref(self))

    try:
        ole32.CoCreateInstance.argtypes = [POINTER(GUID), c_void_p, ctypes.c_ulong, POINTER(GUID), POINTER(c_void_p)]
        pfo = c_void_p()
        g_clsid = GUID(CLSID_FileOperation)
        g_iid = GUID(IID_IFileOperation)
        hr = ole32.CoCreateInstance(byref(g_clsid), None, 1, byref(g_iid), byref(pfo))
        print("CoCreateInstance(FileOperation) hr =", hr, "ptr =", pfo)
        if hr == 0 and pfo:
            # vtable: SetOperationFlags(3), ... ; 用 SHCreateItemFromParsingName 太复杂
            # 这里只用最简路径：IFileOperation::DeleteItem 需要 IShellItem
            print("IFileOperation 已创建，但需 IShellItem，改走其它方案")
    except Exception as e:
        print("IFileOperation 尝试异常:", type(e).__name__, e)

print()
print("结论：目标是否仍存在 =", os.path.exists(TARGET))
