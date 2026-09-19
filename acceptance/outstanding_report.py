# -*- coding: utf-8 -*-
"""验收未闭合项清单（A/B/C 三层全账）。

数据来源（全部可复跑）：
  acceptance/evidence/<日期>/c-layer-replay.json + ui-replay.json
  acceptance/evidence/<日期>/b-layer-replay.json + b-ui-replay.json

归类：
  ❌ 未通过   —— 必须改代码才能判「通过」的条目
  ⚠️ 转人工   —— 不是代码问题，缺外部条件（夹具 / 原生对话框 / 非 Windows / Docker）
  🟡 待决策   —— 结果取决于业主决策（修 or 书面豁免 / 去留）

用法：.venv/Scripts/python.exe acceptance/outstanding_report.py
"""
from __future__ import annotations

import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DATE = "20260919"
EVID = PROJECT / "acceptance" / "evidence" / DATE

# 不在 harness 里、必须靠人判定的项（来源：V6-验收进展-20260919.md）
MANUAL_EXTRA = {
    "M3-033": "目标文件夹原生对话框（自动化点击会阻塞 HTTP 线程）",
    "M3-034": "另存为原生对话框（同上）",
}
STRUCTURAL_UNVERIFIABLE = [
    "打包后 exe 内部的代码逻辑（静态不可验）",
    "scripts/watchdog_server.py 的 --once「服务未运行」分支",
    "VBS 登录自启（需注销重登一次才能观察）",
    "MySQL 在字符集/索引受限时 ALTER 失败的提示文案",
]
DECISIONS = [
    "小票导入 beforeAllSql 硬编码 2026-09（10 月起重跑不清 10 月行）",
    "中秋作业导出 sheetName='123' 是否与独立作业对齐",
    "可疑连接 lcdp_sr / lcdp-sr 指向 198.18.0.1 是否清理",
    "archive/migration-backup-20260917/ 去留",
]

# 本轮已修复并复验的缺陷（保留记录，便于对外说明「验收到此版本已闭合」）
FIXED = [
    "M10-001 / M10-002 备份脚本路径失效 → create_data_backup.py 改为读 DATA_DIR（默认 runtime/data），"
    "与 restore_data_backup.py 口径统一；实测 rc=0、归档含 data/imports.db、manifest 记录实际取数目录",
    "M10-003 恢复回环 → 用真实备份还原，imports.db 字节数一致（3076096 → 3076096）；路径穿越负向对照被拒",
    "M2-016 「字段匹配→按顺序」死控件 → server.py 新增 align_columns_by_position()，"
    "matchBy=order 时按列序号对齐既有目标表列；默认 name 行为逐字节不变",
]

FILES = ["c-layer-replay.json", "ui-replay.json", "b-layer-replay.json", "b-ui-replay.json"]


def load(name: str) -> dict:
    p = EVID / name
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else (data.get("results") or [])
    return {i["id"]: i for i in items}


def main() -> None:
    merged: dict[str, dict] = {}
    for f in FILES:
        for k, v in load(f).items():
            merged.setdefault(k, v)

    real = {k: v for k, v in merged.items() if not k.startswith("_") and not k.startswith("NEW-")}
    fails = {k: v for k, v in real.items() if v.get("result") == "FAIL"}
    manuals = {k: v for k, v in real.items() if v.get("result") == "MANUAL"}
    for k, note in MANUAL_EXTRA.items():
        manuals.setdefault(k, {"detail": note})
    findings = {k: v for k, v in merged.items() if v.get("result") == "FINDING"}

    print("=" * 72)
    print(f"验收未闭合项清单 · 证据目录 evidence/{DATE}/")
    print("=" * 72)
    print(f"已判定条目（含 UT 映射重复）：{len(real)}")

    print(f"\n❌ 未通过（必须改代码）：{len(fails)} 条")
    for k in sorted(fails):
        print(f"   {k}: {fails[k].get('detail', '')[:150]}")

    print(f"\n⚠️ 转人工（缺外部条件）：{len(manuals)} 条")
    for k in sorted(manuals):
        print(f"   {k}: {manuals[k].get('detail', '')[:130]}")

    print(f"\n📌 结构性不可自动验证（须业主书面确认）：{len(STRUCTURAL_UNVERIFIABLE)} 项")
    for s in STRUCTURAL_UNVERIFIABLE:
        print(f"   - {s}")

    print(f"\n🟡 待业主决策：{len(DECISIONS)} 项")
    for s in DECISIONS:
        print(f"   - {s}")

    if findings:
        print(f"\nℹ️ 附加发现（不计入验收条目）：{len(findings)} 条")
        for k in sorted(findings):
            print(f"   {k}: {findings[k].get('detail', '')[:150]}")

    print(f"\n✅ 本轮已修复并复验：{len(FIXED)} 项")
    for s in FIXED:
        print(f"   - {s}")

    # 真正的「必须动手」= 未通过条目里按根因去重
    print("\n" + "-" * 72)
    print(f"合计：未通过 {len(fails)} 条 / 待决策 {len(DECISIONS)} 项 /")
    print(f"      转人工 {len(manuals)} 条 + 结构性 {len(STRUCTURAL_UNVERIFIABLE)} 项（不由我们改代码解决）")


if __name__ == "__main__":
    main()
