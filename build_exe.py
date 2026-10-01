"""Build a portable Windows archive without account data or game artwork."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "MorimensSummon"


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Windows EXE 必须在 Windows 上构建")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "MorimensSummon", "--collect-all", "pydivert",
        "--hidden-import", "scapy.all", "--collect-submodules", "scapy",
        "desktop.py",
    ], cwd=ROOT, check=True)
    for folder in ("web", "resources", "protocol"):
        shutil.copytree(ROOT / folder, DIST / folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "assets") if folder == "web"
                        else shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"EXE_READY {DIST / 'MorimensSummon.exe'}", flush=True)


if __name__ == "__main__":
    main()
