"""从仓库级 Skill 稳定启动本地工作台。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from backend.cli import main
except ImportError as exc:
    raise SystemExit(
        f"项目依赖未安装。请先运行：python3 -m pip install -e {PROJECT_ROOT}"
    ) from exc


if __name__ == "__main__":
    sys.argv = ["interview-sim", "web", *sys.argv[1:]]
    raise SystemExit(main())
