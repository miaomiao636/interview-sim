"""Install a host-neutral Skill; Windows does not require symlink privileges."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT_ROOT / ".agents" / "skills" / "interview-sim"
DESTINATION = Path.home() / ".agents" / "skills" / "interview-sim"


def install_skill(destination: Path, mode: str = "auto") -> int:
    destination = destination.expanduser().absolute()
    if not SOURCE.is_dir():
        raise SystemExit(f"Skill 源目录不存在：{SOURCE}")
    if destination.is_symlink() and destination.resolve() == SOURCE.resolve():
        print(f"Skill 已安装：{destination}")
        return 0
    if destination.exists() or destination.is_symlink():
        raise SystemExit(f"拒绝覆盖已有 Skill：{destination}。升级复制版前请将旧目录改名备份。")
    if mode == "auto":
        mode = "copy" if platform.system() == "Windows" else "symlink"
    if mode not in {"copy", "symlink"}:
        raise ValueError("Unsupported installation mode")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if mode == "copy":
        shutil.copytree(SOURCE, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "interview-sim-location.json"))
        (destination / "interview-sim-location.json").write_text(
            json.dumps({"project_root": str(PROJECT_ROOT)}, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    else:
        try:
            destination.symlink_to(SOURCE, target_is_directory=True)
        except OSError as exc:
            raise SystemExit("无法创建符号链接，请使用 --mode copy；无需管理员权限。") from exc
    print(f"Skill 已安装（{mode}）：{destination}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DESTINATION, help="宿主识别的 Skill 完整目标目录（含 interview-sim）")
    parser.add_argument("--mode", choices=["auto", "copy", "symlink"], default="auto")
    args = parser.parse_args()
    return install_skill(args.dest, args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
