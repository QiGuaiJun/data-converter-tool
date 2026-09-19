# -*- coding: utf-8 -*-
"""B 层覆盖率核算：V5 定义的 B 层 75 条 vs 本次复跑实际覆盖。

- 全量条目宇宙：从 archive/acceptance-plan-final-v5.md 的条目表解析 `| Mx-nnn |`
- B 层 = M4（除 005）+ M6（除 025~028）+ M7（全部）+ M8（全部）+ M9（除 003/005）+ M10（除 005/007）
- 实测证据：b-layer-replay.json（Python harness）+ b-ui-replay.json（Playwright）
"""
from __future__ import annotations

import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PLAN = PROJECT / "archive" / "acceptance-plan-final-v5.md"
EVID = PROJECT / "acceptance" / "evidence" / "20260919"

A_LAYER = {"M4-005", "M6-025", "M6-026", "M6-027", "M6-028", "M9-003", "M9-005", "M10-005", "M10-007"}
B_MODULES = {"M4", "M6", "M7", "M8", "M9", "M10"}


def full_universe() -> list[str]:
    import re

    ids, seen = [], set()
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(M\d+-\d+)\s*\|", line)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            ids.append(m.group(1))
    return ids


def b_layer(universe: list[str]) -> list[str]:
    return [r for r in universe if r.split("-")[0] in B_MODULES and r not in A_LAYER]


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else (data.get("results") or [])
    return {i["id"]: i for i in items}


def main() -> None:
    universe = full_universe()
    b = b_layer(universe)
    harness = load(EVID / "b-layer-replay.json")
    ui = load(EVID / "b-ui-replay.json")

    union = dict(ui)
    union.update(harness)          # harness 优先（Python 侧证据更完整）
    extra_findings = [k for k in union if k.startswith("NEW-")]

    covered = [r for r in b if r in union]
    missing = [r for r in b if r not in union]

    tally = {"PASS": 0, "FAIL": 0, "MANUAL": 0, "FINDING": 0}
    for r in covered:
        k = union[r].get("result", "?")
        tally[k] = tally.get(k, 0) + 1

    print(f"V5 定义的 B 层：{len(b)} 条")
    for mod in sorted(B_MODULES):
        n_all = len([r for r in b if r.startswith(mod + "-")])
        n_cov = len([r for r in covered if r.startswith(mod + "-")])
        print(f"  {mod}: {n_cov}/{n_all}")
    print(f"\nharness(b-layer-replay.json) 条目：{len(harness)}")
    print(f"UI(b-ui-replay.json) 条目：{len(ui)}")
    print(f"\nB 层覆盖：{len(covered)}/{len(b)} = {len(covered) / len(b) * 100:.1f}%")
    print(f"  PASS {tally['PASS']} / FAIL {tally['FAIL']} / MANUAL {tally['MANUAL']}")
    print(f"\n未覆盖（{len(missing)}）：{'无' if not missing else ', '.join(missing)}")
    print(f"\n附加发现（不计入 75 条）：{extra_findings or '无'}")
    print("\nFAIL 明细：")
    for r in covered:
        if union[r].get("result") == "FAIL":
            print(f"  {r}: {union[r].get('detail', '')[:160]}")
    print("\nMANUAL 明细：")
    for r in covered:
        if union[r].get("result") == "MANUAL":
            print(f"  {r}: {union[r].get('detail', '')[:160]}")


if __name__ == "__main__":
    main()
