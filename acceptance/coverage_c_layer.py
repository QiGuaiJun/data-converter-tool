# -*- coding: utf-8 -*-
"""C 层覆盖率核算：V5 定义的 C 层 119 条 vs 本次复跑实际覆盖。

- 全量条目宇宙：从 archive/acceptance-plan-final-v5.md 的条目表解析 `| Mx-nnn |`
- C 层 = M0 全部 + M1 全部 + M2（除 028/058）+ M3（除 A 层 12 条）
- 实测证据：c-layer-replay.json（harness）+ ui-replay.json（Playwright）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PLAN = PROJECT / "archive" / "acceptance-plan-final-v5.md"
EVID = PROJECT / "acceptance" / "evidence" / "20260919"

A_LAYER_M3 = {"003", "004", "006", "016", "020", "021", "022", "028", "029", "033", "034", "036"}
M2_EXCLUDED = {"028", "058"}


def full_universe() -> list[str]:
    ids = []
    seen = set()
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(M\d+-\d+)\s*\|", line)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            ids.append(m.group(1))
    return ids


def c_layer(universe: list[str]) -> list[str]:
    out = []
    for rid in universe:
        mod, num = rid.split("-")
        if mod in {"M0", "M1"}:
            out.append(rid)
        elif mod == "M2" and num not in M2_EXCLUDED:
            out.append(rid)
        elif mod == "M3" and num not in A_LAYER_M3:
            out.append(rid)
    return out


def load_ids(path: Path, key: str):
    if not path.exists():
        return {}, False
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else (data.get(key) or [])
    return {i["id"]: i for i in items}, True


def main() -> None:
    universe = full_universe()
    c = c_layer(universe)
    harness, ok_h = load_ids(EVID / "c-layer-replay.json", "results")
    ui, ok_u = load_ids(EVID / "ui-replay.json", "results")

    print(f"全量条目宇宙（M0~M11 计划条目）：{len(universe)}")
    for mod in sorted({r.split('-')[0] for r in universe}):
        n = len([r for r in universe if r.startswith(mod + "-")])
        print(f"  {mod}: {n}")
    print(f"\nV5 定义的 C 层：{len(c)} 条")
    print(f"  harness 实测：{len(harness)} 条（存在={ok_h}）")
    print(f"  UI  走查实测：{len(ui)} 条（存在={ok_u}）")

    union = dict(ui)
    union.update(harness)

    # M0-010 在复跑中被拆成 M0-010a（启动后无密钥文件）+ M0-010b（加密调用后 44 字符）两条实测，
    # 属同一验收项的完整拆分覆盖，此处显式折算，避免误报为「未覆盖」。
    aliases = {"M0-010": ["M0-010a", "M0-010b"]}
    for base, parts in aliases.items():
        if base not in union and all(p in union for p in parts):
            union[base] = {
                "id": base,
                "result": "PASS" if all(union[p]["result"] == "PASS" for p in parts) else "FAIL",
                "detail": "由 " + " / ".join(f"{p}({union[p]['result']})" for p in parts) + " 折算覆盖",
            }

    covered = [r for r in c if r in union]
    missing = [r for r in c if r not in union]
    extra = [r for r in union if r not in c]

    def tally(ids):
        res = {"PASS": 0, "FAIL": 0, "MANUAL": 0}
        for r in ids:
            res[union[r].get("result", "?")] = res.get(union[r].get("result", "?"), 0) + 1
        return res

    t = tally(covered)
    print(f"\nC 层覆盖：{len(covered)}/{len(c)} = {len(covered)/len(c)*100:.1f}%")
    print(f"  PASS {t['PASS']} / FAIL {t['FAIL']} / MANUAL {t['MANUAL']}")
    print(f"\n未覆盖（{len(missing)}）：")
    print("  " + (", ".join(missing) if missing else "无"))
    print(f"\n不在 C 层但被本次实测覆盖（{len(extra)}，属 A/B 层或跨层）：")
    print("  " + (", ".join(sorted(extra)) if extra else "无"))
    print("\nMANUAL 明细：")
    for r in covered:
        if union[r].get("result") == "MANUAL":
            print(f"  {r}: {union[r].get('detail', '')[:110]}")
    print("\nFAIL 明细：")
    fails = [r for r in covered if union[r].get("result") == "FAIL"]
    print("  " + (", ".join(fails) if fails else "无"))


if __name__ == "__main__":
    main()
