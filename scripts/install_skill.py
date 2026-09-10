"""将仓库内 Skill 以符号链接安装到当前用户的 Codex Skill 目录。"""
from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT_ROOT / ".agents" / "skills" / "interview-sim"
DESTINATION = Path.home() / ".agents" / "skills" / "interview-sim"


def main() -> int:
    if not SOURCE.is_dir():
        raise SystemExit(f"Skill 源目录不存在：{SOURCE}")
    if DESTINATION.is_symlink() and DESTINATION.resolve() == SOURCE.resolve():
        print(f"Skill 已安装：{DESTINATION}")
        return 0
    if DESTINATION.exists() or DESTINATION.is_symlink():
        raise SystemExit(f"拒绝覆盖已有 Skill：{DESTINATION}")
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.symlink_to(SOURCE, target_is_directory=True)
    print(f"Skill 已安装：{DESTINATION} -> {SOURCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
