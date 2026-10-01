"""Package the local analysis tool (app.py + web UI + helper scripts) into a
distributable zip for GitHub Releases.

The package includes an entity-to-image URL catalog, not game image bytes.
start.cmd can cache images locally from SKeyDB on first run.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "dist" / "morimens-summon-tool.zip"

# Everything a user needs to run the local "导出抽卡记录" / "数据分析" tool.
# Deliberately leaves out fetch_all_history.py and friends (not distributed).
INCLUDE_FILES = [
    "app.py",
    "history_store.py",
    "summon_cli.py",
    "sync_skeydb_assets.py",
    "start.cmd",
    "requirements.txt",
    "README.md",
]
INCLUDE_DIRS = [
    "web",
    "resources",
    "templates",
    "docs",
]
EXCLUDE_NAMES = {"__pycache__", ".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc"}


def add_file(zf: zipfile.ZipFile, path: Path) -> None:
    if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
        return
    if path.is_relative_to(ROOT / "web" / "assets") or path.is_relative_to(ROOT / "web" / "downloads"):
        return
    zf.write(path, path.relative_to(ROOT))


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in INCLUDE_FILES:
            path = ROOT / name
            if path.is_file():
                add_file(zf, path)
        for name in INCLUDE_DIRS:
            base = ROOT / name
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if path.is_file():
                    add_file(zf, path)
    print("RELEASE_READY", OUTPUT, f"{OUTPUT.stat().st_size / 1024:.1f} KiB")


if __name__ == "__main__":
    main()
