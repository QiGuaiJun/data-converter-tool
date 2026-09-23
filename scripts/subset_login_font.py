#!/usr/bin/env python
"""把登录页页脚用的艺术字字体做「子集化」，只保留页面上真正出现的那几个字。

为什么需要它：
    中文书法字体动辄 9~10MB（字小魂沧浪行楷约 9.77MB / 9496 字），
    而登录页页脚只用到「让工作简单起来」7 个字。
    子集化后只有几 KB，放进仓库毫无负担，同事和云端页面也能**看到同一款字体**
    （浏览器只能用访问者本机装的字体，不嵌入就等于只有装了字体的人能看到）。

用法：
    ./.venv/Scripts/python.exe scripts/subset_login_font.py <字体文件.ttf> [--text 文字] [--out 输出路径]

默认：
    --text 取 public/login.config.js 里 vision 的值（自动读取，改文案后重跑即可）
    --out  public/fonts/zixiaohun-canglang-xingkai.woff2

依赖：pip install fonttools brotli

⚠️ 授权提醒：字魂 / 字小魂系列为收费字体，商业使用需先取得授权。
   本脚本只做格式转换，不改变字体的授权状态。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "public" / "login.config.js"
DEFAULT_OUT = ROOT / "public" / "fonts" / "zixiaohun-canglang-xingkai.woff2"


def read_vision_text() -> str:
    """从 login.config.js 里读 vision 文案（失败则回落到默认 7 个字）。"""
    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8")
    except OSError:
        return "让工作简单起来"
    match = re.search(r'vision\s*:\s*"([^"]*)"', raw)
    return match.group(1) if match and match.group(1) else "让工作简单起来"


def human(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1048576:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def main() -> int:
    parser = argparse.ArgumentParser(description="登录页页脚艺术字字体子集化")
    parser.add_argument("font", help="原始字体文件（.ttf / .otf）")
    parser.add_argument("--text", default="", help="要保留的字符；默认读 login.config.js 的 vision")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出文件（.woff2）")
    args = parser.parse_args()

    source = Path(args.font)
    if not source.is_file():
        print(f"找不到字体文件：{source}")
        return 2

    try:
        from fontTools import subset  # noqa: PLC0415
    except ImportError:
        print("需要先安装依赖：./.venv/Scripts/python.exe -m pip install fonttools brotli")
        return 2

    text = args.text or read_vision_text()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    options = subset.Options()
    options.flavor = "woff2"
    options.with_zopfli = True
    # 只保留渲染这行字所必需的表，尽可能小
    options.drop_tables += ["DSIG"]
    options.layout_features = []
    options.notdef_outline = True
    options.recalc_bounds = True
    options.name_IDs = ["*"]
    options.name_legacy = True

    font = subset.load_font(str(source), options)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    subset.save_font(font, str(out_path), options)
    font.close()

    before, after = source.stat().st_size, out_path.stat().st_size
    print(json.dumps({
        "源字体": str(source),
        "源大小": human(before),
        "保留字符": text,
        "字符数": len(text),
        "输出": str(out_path.relative_to(ROOT)) if out_path.is_relative_to(ROOT) else str(out_path),
        "输出大小": human(after),
        "压缩比": f"{before / max(after, 1):.0f}x",
    }, ensure_ascii=False, indent=2))
    print("\n完成。刷新登录页（Ctrl + F5）即可看到效果。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
