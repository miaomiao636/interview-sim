"""从仓库级 Skill 稳定启动本地工作台。"""
from __future__ import annotations

import os
import json
import sys
from pathlib import Path


def find_project() -> Path:
    skill_dir = Path(__file__).resolve().parents[1]
    marker = skill_dir / "interview-sim-location.json"
    try:
        root = Path(json.loads(marker.read_text(encoding="utf-8"))["project_root"]) if marker.exists() else skill_dir.parents[2]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("Skill 的完整项目位置无效，请从完整仓库重新安装 Skill。") from exc
    if not root.is_absolute() or not all((root / name).exists() for name in ("pyproject.toml", "backend/cli.py", "frontend/index.html")):
        raise SystemExit("找不到完整项目，请检查项目是否已移动，并重新安装 Skill。")
    return root


def launch() -> int:
    root = find_project()
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.is_file() and os.path.abspath(sys.executable) != os.path.abspath(python):
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    if sys.version_info < (3, 11):
        raise SystemExit("需要 Python 3.11 或更新版本；建议 Python 3.12。请按完整项目 docs/QUICKSTART.md 重建 .venv。")
    os.chdir(root)
    sys.path.insert(0, str(root))
    try:
        from backend.cli import main
    except ImportError as exc:
        raise SystemExit("项目依赖未安装。请按完整项目 docs/QUICKSTART.md 安装锁定依赖与本地应用。") from exc
    sys.argv = ["interview-sim", "web", *sys.argv[1:]]
    return main()


if __name__ == "__main__":
    raise SystemExit(launch())
