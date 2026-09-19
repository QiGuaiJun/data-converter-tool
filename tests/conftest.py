"""让 tests/ 下的用例仍能 `import server`。

2026-09 迁移前，用例与 server.py 同处项目根，靠 pytest 把 rootdir 放进 sys.path
才能直接 import；收进 tests/ 子目录后这层隐式行为消失，必须显式补上项目根目录。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
